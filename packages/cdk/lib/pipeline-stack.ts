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
import * as lambda from 'aws-cdk-lib/aws-lambda';
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


    // Grant Bedrock access
    fargateTaskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-english-v3`,
        `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-multilingual-v3`,
      ],
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
        BEDROCK_MODEL_ID: 'cohere.embed-english-v3',
        ARTIFACTS_BUCKET: databaseStack.artifactsBucket.bucketName,
        DUCKDB_KEY: 'snapshots/fdnix.duckdb',
        OUTPUT_PATH: '/out/fdnix.duckdb',
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

    // IAM role for the Lambda that publishes a new layer version
    const publishLayerRole = new iam.Role(this, 'PublishLayerRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Allow this function to publish new versions of the DuckDB layer
    publishLayerRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'lambda:PublishLayerVersion',
        'lambda:GetLayerVersion',
        'lambda:ListLayerVersions',
      ],
      resources: [dbLayerUnversionedArn, databaseStack.databaseLayer.layerVersionArn],
    }));

    // Allow reading the DuckDB artifact from S3
    publishLayerRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject', 's3:ListBucket'],
      resources: [
        databaseStack.artifactsBucket.bucketArn,
        databaseStack.artifactsBucket.arnForObjects('*'),
      ],
    }));

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
      role: publishLayerRole,
    });

    const publishLayerTask = new stepfunctionsTasks.LambdaInvoke(this, 'PublishLayerTask', {
      lambdaFunction: publishLayerFunction,
      payload: stepfunctions.TaskInput.fromObject({
        bucket_name: databaseStack.artifactsBucket.bucketName,
        key: 'snapshots/fdnix.duckdb',
        layer_arn: dbLayerUnversionedArn,
      }),
    });

    // Define the simplified state machine (single indexer task then publish layer)
    const definition = indexerTask.next(publishLayerTask);

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
