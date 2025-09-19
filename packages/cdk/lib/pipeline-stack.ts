import { Stack, StackProps, Duration, RemovalPolicy, Arn, ArnFormat, Fn, Names } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as stepfunctions from 'aws-cdk-lib/aws-stepfunctions';
import * as path from 'path';
import { FdnixDatabaseStack } from './database-stack';
import { DockerBuildConstruct } from './docker-build-construct';
import { createPipelineStateMachineDefinition } from './pipeline-state-machine-definition';

export interface FdnixPipelineStackProps extends StackProps {
  databaseStack: FdnixDatabaseStack;
}

export class FdnixPipelineStack extends Stack {
  public readonly cluster: ecs.Cluster;
  // Stage 1: Evaluator
  public readonly nixpkgsEvaluatorRepository: ecr.Repository;
  public readonly nixpkgsEvaluatorTaskDefinition: ecs.FargateTaskDefinition;
  public readonly nixpkgsEvaluatorDockerBuild: DockerBuildConstruct;
  // Stage 2: Processor
  public readonly nixpkgsProcessorRepository: ecr.Repository;
  public readonly nixpkgsProcessorTaskDefinition: ecs.FargateTaskDefinition;
  public readonly nixpkgsProcessorDockerBuild: DockerBuildConstruct;
  public readonly bedrockBatchRole: iam.Role;
  // Step Functions
  public readonly pipelineStateMachine: stepfunctions.StateMachine;

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
      containerInsights: false,
    });

    // ECR Repositories for both stages
    this.nixpkgsEvaluatorRepository = new ecr.Repository(this, 'NixpkgsEvaluatorRepository', {
      repositoryName: `fdnixpipelinestack-nixpkgs-evaluator-${Names.uniqueId(this).toLowerCase().slice(-8)}`,
      lifecycleRules: [{
        maxImageCount: 10,
      }],
      removalPolicy: RemovalPolicy.DESTROY,
    });

    this.nixpkgsProcessorRepository = new ecr.Repository(this, 'NixpkgsProcessorRepository', {
      repositoryName: `fdnixpipelinestack-nixpkgs-processor-${Names.uniqueId(this).toLowerCase().slice(-8)}`,
      lifecycleRules: [{
        maxImageCount: 10,
      }],
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // Docker build constructs for both stages
    const containersPath = path.join(__dirname, '../../containers');
    
    this.nixpkgsEvaluatorDockerBuild = new DockerBuildConstruct(this, 'NixpkgsEvaluatorDockerBuild', {
      repository: this.nixpkgsEvaluatorRepository,
      dockerfilePath: path.join(containersPath, 'nixpkgs-evaluator/Dockerfile'),
      contextPath: path.join(containersPath, 'nixpkgs-evaluator'),
      imageName: 'nixpkgs-evaluator',
    });

    this.nixpkgsProcessorDockerBuild = new DockerBuildConstruct(this, 'NixpkgsProcessorDockerBuild', {
      repository: this.nixpkgsProcessorRepository,
      dockerfilePath: path.join(containersPath, 'nixpkgs-processor/Dockerfile'),
      contextPath: path.join(containersPath, 'nixpkgs-processor'),
      imageName: 'nixpkgs-processor',
    });

    // IAM roles for Fargate tasks - shared execution role
    const fargateExecutionRole = new iam.Role(this, 'FargateExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });

    // Stage 1: Evaluator task role (minimal permissions)
    const evaluatorTaskRole = new iam.Role(this, 'EvaluatorTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });

    // Stage 2: Processor task role (full permissions)
    const processorTaskRole = new iam.Role(this, 'ProcessorTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });

    // Grant S3 access - evaluator needs read/write for JSONL, processor needs full access
    databaseStack.artifactsBucket.grantReadWrite(evaluatorTaskRole);
    databaseStack.artifactsBucket.grantReadWrite(processorTaskRole);
    
    // Grant processor write access to processed files bucket for dependency graphs, stats, etc.
    databaseStack.processedFilesBucket.grantReadWrite(processorTaskRole);


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

    // Grant Bedrock permissions only to the processor task role
    processorTaskRole.addToPolicy(new iam.PolicyStatement({
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

    // Grant Lambda layer publishing permissions only to processor
    processorTaskRole.addToPolicy(new iam.PolicyStatement({
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


    // CloudWatch Log Groups for both stages with unique names
    const evaluatorLogGroup = new logs.LogGroup(this, 'NixpkgsEvaluatorLogGroup', {
      logGroupName: `/aws/ecs/fdnixpipelinestack-nixpkgs-evaluator-${Names.uniqueId(this).toLowerCase().slice(-8)}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });


    const processorLogGroup = new logs.LogGroup(this, 'NixpkgsProcessorLogGroup', {
      logGroupName: `/aws/ecs/fdnixpipelinestack-nixpkgs-processor-${Names.uniqueId(this).toLowerCase().slice(-8)}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // Stage 1: Fargate Task Definition for nixpkgs-evaluator (4vCPU, 28GB RAM, 40GB storage)
    this.nixpkgsEvaluatorTaskDefinition = new ecs.FargateTaskDefinition(this, 'NixpkgsEvaluatorTaskDefinition', {
      cpu: 4096,      // 4 vCPU - for nix-eval-jobs
      memoryLimitMiB: 28672,  // 28GB RAM - for nix-eval-jobs
      ephemeralStorageGiB: 40, // 40GB ephemeral storage for large nix evaluations
      executionRole: fargateExecutionRole,
      taskRole: evaluatorTaskRole,
    });

    const evaluatorContainer = this.nixpkgsEvaluatorTaskDefinition.addContainer('NixpkgsEvaluatorContainer', {
      image: ecs.ContainerImage.fromDockerImageAsset(this.nixpkgsEvaluatorDockerBuild.dockerImageAsset),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'nixpkgs-evaluator',
        logGroup: evaluatorLogGroup,
      }),
      environment: {
        AWS_REGION: this.region,
        ARTIFACTS_BUCKET: databaseStack.artifactsBucket.bucketName,
        // Output will be picked up by Step Functions and passed to Stage 2
        JSONL_OUTPUT_KEY: 'evaluations/nixpkgs-raw.jsonl', // Will be overridden with timestamp
      },
    });


    // Stage 2: Fargate Task Definition for nixpkgs-processor (4vCPU, 16GB RAM, 40GB storage)
    this.nixpkgsProcessorTaskDefinition = new ecs.FargateTaskDefinition(this, 'NixpkgsProcessorTaskDefinition', {
      cpu: 4096,      // 4 vCPU - increased for memory-intensive processing
      memoryLimitMiB: 16384,  // 16GB RAM - increased to handle large dataset processing and embeddings
      ephemeralStorageGiB: 40, // 40GB ephemeral storage for large dataset processing
      executionRole: fargateExecutionRole,
      taskRole: processorTaskRole,
    });

    const processorContainer = this.nixpkgsProcessorTaskDefinition.addContainer('NixpkgsProcessorContainer', {
      image: ecs.ContainerImage.fromDockerImageAsset(this.nixpkgsProcessorDockerBuild.dockerImageAsset),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'nixpkgs-processor',
        logGroup: processorLogGroup,
      }),
      environment: {
        AWS_REGION: this.region,
        PROCESSING_MODE: 'both',
        // Disable embedding generation in the pipeline by default
        ENABLE_EMBEDDINGS: 'false',
        // Bedrock configuration
        BEDROCK_MODEL_ID: 'amazon.titan-embed-text-v2:0',
        BEDROCK_OUTPUT_DIMENSIONS: '256',
        BEDROCK_MAX_RPM: '600', // Account service quota
        BEDROCK_MAX_TOKENS_PER_MINUTE: '300000', // Account service quota
        PROCESSING_BATCH_SIZE: '100', // Smaller batches for better rate limiting
        // S3 configuration
        ARTIFACTS_BUCKET: databaseStack.artifactsBucket.bucketName,
        PROCESSED_FILES_BUCKET: databaseStack.processedFilesBucket.bucketName,
        // Input from Stage 1 - will be set dynamically by Step Functions
        // JSONL_INPUT_KEY: 'evaluations/nixpkgs-raw.jsonl', // Set by Step Functions
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

    // Step Functions State Machine for two-stage pipeline orchestration
    // Create a security group for Step Functions managed Fargate tasks
    const stepFunctionsSecurityGroup = new ec2.SecurityGroup(this, 'StepFunctionsSecurityGroup', {
      vpc,
      description: 'Security group for Step Functions managed Fargate tasks with HTTPS egress only',
      allowAllOutbound: false,
    });

    // Allow HTTPS (443) outbound traffic only - for AWS APIs, Bedrock, etc.
    stepFunctionsSecurityGroup.addEgressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      'HTTPS outbound for AWS services'
    );

    // Allow HTTP (80) for package downloads (Nix, pip, etc.)
    stepFunctionsSecurityGroup.addEgressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(80),
      'HTTP outbound for package downloads'
    );

    // Create the state machine definition using the extracted function
    const definition = createPipelineStateMachineDefinition(this, {
      cluster: this.cluster,
      evaluatorTaskDefinition: this.nixpkgsEvaluatorTaskDefinition,
      processorTaskDefinition: this.nixpkgsProcessorTaskDefinition,
      fargateSecurityGroup: stepFunctionsSecurityGroup,
    });

    this.pipelineStateMachine = new stepfunctions.StateMachine(this, 'PipelineStateMachine', {
      definitionBody: stepfunctions.DefinitionBody.fromChainable(definition),
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
        this.nixpkgsEvaluatorTaskDefinition.taskDefinitionArn,
        this.nixpkgsProcessorTaskDefinition.taskDefinitionArn,
        `arn:aws:ecs:${this.region}:${this.account}:task/${this.cluster.clusterName}/*`,
      ],
    }));

    this.pipelineStateMachine.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'iam:PassRole',
      ],
      resources: [
        this.nixpkgsEvaluatorTaskDefinition.executionRole!.roleArn,
        this.nixpkgsEvaluatorTaskDefinition.taskRole!.roleArn,
        this.nixpkgsProcessorTaskDefinition.executionRole!.roleArn,
        this.nixpkgsProcessorTaskDefinition.taskRole!.roleArn,
      ],
    }));

  }
}
