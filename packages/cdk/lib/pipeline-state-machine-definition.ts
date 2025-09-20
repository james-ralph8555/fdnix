import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as stepfunctions from 'aws-cdk-lib/aws-stepfunctions';
import * as stepfunctionsTasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import { Construct } from 'constructs';

export interface StateMachineDefinitionProps {
  cluster: ecs.Cluster;
  evaluatorTaskDefinition: ecs.FargateTaskDefinition;
  processorTaskDefinition: ecs.FargateTaskDefinition;
  fargateSecurityGroup: ec2.SecurityGroup;
}

export function createPipelineStateMachineDefinition(
  scope: Construct,
  props: StateMachineDefinitionProps
): stepfunctions.IChainable {
  const { cluster, evaluatorTaskDefinition, processorTaskDefinition, fargateSecurityGroup } = props;

  // Get the container definitions from the task definitions
  const evaluatorContainer = evaluatorTaskDefinition.defaultContainer!;
  const processorContainer = processorTaskDefinition.defaultContainer!;

  // Stage 1: Evaluator Task
  const evaluatorTask = new stepfunctionsTasks.EcsRunTask(scope, 'NixpkgsEvaluatorTask', {
    integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
    cluster: cluster,
    taskDefinition: evaluatorTaskDefinition,
    launchTarget: new stepfunctionsTasks.EcsFargateLaunchTarget({
      platformVersion: ecs.FargatePlatformVersion.LATEST,
    }),
    assignPublicIp: true, // Required for public subnet access to AWS services
    securityGroups: [fargateSecurityGroup], // Use our HTTPS-only security group
    subnets: { subnetType: ec2.SubnetType.PUBLIC }, // Explicitly use public subnets
    containerOverrides: [{
      containerDefinition: evaluatorContainer,
      environment: [
        {
          name: 'JSONL_OUTPUT_KEY',
          value: stepfunctions.JsonPath.format('evaluations/{}/nixpkgs-raw.jsonl.br', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'))
        }
      ]
    }],
    resultPath: '$.EvaluatorResult',
  });



  // Stage 2a: Processor Task (when using existing JSONL outputs, skipping evaluation)
  const processorTask = new stepfunctionsTasks.EcsRunTask(scope, 'NixpkgsProcessorTask', {
    integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
    cluster: cluster,
    taskDefinition: processorTaskDefinition,
    launchTarget: new stepfunctionsTasks.EcsFargateLaunchTarget({
      platformVersion: ecs.FargatePlatformVersion.LATEST,
    }),
    assignPublicIp: true, // Required for public subnet access to AWS services
    securityGroups: [fargateSecurityGroup], // Use our HTTPS-only security group
    subnets: { subnetType: ec2.SubnetType.PUBLIC }, // Explicitly use public subnets
    containerOverrides: [{
      containerDefinition: processorContainer,
      environment: [
        {
          name: 'JSONL_INPUT_KEY',
          value: stepfunctions.JsonPath.stringAt('$.JSONL_INPUT_KEY')
        },
        {
          name: 'SQLITE_DATA_KEY',
          value: stepfunctions.JsonPath.stringAt('$.SQLITE_DATA_KEY')
        },
        {
          name: 'SQLITE_MINIFIED_KEY',
          value: stepfunctions.JsonPath.stringAt('$.SQLITE_MINIFIED_KEY')
        },
        {
          name: 'DEPENDENCY_S3_KEY',
          value: stepfunctions.JsonPath.stringAt('$.DEPENDENCY_S3_KEY')
        }
      ]
    }],
    resultPath: '$.ProcessorResult',
  });

  // Stage 2b: Processor Task (when following evaluation stage with fresh outputs)
  const processorTaskWithEvaluatorOutput = new stepfunctionsTasks.EcsRunTask(scope, 'NixpkgsProcessorTaskWithEvaluatorOutput', {
    integrationPattern: stepfunctions.IntegrationPattern.RUN_JOB,
    cluster: cluster,
    taskDefinition: processorTaskDefinition,
    launchTarget: new stepfunctionsTasks.EcsFargateLaunchTarget({
      platformVersion: ecs.FargatePlatformVersion.LATEST,
    }),
    assignPublicIp: true, // Required for public subnet access to AWS services
    securityGroups: [fargateSecurityGroup], // Use our HTTPS-only security group
    subnets: { subnetType: ec2.SubnetType.PUBLIC }, // Explicitly use public subnets
    containerOverrides: [{
      containerDefinition: processorContainer,
      environment: [
        {
          name: 'JSONL_INPUT_KEY',
          value: stepfunctions.JsonPath.format('evaluations/{}/nixpkgs-raw.jsonl.br', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'))
        },
        {
          name: 'SQLITE_DATA_KEY',
          value: stepfunctions.JsonPath.format('snapshots/{}/fdnix-data.db', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'))
        },
        {
          name: 'SQLITE_MINIFIED_KEY',
          value: stepfunctions.JsonPath.format('snapshots/{}/fdnix.db', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'))
        },
        {
          name: 'DEPENDENCY_S3_KEY',
          value: stepfunctions.JsonPath.format('dependencies/{}/fdnix-deps.json', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'))
        }
      ]
    }],
    resultPath: '$.ProcessorResult',
  });

  // Check if JSONL input is provided to skip evaluation
  const checkForExistingOutputs = new stepfunctions.Choice(scope, 'CheckForExistingOutputs')
    .when(
      stepfunctions.Condition.isPresent('$.JSONL_INPUT_KEY'),
      processorTask
    )
    .otherwise(
      evaluatorTask.next(processorTaskWithEvaluatorOutput)
    );

  // Define the conditional pipeline:
  // Path 1: All parameters provided -> ProcessorTask (skip evaluation)
  // Path 2: No JSONL -> EvaluatorTask -> ProcessorTaskWithEvaluatorOutput (full pipeline)
  return checkForExistingOutputs;
}