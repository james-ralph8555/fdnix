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
  public readonly bedrockBatchRole: iam.Role;

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

    // Create IAM role for Bedrock batch inference
    this.bedrockBatchRole = new iam.Role(this, 'BedrockBatchRole', {
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('bedrock.amazonaws.com'),
        new iam.ArnPrincipal('arn:aws:iam::127659835464:user/Administrator'),
      ),
      inlinePolicies: {
        BedrockBatchPolicy: new iam.PolicyDocument({
          statements: [
            // S3 permissions for batch inference input/output
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                's3:GetObject',
                's3:PutObject',
                's3:DeleteObject',
                's3:ListBucket',
              ],
              resources: [
                databaseStack.artifactsBucket.bucketArn,
                `${databaseStack.artifactsBucket.bucketArn}/*`,
              ],
            }),
            // Bedrock model invocation permissions for batch inference
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock:InvokeModel',
                'bedrock:CreateModelInvocationJob',
                'bedrock:GetModelInvocationJob',
                'bedrock:StopModelInvocationJob',
              ],
              resources: [
                `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`,
                `arn:aws:bedrock:${this.region}:${this.account}:model-invocation-job/*`,
              ],
              conditions: {
                StringEquals: {
                  'bedrock:sourceBucket': databaseStack.artifactsBucket.bucketName,
                },
              },
            }),
            // Allow listing available foundation models
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['bedrock:ListFoundationModels'],
              resources: ['*'],
            }),
          ],
        }),
      },
    });

    // Grant Bedrock permissions to the Fargate task role
    fargateTaskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock:CreateModelInvocationJob',
        'bedrock:GetModelInvocationJob',
        'bedrock:StopModelInvocationJob',
        'bedrock:ListModelInvocationJobs',
        'bedrock:ListFoundationModels',
      ],
      resources: ['*'], // Bedrock batch jobs require wildcard permissions
    }));

    // Grant permission to pass the Bedrock batch role
    fargateTaskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'iam:PassRole',
      ],
      resources: [this.bedrockBatchRole.roleArn],
    }));

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
        // Bedrock configuration
        BEDROCK_MODEL_ID: 'amazon.titan-embed-text-v2:0',
        BEDROCK_OUTPUT_DIMENSIONS: '256',
        BEDROCK_BATCH_SIZE: '50000',
        BEDROCK_ROLE_ARN: this.bedrockBatchRole.roleArn,
        // S3 configuration
        ARTIFACTS_BUCKET: databaseStack.artifactsBucket.bucketName,
        BEDROCK_INPUT_BUCKET: databaseStack.artifactsBucket.bucketName,
        BEDROCK_OUTPUT_BUCKET: databaseStack.artifactsBucket.bucketName,
        // Dual database configuration
        DUCKDB_DATA_KEY: 'snapshots/fdnix-data.duckdb',        // Main database with all metadata
        DUCKDB_MINIFIED_KEY: 'snapshots/fdnix.duckdb',        // Minified database for Lambda layer
        OUTPUT_PATH: '/out/fdnix-data.duckdb',                 // Main database output path
        OUTPUT_MINIFIED_PATH: '/out/fdnix.duckdb',             // Minified database output path
        // Layer publish configuration
        PUBLISH_LAYER: 'true',
        LAYER_ARN: dbLayerUnversionedArn,
        // Batch job polling configuration
        BEDROCK_POLL_INTERVAL: '60',                           // Poll every 60 seconds
        BEDROCK_MAX_WAIT_TIME: '7200',                         // Max 2 hours per batch job
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
