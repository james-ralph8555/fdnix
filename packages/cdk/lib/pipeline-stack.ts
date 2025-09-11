import { Stack, StackProps, Duration, RemovalPolicy, Arn, ArnFormat, Fn, Names } from 'aws-cdk-lib';
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
import * as autoscaling from 'aws-cdk-lib/aws-autoscaling';
import * as path from 'path';
import { FdnixDatabaseStack } from './database-stack';
import { DockerBuildConstruct } from './docker-build-construct';
import { PipelineStateMachine } from './constructs/pipeline-state-machine';

export interface FdnixPipelineStackProps extends StackProps {
  databaseStack: FdnixDatabaseStack;
}

export class FdnixPipelineStack extends Stack {
  public readonly cluster: ecs.Cluster;
  // Stage 1: Evaluator (EC2)
  public readonly nixpkgsEvaluatorRepository: ecr.Repository;
  public readonly nixpkgsEvaluatorTaskDefinition: ecs.Ec2TaskDefinition;
  public readonly nixpkgsEvaluatorDockerBuild: DockerBuildConstruct;
  public readonly evaluatorAutoScalingGroup: autoscaling.AutoScalingGroup;
  // Stage 2: Processor (Fargate)
  public readonly nixpkgsProcessorRepository: ecr.Repository;
  public readonly nixpkgsProcessorTaskDefinition: ecs.FargateTaskDefinition;
  public readonly nixpkgsProcessorDockerBuild: DockerBuildConstruct;
  // Pipeline
  public readonly pipelineStateMachine: stepfunctions.StateMachine;
  public readonly bedrockBatchRole: iam.Role;

