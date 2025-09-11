import { Construct } from 'constructs';
import { Duration, Stack } from 'aws-cdk-lib';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as autoscaling from 'aws-cdk-lib/aws-autoscaling';
import * as stepfunctions from 'aws-cdk-lib/aws-stepfunctions';
import * as stepfunctionsTasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as iam from 'aws-cdk-lib/aws-iam';

export interface PipelineStateMachineProps {
  cluster: ecs.Cluster;
  evaluatorTaskDefinition: ecs.Ec2TaskDefinition;
  processorTaskDefinition: ecs.FargateTaskDefinition;
  evaluatorContainer: ecs.ContainerDefinition;
  processorContainer: ecs.ContainerDefinition;
  evaluatorAutoScalingGroup: autoscaling.AutoScalingGroup;
  fargateSecurityGroup: ec2.SecurityGroup;
}

export class PipelineStateMachine extends Construct {
  public readonly stateMachine: stepfunctions.StateMachine;

  constructor(scope: Construct, id: string, props: PipelineStateMachineProps) {
    super(scope, id);

    const {
      cluster,
      evaluatorTaskDefinition,
      processorTaskDefinition,
      evaluatorContainer,
      processorContainer,
      evaluatorAutoScalingGroup,
      fargateSecurityGroup,
    } = props;

    const stack = Stack.of(this);

    // Step Functions Auto Scaling Tasks for EC2 evaluator instances
    const scaleUpEvaluatorTask = new stepfunctionsTasks.CallAwsService(this, 'ScaleUpEvaluatorTask', {
      service: 'autoscaling',
      action: 'setDesiredCapacity',
      parameters: {
        AutoScalingGroupName: evaluatorAutoScalingGroup.autoScalingGroupName,
        DesiredCapacity: 1,
        HonorCooldown: false,
      },
      iamResources: [
        `arn:aws:autoscaling:${stack.region}:${stack.account}:autoScalingGroup:*:autoScalingGroupName/${evaluatorAutoScalingGroup.autoScalingGroupName}`,
      ],
    });

    const scaleDownEvaluatorTask = new stepfunctionsTasks.CallAwsService(this, 'ScaleDownEvaluatorTask', {
      service: 'autoscaling',
      action: 'setDesiredCapacity',
      parameters: {
        AutoScalingGroupName: evaluatorAutoScalingGroup.autoScalingGroupName,
        DesiredCapacity: 0,
        HonorCooldown: false,
      },
      iamResources: [
        `arn:aws:autoscaling:${stack.region}:${stack.account}:autoScalingGroup:*:autoScalingGroupName/${evaluatorAutoScalingGroup.autoScalingGroupName}`,
      ],
    });

    // Check if EC2 instances are running and registered with ECS
    const checkInstancesTask = new stepfunctionsTasks.CallAwsService(this, 'CheckInstancesTask', {
      service: 'autoscaling',
      action: 'describeAutoScalingGroups',
      parameters: {
        AutoScalingGroupNames: [evaluatorAutoScalingGroup.autoScalingGroupName],
      },
      iamResources: [
        `arn:aws:autoscaling:${stack.region}:${stack.account}:autoScalingGroup:*:autoScalingGroupName/${evaluatorAutoScalingGroup.autoScalingGroupName}`,
      ],
      resultPath: '$.AutoScalingResult',
    });

    // 60-second wait between polling attempts
    const waitBetweenPolls = new stepfunctions.Wait(this, 'WaitBetweenPolls', {
      time: stepfunctions.WaitTime.duration(Duration.seconds(60)),
    });

    // Pass state to indicate instances are ready
    const instancesReady = new stepfunctions.Pass(this, 'InstancesReady');

    // Choice state to check if instances are ready
    const checkInstancesReady = new stepfunctions.Choice(this, 'CheckInstancesReady')
      .when(
        stepfunctions.Condition.and(
          stepfunctions.Condition.numberGreaterThanEquals('$.AutoScalingResult.AutoScalingGroups[0].DesiredCapacity', 1),
          stepfunctions.Condition.isPresent('$.AutoScalingResult.AutoScalingGroups[0].Instances[0]'),
          stepfunctions.Condition.stringEquals('$.AutoScalingResult.AutoScalingGroups[0].Instances[0].LifecycleState', 'InService')
        ),
        instancesReady
      )
      .otherwise(waitBetweenPolls);

    // Create the polling loop
    checkInstancesTask.next(checkInstancesReady);
    waitBetweenPolls.next(checkInstancesTask);

    // The polling loop starts with checking instances
    const instancePollingLoop = checkInstancesTask;

    // Stage 1: Evaluator Task (EC2)
    const evaluatorTask = new stepfunctionsTasks.EcsRunTask(this, 'NixpkgsEvaluatorTask', {
      integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
      cluster,
      taskDefinition: evaluatorTaskDefinition,
      launchTarget: new stepfunctionsTasks.EcsEc2LaunchTarget(),
      containerOverrides: [{
        containerDefinition: evaluatorContainer,
        environment: [
          {
            name: 'JSONL_OUTPUT_KEY',
            value: stepfunctions.JsonPath.format(
              'evaluations/{}/nixpkgs-raw.jsonl',
              stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'),
            ),
          },
        ],
      }],
      resultPath: '$.EvaluatorResult',
    });

    // Pass defaults for processing-only runs
    const setDefaultKeys = new stepfunctions.Pass(this, 'SetDefaultKeys', {
      parameters: {
        'jsonlInputKey.$': '$.jsonlInputKey',
        'lancedbDataKey.$': stepfunctions.JsonPath.format('snapshots/{}/fdnix-data.lancedb', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime')),
        'lancedbMinifiedKey.$': stepfunctions.JsonPath.format('snapshots/{}/fdnix.lancedb', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime')),
        'dependencyS3Key.$': stepfunctions.JsonPath.format('dependencies/{}/fdnix-deps.json', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime')),
      },
    });

    // Stage 2a: Processor Task (when using existing JSONL outputs)
    const processorTask = new stepfunctionsTasks.EcsRunTask(this, 'NixpkgsProcessorTask', {
      integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
      cluster,
      taskDefinition: processorTaskDefinition,
      launchTarget: new stepfunctionsTasks.EcsFargateLaunchTarget({
        platformVersion: ecs.FargatePlatformVersion.LATEST,
      }),
      assignPublicIp: false, // Use IPv6 egress-only networking
      securityGroups: [fargateSecurityGroup],
      subnets: { subnetType: ec2.SubnetType.PUBLIC },
      containerOverrides: [{
        containerDefinition: processorContainer,
        environment: [
          { name: 'JSONL_INPUT_KEY', value: stepfunctions.JsonPath.stringAt('$.jsonlInputKey') },
          { name: 'LANCEDB_DATA_KEY', value: stepfunctions.JsonPath.stringAt('$.lancedbDataKey') },
          { name: 'LANCEDB_MINIFIED_KEY', value: stepfunctions.JsonPath.stringAt('$.lancedbMinifiedKey') },
          { name: 'DEPENDENCY_S3_KEY', value: stepfunctions.JsonPath.stringAt('$.dependencyS3Key') },
        ],
      }],
      resultPath: '$.ProcessorResult',
    });

    // Stage 2b: Processor Task (after evaluation stage)
    const processorTaskWithEvaluatorOutput = new stepfunctionsTasks.EcsRunTask(this, 'NixpkgsProcessorTaskWithEvaluatorOutput', {
      integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
      cluster,
      taskDefinition: processorTaskDefinition,
      launchTarget: new stepfunctionsTasks.EcsFargateLaunchTarget({
        platformVersion: ecs.FargatePlatformVersion.LATEST,
      }),
      assignPublicIp: false, // Use IPv6 egress-only networking
      securityGroups: [fargateSecurityGroup],
      subnets: { subnetType: ec2.SubnetType.PUBLIC },
      containerOverrides: [{
        containerDefinition: processorContainer,
        environment: [
          {
            name: 'JSONL_INPUT_KEY',
            value: stepfunctions.JsonPath.format('evaluations/{}/nixpkgs-raw.jsonl', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime')),
          },
          {
            name: 'LANCEDB_DATA_KEY',
            value: stepfunctions.JsonPath.format('snapshots/{}/fdnix-data.lancedb', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime')),
          },
          {
            name: 'LANCEDB_MINIFIED_KEY',
            value: stepfunctions.JsonPath.format('snapshots/{}/fdnix.lancedb', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime')),
          },
          {
            name: 'DEPENDENCY_S3_KEY',
            value: stepfunctions.JsonPath.format('dependencies/{}/fdnix-deps.json', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime')),
          },
        ],
      }],
      resultPath: '$.ProcessorResult',
    });

    // Connect the instances ready state to the evaluator task
    instancesReady.next(evaluatorTask);

    // Evaluator with scale up, poll for readiness, then run
    const evaluatorWithScaling = scaleUpEvaluatorTask
      .next(instancePollingLoop);

    // Wrap only the evaluator workflow in error handling
    const evaluatorTryCatch = new stepfunctions.Parallel(this, 'EvaluatorTryCatch', {
      resultPath: '$.EvaluatorResults',
    })
      .branch(evaluatorWithScaling)
      .addCatch(new stepfunctions.Pass(this, 'EvaluatorFailedPass', {
        result: stepfunctions.Result.fromObject({ status: 'EVALUATOR_FAILED' }),
      }), {
        errors: ['States.ALL'],
        resultPath: '$.EvaluatorError',
      });

    // Always scale down after the evaluator completes (success or failure)
    const evaluatorWorkflowWithErrorHandling = evaluatorTryCatch
      .next(scaleDownEvaluatorTask)
      .next(processorTaskWithEvaluatorOutput);

    // Choice: skip evaluation if jsonlInputKey present
    const definition = new stepfunctions.Choice(this, 'CheckForExistingOutputs')
      .when(stepfunctions.Condition.isPresent('$.jsonlInputKey'), setDefaultKeys.next(processorTask))
      .otherwise(evaluatorWorkflowWithErrorHandling);

    this.stateMachine = new stepfunctions.StateMachine(this, 'PipelineStateMachine', {
      definition,
      timeout: Duration.hours(6),
    });

    // Add permission to describe Auto Scaling Groups
    this.stateMachine.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['autoscaling:DescribeAutoScalingGroups'],
      resources: ['*'], // DescribeAutoScalingGroups doesn't support resource-level permissions
    }));
  }
}

