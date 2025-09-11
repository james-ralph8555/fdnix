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

  // Extract parameters from Name/Value array format and set default values
  // This ensures we have S3 keys for all outputs even if not provided in input
  const setDefaultKeys = new stepfunctions.Pass(scope, 'SetDefaultKeys', {
    parameters: {
      'jsonlInputKey.$': '$.[?(@.Name==\'JSONL_INPUT_KEY\')].Value | [0]',
      'lancedbDataKey.$': '$.[?(@.Name==\'LANCEDB_DATA_KEY\')].Value | [0] // null',
      'lancedbMinifiedKey.$': '$.[?(@.Name==\'LANCEDB_MINIFIED_KEY\')].Value | [0] // null', 
      'dependencyS3Key.$': '$.[?(@.Name==\'DEPENDENCY_S3_KEY\')].Value | [0] // null',
      'defaultLancedbDataKey': stepfunctions.JsonPath.format('snapshots/{}/fdnix-data.lancedb', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime')),
      'defaultLancedbMinifiedKey': stepfunctions.JsonPath.format('snapshots/{}/fdnix.lancedb', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime')),
      'defaultDependencyS3Key': stepfunctions.JsonPath.format('dependencies/{}/fdnix-deps.json', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'))
    }
  });

  // Apply defaults for null values
  const applyDefaults = new stepfunctions.Pass(scope, 'ApplyDefaults', {
    parameters: {
      'jsonlInputKey.$': '$.jsonlInputKey',
      'lancedbDataKey.$': '$.lancedbDataKey // $.defaultLancedbDataKey',
      'lancedbMinifiedKey.$': '$.lancedbMinifiedKey // $.defaultLancedbMinifiedKey',
      'dependencyS3Key.$': '$.dependencyS3Key // $.defaultDependencyS3Key'
    }
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
          value: stepfunctions.JsonPath.stringAt('$.jsonlInputKey')
        },
        {
          name: 'LANCEDB_DATA_KEY',
          value: stepfunctions.JsonPath.stringAt('$.lancedbDataKey')
        },
        {
          name: 'LANCEDB_MINIFIED_KEY',
          value: stepfunctions.JsonPath.stringAt('$.lancedbMinifiedKey')
        },
        {
          name: 'DEPENDENCY_S3_KEY',
          value: stepfunctions.JsonPath.stringAt('$.dependencyS3Key')
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
          name: 'LANCEDB_DATA_KEY',
          value: stepfunctions.JsonPath.format('snapshots/{}/fdnix-data.lancedb', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'))
        },
        {
          name: 'LANCEDB_MINIFIED_KEY',
          value: stepfunctions.JsonPath.format('snapshots/{}/fdnix.lancedb', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'))
        },
        {
          name: 'DEPENDENCY_S3_KEY',
          value: stepfunctions.JsonPath.format('dependencies/{}/fdnix-deps.json', stepfunctions.JsonPath.stringAt('$$.Execution.StartTime'))
        }
      ]
    }],
    resultPath: '$.ProcessorResult',
  });

  // Check if JSONL outputs are provided in the input to skip evaluation
  // If JSONL_INPUT_KEY parameter is present, we skip evaluation and go straight to processing
  // Otherwise, we run the full pipeline: evaluation then processing
  const checkForExistingOutputs = new stepfunctions.Choice(scope, 'CheckForExistingOutputs')
    .when(
      stepfunctions.Condition.isPresent('$.[?(@.Name==\'JSONL_INPUT_KEY\')]'),
      setDefaultKeys.next(applyDefaults).next(processorTask)
    )
    .otherwise(
      evaluatorTask.next(processorTaskWithEvaluatorOutput)
    );

  // Define the conditional pipeline:
  // Path 1: JSONL provided -> SetDefaultKeys -> ApplyDefaults -> ProcessorTask (skip evaluation)
  // Path 2: No JSONL -> EvaluatorTask -> ProcessorTaskWithEvaluatorOutput (full pipeline)
  return checkForExistingOutputs;
}