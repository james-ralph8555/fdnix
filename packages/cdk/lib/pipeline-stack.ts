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

    // Create VPC for Fargate tasks with $0/hr configuration
    // 
    // This configuration eliminates hourly charges by:
    // - Using public subnets only (no NAT Gateway: saves ~$45/month)  
    // - Gateway VPC endpoints for S3/DynamoDB (no hourly fees, no data transfer costs)
    // - Public IP assignment for internet access to other AWS services
    // - Security group restricting egress to HTTPS only for security
    const vpc = new ec2.Vpc(this, 'PipelineVpc', {
      maxAzs: 2,
      natGateways: 0, // $0/hr: No NAT Gateway
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        // No private subnets to avoid NAT Gateway requirement
      ],
    });

    // Add Gateway VPC Endpoints for $0/hr AWS service access
    // S3 Gateway endpoint - no hourly charges, no data transfer costs
    vpc.addGatewayEndpoint('S3GatewayEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    // DynamoDB Gateway endpoint - no hourly charges, no data transfer costs  
    vpc.addGatewayEndpoint('DynamoDbGatewayEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
    });

    // Security Group for Fargate tasks - HTTPS egress only
    const fargateSecurityGroup = new ec2.SecurityGroup(this, 'FargateSecurityGroup', {
      vpc,
      description: 'Security group for Fargate tasks with HTTPS egress only',
      allowAllOutbound: false, // Explicitly deny all outbound by default
    });

    // Allow HTTPS (443) outbound traffic only - for AWS APIs, Bedrock, etc.
    fargateSecurityGroup.addEgressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      'HTTPS outbound for AWS services'
    );

    // Allow HTTP (80) for package downloads (Nix, pip, etc.) - can be removed if not needed
    fargateSecurityGroup.addEgressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(80),
      'HTTP outbound for package downloads'
    );

    // ECS Cluster
    this.cluster = new ecs.Cluster(this, 'ProcessingCluster', {
      vpc,
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
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
        'bedrock:InvokeModel', // Standard API for individual embedding requests
        'bedrock:ListFoundationModels',
      ],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`,
        '*', // ListFoundationModels requires wildcard
      ],
    }));

    // No longer need batch role passing since we use standard API

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
      memoryLimitMiB: 16384,
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
        BEDROCK_MAX_RPM: '600', // Account service quota
        BEDROCK_MAX_TOKENS_PER_MINUTE: '300000', // Account service quota
        PROCESSING_BATCH_SIZE: '100', // Smaller batches for better rate limiting
        // S3 configuration
        ARTIFACTS_BUCKET: databaseStack.artifactsBucket.bucketName,
        // Dual database configuration
        LANCEDB_DATA_KEY: 'snapshots/fdnix-data.lancedb',        // Main database with all metadata
        LANCEDB_MINIFIED_KEY: 'snapshots/fdnix.lancedb',        // Minified database for Lambda layer
        OUTPUT_PATH: '/out/fdnix-data.lancedb',                 // Main database output path
        OUTPUT_MINIFIED_PATH: '/out/fdnix.lancedb',             // Minified database output path
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
      launchTarget: new stepfunctionsTasks.EcsFargateLaunchTarget({
        platformVersion: ecs.FargatePlatformVersion.LATEST,
      }),
      assignPublicIp: true, // Required for public subnet access to AWS services
      securityGroups: [fargateSecurityGroup], // Use our HTTPS-only security group
      subnets: { subnetType: ec2.SubnetType.PUBLIC }, // Explicitly use public subnets
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