  constructor(scope: Construct, id: string, props: FdnixPipelineStackProps) {
    super(scope, id, props);

    const { databaseStack } = props;

    // Create VPC for Fargate tasks with $0/hr configuration
    // 
    // This configuration eliminates hourly charges by:
    // - Using public subnets with IPv6 egress-only (no NAT Gateway: saves ~$45/month)  
    // - Gateway VPC endpoints for S3/DynamoDB (no hourly fees, no data transfer costs)
    // - IPv6 egress-only internet access (no public IPv4 addresses)
    // - Security group restricting egress to HTTPS only for security
    const vpc = new ec2.Vpc(this, 'PipelineVpc', {
      maxAzs: 2,
      natGateways: 0, // $0/hr: No NAT Gateway
      enableDnsHostnames: true,
      enableDnsSupport: true,
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
          mapPublicIpOnLaunch: false, // Disable IPv4 public IP assignment
        },
        // No private subnets to avoid NAT Gateway requirement
      ],
    });

    // Enable IPv6 for the VPC with Amazon-provided IPv6 CIDR block
    const ipv6CidrBlock = new ec2.CfnVPCCidrBlock(this, 'Ipv6CidrBlock', {
      vpcId: vpc.vpcId,
      amazonProvidedIpv6CidrBlock: true,
    });

    // Configure IPv6 CIDR blocks for subnets
    vpc.publicSubnets.forEach((subnet, index) => {
      const cfnSubnet = subnet.node.defaultChild as ec2.CfnSubnet;
      cfnSubnet.ipv6CidrBlock = Fn.select(index, Fn.cidr(Fn.select(0, vpc.vpcIpv6CidrBlocks), 2, '64'));
      cfnSubnet.assignIpv6AddressOnCreation = true;
      cfnSubnet.addDependency(ipv6CidrBlock);
    });

    // Create egress-only internet gateway for IPv6
    const eigw = new ec2.CfnEgressOnlyInternetGateway(this, 'EgressOnlyIGW', {
      vpcId: vpc.vpcId,
    });

    // Add IPv6 routes to route tables for egress-only access
    vpc.publicSubnets.forEach((subnet) => {
      new ec2.CfnRoute(this, `IPv6Route-${subnet.node.id}`, {
        routeTableId: subnet.routeTable.routeTableId,
        destinationIpv6CidrBlock: '::/0',
        egressOnlyInternetGatewayId: eigw.ref,
      });
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
      ec2.Peer.anyIpv6(),
      ec2.Port.tcp(443),
      'HTTPS outbound for AWS services (IPv6)'
    );

    // Allow HTTP (80) for package downloads (Nix, pip, etc.) - can be removed if not needed
    fargateSecurityGroup.addEgressRule(
      ec2.Peer.anyIpv6(),
      ec2.Port.tcp(80),
      'HTTP outbound for package downloads (IPv6)'
    );

    // Security Group for EC2 instances (evaluator) - HTTPS egress only
    const ec2SecurityGroup = new ec2.SecurityGroup(this, 'EC2SecurityGroup', {
      vpc,
      description: 'Security group for EC2 instances running evaluator with HTTPS egress only',
      allowAllOutbound: false, // Explicitly deny all outbound by default
    });

    // Allow HTTPS (443) outbound traffic only - for AWS APIs, ECR, etc.
    ec2SecurityGroup.addEgressRule(
      ec2.Peer.anyIpv6(),
      ec2.Port.tcp(443),
      'HTTPS outbound for AWS services (IPv6)'
    );

    // Allow HTTP (80) for package downloads (Nix, nix-eval-jobs, etc.)
    ec2SecurityGroup.addEgressRule(
      ec2.Peer.anyIpv6(),
      ec2.Port.tcp(80),
      'HTTP outbound for package downloads (IPv6)'
    );

    // ECS Cluster
    this.cluster = new ecs.Cluster(this, 'ProcessingCluster', {
      vpc,
      containerInsightsV2: ecs.ContainerInsights.ENHANCED,
    });

    // Apply removal policy for proper cleanup
    this.cluster.applyRemovalPolicy(RemovalPolicy.DESTROY);

    // IAM role for EC2 instances (moved before LaunchTemplate)
    const ec2InstanceRole = new iam.Role(this, 'EC2InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonEC2ContainerServiceforEC2Role'),
      ],
    });

    // Launch Template for EC2 instances (evaluator)
    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      `echo ECS_CLUSTER=${this.cluster.clusterName} >> /etc/ecs/ecs.config`,
      'echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config',
      'echo ECS_BACKEND_HOST= >> /etc/ecs/ecs.config', // Clear any backend host override
      'echo ECS_AVAILABLE_LOGGING_DRIVERS=["json-file","awslogs"] >> /etc/ecs/ecs.config',
      // Restart ECS agent to pick up configuration
      'systemctl restart ecs',
      // Wait for ECS agent to start
      'sleep 30'
    );

    const launchTemplate = new ec2.LaunchTemplate(this, 'EvaluatorLaunchTemplate', {
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.I4I, ec2.InstanceSize.XLARGE),
      machineImage: ecs.EcsOptimizedImage.amazonLinux2(),
      securityGroup: ec2SecurityGroup,
      associatePublicIpAddress: false, // Use IPv6 egress-only networking
      userData,
      role: ec2InstanceRole, // Associate IAM role with EC2 instances
    });

    // Apply removal policy for proper cleanup
    launchTemplate.applyRemovalPolicy(RemovalPolicy.DESTROY);

    // Auto Scaling Group for evaluator ($0/hr when idle)
    this.evaluatorAutoScalingGroup = new autoscaling.AutoScalingGroup(this, 'EvaluatorAutoScalingGroup', {
      vpc,
      launchTemplate,
      minCapacity: 0, // $0/hr: No instances when idle
      maxCapacity: 1, // Single instance for evaluator tasks
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC }, // Use public subnets for internet access
    });

    // Apply removal policy for proper cleanup
    this.evaluatorAutoScalingGroup.applyRemovalPolicy(RemovalPolicy.DESTROY);

    // Create capacity provider and add it to cluster
    const capacityProvider = new ecs.AsgCapacityProvider(this, 'EvaluatorCapacityProvider', {
      autoScalingGroup: this.evaluatorAutoScalingGroup,
      enableManagedScaling: true,
      enableManagedTerminationProtection: false,
      targetCapacityPercent: 100,
    });

    // Add capacity provider to cluster
    this.cluster.addAsgCapacityProvider(capacityProvider);




    // ECR Repositories for both stages
    this.nixpkgsEvaluatorRepository = new ecr.Repository(this, 'NixpkgsEvaluatorRepository', {
      repositoryName: `fdnix-pipeline-nixpkgs-evaluator-${Names.uniqueId(this).toLowerCase().slice(-8)}`,
      lifecycleRules: [{
        maxImageCount: 10,
      }],
      removalPolicy: RemovalPolicy.DESTROY,
    });

    this.nixpkgsProcessorRepository = new ecr.Repository(this, 'NixpkgsProcessorRepository', {
      repositoryName: `fdnix-pipeline-nixpkgs-processor-${Names.uniqueId(this).toLowerCase().slice(-8)}`,
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
      logGroupName: `/aws/ecs/fdnix-pipeline-nixpkgs-evaluator-${Names.uniqueId(this).toLowerCase().slice(-8)}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });


    const processorLogGroup = new logs.LogGroup(this, 'NixpkgsProcessorLogGroup', {
      logGroupName: `/aws/ecs/fdnix-pipeline-nixpkgs-processor-${Names.uniqueId(this).toLowerCase().slice(-8)}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // Stage 1: EC2 Task Definition for nixpkgs-evaluator (uses full i4i.xlarge: 4vCPU, 32GB RAM, local NVMe)
    this.nixpkgsEvaluatorTaskDefinition = new ecs.Ec2TaskDefinition(this, 'NixpkgsEvaluatorTaskDefinition', {
      // No CPU/memory limits - uses full i4i.xlarge instance resources
      // No ephemeral storage - uses local NVMe storage for maximum I/O performance
      executionRole: fargateExecutionRole,
      taskRole: evaluatorTaskRole,
    });

    const evaluatorContainer = this.nixpkgsEvaluatorTaskDefinition.addContainer('NixpkgsEvaluatorContainer', {
      image: ecs.ContainerImage.fromDockerImageAsset(this.nixpkgsEvaluatorDockerBuild.dockerImageAsset),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'nixpkgs-evaluator',
        logGroup: evaluatorLogGroup,
      }),
      // Memory configuration for EC2 task (i4i.xlarge has 32GB RAM)
      memoryReservationMiB: 30720, // Reserve 30GB of the 32GB available
      environment: {
        AWS_REGION: this.region,
        ARTIFACTS_BUCKET: databaseStack.artifactsBucket.bucketName,
        // Output will be picked up by Step Functions and passed to Stage 2
        JSONL_OUTPUT_KEY: 'evaluations/nixpkgs-raw.jsonl', // Will be overridden with timestamp
      },
    });


    // Stage 2: Fargate Task Definition for nixpkgs-processor (2vCPU, 6GB RAM, 40GB storage)
    this.nixpkgsProcessorTaskDefinition = new ecs.FargateTaskDefinition(this, 'NixpkgsProcessorTaskDefinition', {
      cpu: 2048,      // 2 vCPU - optimized for data processing
      memoryLimitMiB: 6144,   // 6GB RAM - sufficient for LanceDB operations
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

    // Extracted Step Functions into a reusable construct
    const pipelineSm = new PipelineStateMachine(this, 'PipelineStateMachineConstruct', {
      cluster: this.cluster,
      evaluatorTaskDefinition: this.nixpkgsEvaluatorTaskDefinition,
      processorTaskDefinition: this.nixpkgsProcessorTaskDefinition,
      evaluatorContainer,
      processorContainer,
      evaluatorAutoScalingGroup: this.evaluatorAutoScalingGroup,
      fargateSecurityGroup,
    });

    this.pipelineStateMachine = pipelineSm.stateMachine;

    // Apply removal policy for proper cleanup
    this.pipelineStateMachine.applyRemovalPolicy(RemovalPolicy.DESTROY);

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
        fargateExecutionRole.roleArn,
        evaluatorTaskRole.roleArn,
        processorTaskRole.roleArn,
      ],
    }));

    // Grant Step Functions permissions to scale Auto Scaling Groups
    this.pipelineStateMachine.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'autoscaling:SetDesiredCapacity',
        'autoscaling:DescribeAutoScalingGroups',
      ],
      resources: [
        `arn:aws:autoscaling:${this.region}:${this.account}:autoScalingGroup:*:autoScalingGroupName/${this.evaluatorAutoScalingGroup.autoScalingGroupName}`,
      ],
    }));
  }
}
