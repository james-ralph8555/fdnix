import { Stack, StackProps, Duration, RemovalPolicy, Arn, ArnFormat, Fn } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as stepfunctions from 'aws-cdk-lib/aws-stepfunctions';
import * as stepfunctionsTasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as path from 'path';
import { FdnixDatabaseStack } from './database-stack';
import { DockerBuildConstruct } from './docker-build-construct';

export interface FdnixPipelineStackProps extends StackProps {
  databaseStack: FdnixDatabaseStack;
}

export class FdnixPipelineStack extends Stack {
  public readonly cluster: ecs.Cluster;
  public readonly nixpkgsIndexerRepository: ecr.Repository;
  public readonly nixpkgsIndexerTaskDefinition: ecs.FargateTaskDefinition;
  public readonly pipelineStateMachine: stepfunctions.StateMachine;
  public readonly nixpkgsIndexerDockerBuild: DockerBuildConstruct;

  constructor(scope: Construct, id: string, props: FdnixPipelineStackProps) {
    super(scope, id, props);

    const { databaseStack } = props;

    // Create VPC for Fargate tasks
    const vpc = new ec2.Vpc(this, 'PipelineVpc', {
      maxAzs: 2,
      natGateways: 1,
    });

    // ECS Cluster
    this.cluster = new ecs.Cluster(this, 'ProcessingCluster', {
      vpc,
      containerInsights: true,
    });

    // ECR Repository for nixpkgs-indexer
    this.nixpkgsIndexerRepository = new ecr.Repository(this, 'NixpkgsIndexerRepository', {
      lifecycleRules: [{
        maxImageCount: 10,
      }],
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Docker build construct for nixpkgs-indexer
    const containersPath = path.join(__dirname, '../../containers');
    
    this.nixpkgsIndexerDockerBuild = new DockerBuildConstruct(this, 'NixpkgsIndexerDockerBuild', {
      repository: this.nixpkgsIndexerRepository,
      dockerfilePath: path.join(containersPath, 'nixpkgs-indexer/Dockerfile'),
      contextPath: path.join(containersPath, 'nixpkgs-indexer'),
      imageName: 'nixpkgs-indexer',
    });

    // IAM roles for Fargate tasks
    const fargateExecutionRole = new iam.Role(this, 'FargateExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });

    const fargateTaskRole = new iam.Role(this, 'FargateTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });

    // Grant database access to task role
    databaseStack.artifactsBucket.grantReadWrite(fargateTaskRole);


    // No longer need Bedrock permissions as we're using Google Gemini API
    // Google Gemini API key will be provided via secrets manager or environment variable

    // Grant Lambda layer publishing permissions using safe ARN handling
    const dbLayerParts = Arn.split(databaseStack.databaseLayer.layerVersionArn, ArnFormat.COLON_RESOURCE_NAME);
    const dbLayerBaseName = Fn.select(0, Fn.split(':', dbLayerParts.resourceName!));
    const dbLayerUnversionedArn = Arn.format(
      {
        service: 'lambda',
        resource: 'layer',
        region: dbLayerParts.region,
        account: dbLayerParts.account,
        resourceName: dbLayerBaseName,
        arnFormat: ArnFormat.COLON_RESOURCE_NAME,
      },
      this,
    );

    fargateTaskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'lambda:PublishLayerVersion',
        'lambda:GetLayerVersion',
        'lambda:ListLayerVersions',
      ],
      resources: [
        // Unversioned for publish/list, versioned for get
        dbLayerUnversionedArn,
        databaseStack.databaseLayer.layerVersionArn,
      ],
    }));


    // CloudWatch Log Group
    const indexerLogGroup = new logs.LogGroup(this, 'NixpkgsIndexerLogGroup', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Fargate Task Definition for nixpkgs-indexer
    this.nixpkgsIndexerTaskDefinition = new ecs.FargateTaskDefinition(this, 'NixpkgsIndexerTaskDefinition', {
      cpu: 2048,
      memoryLimitMiB: 6144,
      executionRole: fargateExecutionRole,
      taskRole: fargateTaskRole,
    });

    const indexerContainer = this.nixpkgsIndexerTaskDefinition.addContainer('NixpkgsIndexerContainer', {
      image: ecs.ContainerImage.fromDockerImageAsset(this.nixpkgsIndexerDockerBuild.dockerImageAsset),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'nixpkgs-indexer',
        logGroup: indexerLogGroup,
      }),
      environment: {
        AWS_REGION: this.region,
        PROCESSING_MODE: 'both',
        GEMINI_MODEL_ID: 'gemini-embedding-001',
        GEMINI_OUTPUT_DIMENSIONS: '256',
        GEMINI_TASK_TYPE: 'SEMANTIC_SIMILARITY',
        ARTIFACTS_BUCKET: databaseStack.artifactsBucket.bucketName,
        DUCKDB_KEY: 'snapshots/fdnix.duckdb',
        OUTPUT_PATH: '/out/fdnix.duckdb',
        // Layer publish configuration
        PUBLISH_LAYER: 'true',
        LAYER_ARN: dbLayerUnversionedArn,
      },
    });

    // Step Functions State Machine for pipeline orchestration
    const indexerTask = new stepfunctionsTasks.EcsRunTask(this, 'NixpkgsIndexerTask', {
      integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
      cluster: this.cluster,
      taskDefinition: this.nixpkgsIndexerTaskDefinition,
      launchTarget: new stepfunctionsTasks.EcsFargateLaunchTarget(),
      assignPublicIp: true,
    });

    // Define the simplified state machine (single indexer task; layer publish moved into container)
    const definition = indexerTask;

    this.pipelineStateMachine = new stepfunctions.StateMachine(this, 'PipelineStateMachine', {
      definition,
      timeout: Duration.hours(6),
    });

    // EventBridge rule for daily execution
    const dailyRule = new events.Rule(this, 'DailyPipelineRule', {
      description: 'Triggers the fdnix data processing pipeline daily',
      schedule: events.Schedule.cron({ 
        hour: '2', 
        minute: '0' 
      }),
    });

    // Add the state machine as a target
    dailyRule.addTarget(new targets.SfnStateMachine(this.pipelineStateMachine));

    // Grant Step Functions permissions to run ECS tasks
    this.pipelineStateMachine.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'ecs:RunTask',
        'ecs:StopTask',
        'ecs:DescribeTasks',
      ],
      resources: [
        this.nixpkgsIndexerTaskDefinition.taskDefinitionArn,
        `arn:aws:ecs:${this.region}:${this.account}:task/${this.cluster.clusterName}/*`,
      ],
    }));

    this.pipelineStateMachine.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'iam:PassRole',
      ],
      resources: [
        fargateExecutionRole.roleArn,
        fargateTaskRole.roleArn,
      ],
    }));
  }
}
