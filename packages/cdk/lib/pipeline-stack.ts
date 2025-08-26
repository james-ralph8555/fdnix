import { Stack, StackProps, Duration, RemovalPolicy } from 'aws-cdk-lib';
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
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as path from 'path';
import { FdnixDatabaseStack } from './database-stack';
import { DockerBuildConstruct } from './docker-build-construct';

export interface FdnixPipelineStackProps extends StackProps {
  databaseStack: FdnixDatabaseStack;
}

export class FdnixPipelineStack extends Stack {
  public readonly cluster: ecs.Cluster;
  public readonly metadataRepository: ecr.Repository;
  public readonly embeddingRepository: ecr.Repository;
  public readonly metadataTaskDefinition: ecs.FargateTaskDefinition;
  public readonly embeddingTaskDefinition: ecs.FargateTaskDefinition;
  public readonly pipelineStateMachine: stepfunctions.StateMachine;
  public readonly metadataDockerBuild: DockerBuildConstruct;
  public readonly embeddingDockerBuild: DockerBuildConstruct;

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

    // ECR Repositories
    this.metadataRepository = new ecr.Repository(this, 'MetadataRepository', {
      lifecycleRules: [{
        maxImageCount: 10,
      }],
      removalPolicy: RemovalPolicy.RETAIN,
    });

    this.embeddingRepository = new ecr.Repository(this, 'EmbeddingRepository', {
      lifecycleRules: [{
        maxImageCount: 10,
      }],
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Docker build constructs for automated container building
    const containersPath = path.join(__dirname, '../../containers');
    
    this.metadataDockerBuild = new DockerBuildConstruct(this, 'MetadataDockerBuild', {
      repository: this.metadataRepository,
      dockerfilePath: path.join(containersPath, 'metadata-generator/Dockerfile'),
      contextPath: path.join(containersPath, 'metadata-generator'),
      imageName: 'metadata-generator',
    });

    this.embeddingDockerBuild = new DockerBuildConstruct(this, 'EmbeddingDockerBuild', {
      repository: this.embeddingRepository,
      dockerfilePath: path.join(containersPath, 'embedding-generator/Dockerfile'),
      contextPath: path.join(containersPath, 'embedding-generator'),
      imageName: 'embedding-generator',
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


    // Grant Bedrock access
    fargateTaskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-english-v3`,
        `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-multilingual-v3`,
      ],
    }));

    // Grant Lambda layer publishing permissions
    fargateTaskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'lambda:PublishLayerVersion',
        'lambda:GetLayerVersion',
        'lambda:ListLayerVersions',
      ],
      resources: [
        `${databaseStack.databaseLayer.layerVersionArn.split(':').slice(0, -1).join(':')}:*`,
      ],
    }));


    // CloudWatch Log Groups
    const metadataLogGroup = new logs.LogGroup(this, 'MetadataLogGroup', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const embeddingLogGroup = new logs.LogGroup(this, 'EmbeddingLogGroup', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Fargate Task Definitions
    this.metadataTaskDefinition = new ecs.FargateTaskDefinition(this, 'MetadataTaskDefinition', {
      cpu: 1024,
      memoryLimitMiB: 3072,
      executionRole: fargateExecutionRole,
      taskRole: fargateTaskRole,
    });

    const metadataContainer = this.metadataTaskDefinition.addContainer('MetadataContainer', {
      image: ecs.ContainerImage.fromDockerImageAsset(this.metadataDockerBuild.dockerImageAsset),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'metadata',
        logGroup: metadataLogGroup,
      }),
      environment: {
        AWS_REGION: this.region,
        ARTIFACTS_BUCKET: databaseStack.artifactsBucket.bucketName,
        DUCKDB_KEY: 'snapshots/fdnix.duckdb',
      },
    });

    this.embeddingTaskDefinition = new ecs.FargateTaskDefinition(this, 'EmbeddingTaskDefinition', {
      cpu: 2048,
      memoryLimitMiB: 6144,
      executionRole: fargateExecutionRole,
      taskRole: fargateTaskRole,
    });

    const embeddingContainer = this.embeddingTaskDefinition.addContainer('EmbeddingContainer', {
      image: ecs.ContainerImage.fromDockerImageAsset(this.embeddingDockerBuild.dockerImageAsset),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'embedding',
        logGroup: embeddingLogGroup,
      }),
      environment: {
        AWS_REGION: this.region,
        BEDROCK_MODEL_ID: 'cohere.embed-english-v3',
        ARTIFACTS_BUCKET: databaseStack.artifactsBucket.bucketName,
        DUCKDB_KEY: 'snapshots/fdnix.duckdb',
      },
    });

    // Step Functions State Machine for pipeline orchestration
    const metadataTask = new stepfunctionsTasks.EcsRunTask(this, 'MetadataTask', {
      integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
      cluster: this.cluster,
      taskDefinition: this.metadataTaskDefinition,
      launchTarget: new stepfunctionsTasks.EcsFargateLaunchTarget(),
      assignPublicIp: true,
    });

    const embeddingTask = new stepfunctionsTasks.EcsRunTask(this, 'EmbeddingTask', {
      integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
      cluster: this.cluster,
      taskDefinition: this.embeddingTaskDefinition,
      launchTarget: new stepfunctionsTasks.EcsFargateLaunchTarget(),
      assignPublicIp: true,
    });

    // Lambda function to publish layer from S3 artifact
    const publishLayerFunction = new lambda.Function(this, 'PublishLayerFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromInline(`
import boto3
import json

def handler(event, context):
    s3_client = boto3.client('s3')
    lambda_client = boto3.client('lambda')
    
    bucket_name = event['bucket_name']
    key = event['key']
    layer_arn = event['layer_arn']
    
    try:
        # Publish new layer version
        response = lambda_client.publish_layer_version(
            LayerName=layer_arn,
            Description='DuckDB database file for fdnix search API',
            Content={
                'S3Bucket': bucket_name,
                'S3Key': key
            },
            CompatibleRuntimes=['provided.al2023'],
            CompatibleArchitectures=['arm64']
        )
        
        return {
            'statusCode': 200,
            'layerVersionArn': response['LayerVersionArn'],
            'version': response['Version']
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'error': str(e)
        }
      `),
      timeout: Duration.minutes(5),
      role: fargateTaskRole,
    });

    const publishLayerTask = new stepfunctionsTasks.LambdaInvoke(this, 'PublishLayerTask', {
      lambdaFunction: publishLayerFunction,
      payload: stepfunctions.TaskInput.fromObject({
        bucket_name: databaseStack.artifactsBucket.bucketName,
        key: 'snapshots/fdnix.duckdb',
        layer_arn: `${databaseStack.databaseLayer.layerVersionArn.split(':').slice(0, -1).join(':')}`,
      }),
    });

    // Wait state between tasks
    const waitForProcessing = new stepfunctions.Wait(this, 'WaitForProcessing', {
      time: stepfunctions.WaitTime.duration(Duration.minutes(5)),
    });

    // Define the state machine
    const definition = metadataTask
      .next(waitForProcessing)
      .next(embeddingTask)
      .next(publishLayerTask);

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
        this.metadataTaskDefinition.taskDefinitionArn,
        this.embeddingTaskDefinition.taskDefinitionArn,
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
