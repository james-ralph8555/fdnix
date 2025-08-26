"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.FdnixPipelineStack = void 0;
const aws_cdk_lib_1 = require("aws-cdk-lib");
const ecs = __importStar(require("aws-cdk-lib/aws-ecs"));
const ecr = __importStar(require("aws-cdk-lib/aws-ecr"));
const iam = __importStar(require("aws-cdk-lib/aws-iam"));
const events = __importStar(require("aws-cdk-lib/aws-events"));
const targets = __importStar(require("aws-cdk-lib/aws-events-targets"));
const ec2 = __importStar(require("aws-cdk-lib/aws-ec2"));
const logs = __importStar(require("aws-cdk-lib/aws-logs"));
const stepfunctions = __importStar(require("aws-cdk-lib/aws-stepfunctions"));
const stepfunctionsTasks = __importStar(require("aws-cdk-lib/aws-stepfunctions-tasks"));
class FdnixPipelineStack extends aws_cdk_lib_1.Stack {
    cluster;
    metadataRepository;
    embeddingRepository;
    metadataTaskDefinition;
    embeddingTaskDefinition;
    pipelineStateMachine;
    constructor(scope, id, props) {
        super(scope, id, props);
        const { databaseStack } = props;
        // Create VPC for Fargate tasks
        const vpc = new ec2.Vpc(this, 'PipelineVpc', {
            vpcName: 'fdnix-pipeline-vpc',
            maxAzs: 2,
            natGateways: 1,
        });
        // ECS Cluster
        this.cluster = new ecs.Cluster(this, 'ProcessingCluster', {
            clusterName: 'fdnix-processing-cluster',
            vpc,
            containerInsights: true,
        });
        // ECR Repositories
        this.metadataRepository = new ecr.Repository(this, 'MetadataRepository', {
            repositoryName: 'fdnix-metadata-generator',
            lifecycleRules: [{
                    maxImageCount: 10,
                }],
        });
        this.embeddingRepository = new ecr.Repository(this, 'EmbeddingRepository', {
            repositoryName: 'fdnix-embedding-generator',
            lifecycleRules: [{
                    maxImageCount: 10,
                }],
        });
        // IAM roles for Fargate tasks
        const fargateExecutionRole = new iam.Role(this, 'FargateExecutionRole', {
            roleName: 'fdnix-fargate-execution-role',
            assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
            ],
        });
        const fargateTaskRole = new iam.Role(this, 'FargateTaskRole', {
            roleName: 'fdnix-fargate-task-role',
            assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
        });
        // Grant database access to task role
        databaseStack.packagesTable.grantReadWriteData(fargateTaskRole);
        databaseStack.vectorIndexBucket.grantReadWrite(fargateTaskRole);
        // Grant Bedrock access
        fargateTaskRole.addToPolicy(new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ['bedrock:InvokeModel'],
            resources: [
                `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-english-v3`,
                `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-multilingual-v3`,
            ],
        }));
        // CloudWatch Log Groups
        const metadataLogGroup = new logs.LogGroup(this, 'MetadataLogGroup', {
            logGroupName: '/fdnix/metadata-generator',
            retention: logs.RetentionDays.ONE_MONTH,
        });
        const embeddingLogGroup = new logs.LogGroup(this, 'EmbeddingLogGroup', {
            logGroupName: '/fdnix/embedding-generator',
            retention: logs.RetentionDays.ONE_MONTH,
        });
        // Fargate Task Definitions
        this.metadataTaskDefinition = new ecs.FargateTaskDefinition(this, 'MetadataTaskDefinition', {
            family: 'fdnix-metadata-task',
            cpu: 1024,
            memoryLimitMiB: 3008,
            executionRole: fargateExecutionRole,
            taskRole: fargateTaskRole,
        });
        const metadataContainer = this.metadataTaskDefinition.addContainer('MetadataContainer', {
            image: ecs.ContainerImage.fromEcrRepository(this.metadataRepository),
            logging: ecs.LogDrivers.awsLogs({
                streamPrefix: 'metadata',
                logGroup: metadataLogGroup,
            }),
            environment: {
                DYNAMODB_TABLE: databaseStack.packagesTable.tableName,
                AWS_REGION: this.region,
            },
        });
        this.embeddingTaskDefinition = new ecs.FargateTaskDefinition(this, 'EmbeddingTaskDefinition', {
            family: 'fdnix-embedding-task',
            cpu: 2048,
            memoryLimitMiB: 6144,
            executionRole: fargateExecutionRole,
            taskRole: fargateTaskRole,
        });
        const embeddingContainer = this.embeddingTaskDefinition.addContainer('EmbeddingContainer', {
            image: ecs.ContainerImage.fromEcrRepository(this.embeddingRepository),
            logging: ecs.LogDrivers.awsLogs({
                streamPrefix: 'embedding',
                logGroup: embeddingLogGroup,
            }),
            environment: {
                DYNAMODB_TABLE: databaseStack.packagesTable.tableName,
                S3_BUCKET: databaseStack.vectorIndexBucket.bucketName,
                AWS_REGION: this.region,
                BEDROCK_MODEL_ID: 'cohere.embed-english-v3',
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
        // Wait state between tasks
        const waitForProcessing = new stepfunctions.Wait(this, 'WaitForProcessing', {
            time: stepfunctions.WaitTime.duration(aws_cdk_lib_1.Duration.minutes(5)),
        });
        // Define the state machine
        const definition = metadataTask
            .next(waitForProcessing)
            .next(embeddingTask);
        this.pipelineStateMachine = new stepfunctions.StateMachine(this, 'PipelineStateMachine', {
            stateMachineName: 'fdnix-daily-pipeline',
            definition,
            timeout: aws_cdk_lib_1.Duration.hours(6),
        });
        // EventBridge rule for daily execution
        const dailyRule = new events.Rule(this, 'DailyPipelineRule', {
            ruleName: 'fdnix-daily-pipeline-trigger',
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
exports.FdnixPipelineStack = FdnixPipelineStack;
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoicGlwZWxpbmUtc3RhY2suanMiLCJzb3VyY2VSb290IjoiIiwic291cmNlcyI6WyJwaXBlbGluZS1zdGFjay50cyJdLCJuYW1lcyI6W10sIm1hcHBpbmdzIjoiOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7QUFBQSw2Q0FBMEQ7QUFFMUQseURBQTJDO0FBQzNDLHlEQUEyQztBQUMzQyx5REFBMkM7QUFDM0MsK0RBQWlEO0FBQ2pELHdFQUEwRDtBQUMxRCx5REFBMkM7QUFDM0MsMkRBQTZDO0FBQzdDLDZFQUErRDtBQUMvRCx3RkFBMEU7QUFPMUUsTUFBYSxrQkFBbUIsU0FBUSxtQkFBSztJQUMzQixPQUFPLENBQWM7SUFDckIsa0JBQWtCLENBQWlCO0lBQ25DLG1CQUFtQixDQUFpQjtJQUNwQyxzQkFBc0IsQ0FBNEI7SUFDbEQsdUJBQXVCLENBQTRCO0lBQ25ELG9CQUFvQixDQUE2QjtJQUVqRSxZQUFZLEtBQWdCLEVBQUUsRUFBVSxFQUFFLEtBQThCO1FBQ3RFLEtBQUssQ0FBQyxLQUFLLEVBQUUsRUFBRSxFQUFFLEtBQUssQ0FBQyxDQUFDO1FBRXhCLE1BQU0sRUFBRSxhQUFhLEVBQUUsR0FBRyxLQUFLLENBQUM7UUFFaEMsK0JBQStCO1FBQy9CLE1BQU0sR0FBRyxHQUFHLElBQUksR0FBRyxDQUFDLEdBQUcsQ0FBQyxJQUFJLEVBQUUsYUFBYSxFQUFFO1lBQzNDLE9BQU8sRUFBRSxvQkFBb0I7WUFDN0IsTUFBTSxFQUFFLENBQUM7WUFDVCxXQUFXLEVBQUUsQ0FBQztTQUNmLENBQUMsQ0FBQztRQUVILGNBQWM7UUFDZCxJQUFJLENBQUMsT0FBTyxHQUFHLElBQUksR0FBRyxDQUFDLE9BQU8sQ0FBQyxJQUFJLEVBQUUsbUJBQW1CLEVBQUU7WUFDeEQsV0FBVyxFQUFFLDBCQUEwQjtZQUN2QyxHQUFHO1lBQ0gsaUJBQWlCLEVBQUUsSUFBSTtTQUN4QixDQUFDLENBQUM7UUFFSCxtQkFBbUI7UUFDbkIsSUFBSSxDQUFDLGtCQUFrQixHQUFHLElB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Iiwic291cmNlc0NvbnRlbnQiOlsiaW1wb3J0IHsgU3RhY2ssIFN0YWNrUHJvcHMsIER1cmF0aW9uIH0gZnJvbSAnYXdzLWNkay1saWInO1xuaW1wb3J0IHsgQ29uc3RydWN0IH0gZnJvbSAnY29uc3RydWN0cyc7XG5pbXBvcnQgKiBhcyBlY3MgZnJvbSAnYXdzLWNkay1saWIvYXdzLWVjcyc7XG5pbXBvcnQgKiBhcyBlY3IgZnJvbSAnYXdzLWNkay1saWIvYXdzLWVjcic7XG5pbXBvcnQgKiBhcyBpYW0gZnJvbSAnYXdzLWNkay1saWIvYXdzLWlhbSc7XG5pbXBvcnQgKiBhcyBldmVudHMgZnJvbSAnYXdzLWNkay1saWIvYXdzLWV2ZW50cyc7XG5pbXBvcnQgKiBhcyB0YXJnZXRzIGZyb20gJ2F3cy1jZGstbGliL2F3cy1ldmVudHMtdGFyZ2V0cyc7XG5pbXBvcnQgKiBhcyBlYzIgZnJvbSAnYXdzLWNkay1saWIvYXdzLWVjMic7XG5pbXBvcnQgKiBhcyBsb2dzIGZyb20gJ2F3cy1jZGstbGliL2F3cy1sb2dzJztcbmltcG9ydCAqIGFzIHN0ZXBmdW5jdGlvbnMgZnJvbSAnYXdzLWNkay1saWIvYXdzLXN0ZXBmdW5jdGlvbnMnO1xuaW1wb3J0ICogYXMgc3RlcGZ1bmN0aW9uc1Rhc2tzIGZyb20gJ2F3cy1jZGstbGliL2F3cy1zdGVwZnVuY3Rpb25zLXRhc2tzJztcbmltcG9ydCB7IEZkbml4RGF0YWJhc2VTdGFjayB9IGZyb20gJy4vZGF0YWJhc2Utc3RhY2snO1xuXG5leHBvcnQgaW50ZXJmYWNlIEZkbml4UGlwZWxpbmVTdGFja1Byb3BzIGV4dGVuZHMgU3RhY2tQcm9wcyB7XG4gIGRhdGFiYXNlU3RhY2s6IEZkbml4RGF0YWJhc2VTdGFjaztcbn1cblxuZXhwb3J0IGNsYXNzIEZkbml4UGlwZWxpbmVTdGFjayBleHRlbmRzIFN0YWNrIHtcbiAgcHVibGljIHJlYWRvbmx5IGNsdXN0ZXI6IGVjcy5DbHVzdGVyO1xuICBwdWJsaWMgcmVhZG9ubHkgbWV0YWRhdGFSZXBvc2l0b3J5OiBlY3IuUmVwb3NpdG9yeTtcbiAgcHVibGljIHJlYWRvbmx5IGVtYmVkZGluZ1JlcG9zaXRvcnk6IGVjci5SZXBvc2l0b3J5O1xuICBwdWJsaWMgcmVhZG9ubHkgbWV0YWRhdGFUYXNrRGVmaW5pdGlvbjogZWNzLkZhcmdhdGVUYXNrRGVmaW5pdGlvbjtcbiAgcHVibGljIHJlYWRvbmx5IGVtYmVkZGluZ1Rhc2tEZWZpbml0aW9uOiBlY3MuRmFyZ2F0ZVRhc2tEZWZpbml0aW9uO1xuICBwdWJsaWMgcmVhZG9ubHkgcGlwZWxpbmVTdGF0ZU1hY2hpbmU6IHN0ZXBmdW5jdGlvbnMuU3RhdGVNYWNoaW5lO1xuXG4gIGNvbnN0cnVjdG9yKHNjb3BlOiBDb25zdHJ1Y3QsIGlkOiBzdHJpbmcsIHByb3BzOiBGZG5peFBpcGVsaW5lU3RhY2tQcm9wcykge1xuICAgIHN1cGVyKHNjb3BlLCBpZCwgcHJvcHMpO1xuXG4gICAgY29uc3QgeyBkYXRhYmFzZVN0YWNrIH0gPSBwcm9wcztcblxuICAgIC8vIENyZWF0ZSBWUEMgZm9yIEZhcmdhdGUgdGFza3NcbiAgICBjb25zdCB2cGMgPSBuZXcgZWMyLlZwYyh0aGlzLCAnUGlwZWxpbmVWcGMnLCB7XG4gICAgICB2cGNOYW1lOiAnZmRuaXgtcGlwZWxpbmUtdnBjJyxcbiAgICAgIG1heEF6czogMixcbiAgICAgIG5hdEdhdGV3YXlzOiAxLFxuICAgIH0pO1xuXG4gICAgLy8gRUNTIENsdXN0ZXJcbiAgICB0aGlzLmNsdXN0ZXIgPSBuZXcgZWNzLkNsdXN0ZXIodGhpcywgJ1Byb2Nlc3NpbmdDbHVzdGVyJywge1xuICAgICAgY2x1c3Rlck5hbWU6ICdmZG5peC1wcm9jZXNzaW5nLWNsdXN0ZXInLFxuICAgICAgdnBjLFxuICAgICAgY29udGFpbmVySW5zaWdodHM6IHRydWUsXG4gICAgfSk7XG5cbiAgICAvLyBFQ1IgUmVwb3NpdG9yaWVzXG4gICAgdGhpcy5tZXRhZGF0YVJlcG9zaXRvcnkgPSBuZXcgZWNyLlJlcG9zaXRvcnkodGhpcywgJ01ldGFkYXRhUmVwb3NpdG9yeScsIHtcbiAgICAgIHJlcG9zaXRvcnlOYW1lOiAnZmRuaXgtbWV0YWRhdGEtZ2VuZXJhdG9yJyxcbiAgICAgIGxpZmVjeWNsZVJ1bGVzOiBbe1xuICAgICAgICBtYXhJbWFnZUNvdW50OiAxMCxcbiAgICAgIH1dLFxuICAgIH0pO1xuXG4gICAgdGhpcy5lbWJlZGRpbmdSZXBvc2l0b3J5ID0gbmV3IGVjci5SZXBvc2l0b3J5KHRoaXMsICdFbWJlZGRpbmdSZXBvc2l0b3J5Jywge1xuICAgICAgcmVwb3NpdG9yeU5hbWU6ICdmZG5peC1lbWJlZGRpbmctZ2VuZXJhdG9yJyxcbiAgICAgIGxpZmVjeWNsZVJ1bGVzOiBbe1xuICAgICAgICBtYXhJbWFnZUNvdW50OiAxMCxcbiAgICAgIH1dLFxuICAgIH0pO1xuXG4gICAgLy8gSUFNIHJvbGVzIGZvciBGYXJnYXRlIHRhc2tzXG4gICAgY29uc3QgZmFyZ2F0ZUV4ZWN1dGlvblJvbGUgPSBuZXcgaWFtLlJvbGUodGhpcywgJ0ZhcmdhdGVFeGVjdXRpb25Sb2xlJywge1xuICAgICAgcm9sZU5hbWU6ICdmZG5peC1mYXJnYXRlLWV4ZWN1dGlvbi1yb2xlJyxcbiAgICAgIGFzc3VtZWRCeTogbmV3IGlhbS5TZXJ2aWNlUHJpbmNpcGFsKCdlY3MtdGFza3MuYW1hem9uYXdzLmNvbScpLFxuICAgICAgbWFuYWdlZFBvbGljaWVzOiBbXG4gICAgICAgIGlhbS5NYW5hZ2VkUG9saWN5LmZyb21Bd3NNYW5hZ2VkUG9saWN5TmFtZSgnc2VydmljZS1yb2xlL0FtYXpvbkVDU1Rhc2tFeGVjdXRpb25Sb2xlUG9saWN5JyksXG4gICAgICBdLFxuICAgIH0pO1xuXG4gICAgY29uc3QgZmFyZ2F0ZVRhc2tSb2xlID0gbmV3IGlhbS5Sb2xlKHRoaXMsICdGYXJnYXRlVGFza1JvbGUnLCB7XG4gICAgICByb2xlTmFtZTogJ2Zkbml4LWZhcmdhdGUtdGFzay1yb2xlJyxcbiAgICAgIGFzc3VtZWRCeTogbmV3IGlhbS5TZXJ2aWNlUHJpbmNpcGFsKCdlY3MtdGFza3MuYW1hem9uYXdzLmNvbScpLFxuICAgIH0pO1xuXG4gICAgLy8gR3JhbnQgZGF0YWJhc2UgYWNjZXNzIHRvIHRhc2sgcm9sZVxuICAgIGRhdGFiYXNlU3RhY2sucGFja2FnZXNUYWJsZS5ncmFudFJlYWRXcml0ZURhdGEoZmFyZ2F0ZVRhc2tSb2xlKTtcbiAgICBkYXRhYmFzZVN0YWNrLnZlY3RvckluZGV4QnVja2V0LmdyYW50UmVhZFdyaXRlKGZhcmdhdGVUYXNrUm9sZSk7XG5cblxuICAgIC8vIEdyYW50IEJlZHJvY2sgYWNjZXNzXG4gICAgZmFyZ2F0ZVRhc2tSb2xlLmFkZFRvUG9saWN5KG5ldyBpYW0uUG9saWN5U3RhdGVtZW50KHtcbiAgICAgIGVmZmVjdDogaWFtLkVmZmVjdC5BTExPVyxcbiAgICAgIGFjdGlvbnM6IFsnYmVkcm9jazpJbnZva2VNb2RlbCddLFxuICAgICAgcmVzb3VyY2VzOiBbXG4gICAgICAgIGBhcm46YXdzOmJlZHJvY2s6JHt0aGlzLnJlZ2lvbn06OmZvdW5kYXRpb24tbW9kZWwvY29oZXJlLmVtYmVkLWVuZ2xpc2gtdjNgLFxuICAgICAgICBgYXJuOmF3czpiZWRyb2NrOiR7dGhpcy5yZWdpb259Ojpmb3VuZGF0aW9uLW1vZGVsL2NvaGVyZS5lbWJlZC1tdWx0aWxpbmd1YWwtdjNgLFxuICAgICAgXSxcbiAgICB9KSk7XG5cbiAgICAvLyBDbG91ZFdhdGNoIExvZyBHcm91cHNcbiAgICBjb25zdCBtZXRhZGF0YUxvZ0dyb3VwID0gbmV3IGxvZ3MuTG9nR3JvdXAodGhpcywgJ01ldGFkYXRhTG9nR3JvdXAnLCB7XG4gICAgICBsb2dHcm91cE5hbWU6ICcvZmRuaXgvbWV0YWRhdGEtZ2VuZXJhdG9yJyxcbiAgICAgIHJldGVudGlvbjogbG9ncy5SZXRlbnRpb25EYXlzLk9ORV9NT05USCxcbiAgICB9KTtcblxuICAgIGNvbnN0IGVtYmVkZGluZ0xvZ0dyb3VwID0gbmV3IGxvZ3MuTG9nR3JvdXAodGhpcywgJ0VtYmVkZGluZ0xvZ0dyb3VwJywge1xuICAgICAgbG9nR3JvdXBOYW1lOiAnL2Zkbml4L2VtYmVkZGluZy1nZW5lcmF0b3InLFxuICAgICAgcmV0ZW50aW9uOiBsb2dzLlJldGVudGlvbkRheXMuT05FX01PTlRILFxuICAgIH0pO1xuXG4gICAgLy8gRmFyZ2F0ZSBUYXNrIERlZmluaXRpb25zXG4gICAgdGhpcy5tZXRhZGF0YVRhc2tEZWZpbml0aW9uID0gbmV3IGVjcy5GYXJnYXRlVGFza0RlZmluaXRpb24odGhpcywgJ01ldGFkYXRhVGFza0RlZmluaXRpb24nLCB7XG4gICAgICBmYW1pbHk6ICdmZG5peC1tZXRhZGF0YS10YXNrJyxcbiAgICAgIGNwdTogMTAyNCxcbiAgICAgIG1lbW9yeUxpbWl0TWlCOiAzMDA4LFxuICAgICAgZXhlY3V0aW9uUm9sZTogZmFyZ2F0ZUV4ZWN1dGlvblJvbGUsXG4gICAgICB0YXNrUm9sZTogZmFyZ2F0ZVRhc2tSb2xlLFxuICAgIH0pO1xuXG4gICAgY29uc3QgbWV0YWRhdGFDb250YWluZXIgPSB0aGlzLm1ldGFkYXRhVGFza0RlZmluaXRpb24uYWRkQ29udGFpbmVyKCdNZXRhZGF0YUNvbnRhaW5lcicsIHtcbiAgICAgIGltYWdlOiBlY3MuQ29udGFpbmVySW1hZ2UuZnJvbUVjclJlcG9zaXRvcnkodGhpcy5tZXRhZGF0YVJlcG9zaXRvcnkpLFxuICAgICAgbG9nZ2luZzogZWNzLkxvZ0RyaXZlcnMuYXdzTG9ncyh7XG4gICAgICAgIHN0cmVhbVByZWZpeDogJ21ldGFkYXRhJyxcbiAgICAgICAgbG9nR3JvdXA6IG1ldGFkYXRhTG9nR3JvdXAsXG4gICAgICB9KSxcbiAgICAgIGVudmlyb25tZW50OiB7XG4gICAgICAgIERZTkFNT0RCX1RBQkxFOiBkYXRhYmFzZVN0YWNrLnBhY2thZ2VzVGFibGUudGFibGVOYW1lLFxuICAgICAgICBBV1NfUkVHSU9OOiB0aGlzLnJlZ2lvbixcbiAgICAgIH0sXG4gICAgfSk7XG5cbiAgICB0aGlzLmVtYmVkZGluZ1Rhc2tEZWZpbml0aW9uID0gbmV3IGVjcy5GYXJnYXRlVGFza0RlZmluaXRpb24odGhpcywgJ0VtYmVkZGluZ1Rhc2tEZWZpbml0aW9uJywge1xuICAgICAgZmFtaWx5OiAnZmRuaXgtZW1iZWRkaW5nLXRhc2snLFxuICAgICAgY3B1OiAyMDQ4LFxuICAgICAgbWVtb3J5TGltaXRNaUI6IDYxNDQsXG4gICAgICBleGVjdXRpb25Sb2xlOiBmYXJnYXRlRXhlY3V0aW9uUm9sZSxcbiAgICAgIHRhc2tSb2xlOiBmYXJnYXRlVGFza1JvbGUsXG4gICAgfSk7XG5cbiAgICBjb25zdCBlbWJlZGRpbmdDb250YWluZXIgPSB0aGlzLmVtYmVkZGluZ1Rhc2tEZWZpbml0aW9uLmFkZENvbnRhaW5lcignRW1iZWRkaW5nQ29udGFpbmVyJywge1xuICAgICAgaW1hZ2U6IGVjcy5Db250YWluZXJJbWFnZS5mcm9tRWNyUmVwb3NpdG9yeSh0aGlzLmVtYmVkZGluZ1JlcG9zaXRvcnkpLFxuICAgICAgbG9nZ2luZzogZWNzLkxvZ0RyaXZlcnMuYXdzTG9ncyh7XG4gICAgICAgIHN0cmVhbVByZWZpeDogJ2VtYmVkZGluZycsXG4gICAgICAgIGxvZ0dyb3VwOiBlbWJlZGRpbmdMb2dHcm91cCxcbiAgICAgIH0pLFxuICAgICAgZW52aXJvbm1lbnQ6IHtcbiAgICAgICAgRFlOQU1PREJfVEFCTEU6IGRhdGFiYXNlU3RhY2sucGFja2FnZXNUYWJsZS50YWJsZU5hbWUsXG4gICAgICAgIFMzX0JVQ0tFVDogZGF0YWJhc2VTdGFjay52ZWN0b3JJbmRleEJ1Y2tldC5idWNrZXROYW1lLFxuICAgICAgICBBV1NfUkVHSU9OOiB0aGlzLnJlZ2lvbixcbiAgICAgICAgQkVEUk9DS19NT0RFTF9JRDogJ2NvaGVyZS5lbWJlZC1lbmdsaXNoLXYzJyxcbiAgICAgIH0sXG4gICAgfSk7XG5cbiAgICAvLyBTdGVwIEZ1bmN0aW9ucyBTdGF0ZSBNYWNoaW5lIGZvciBwaXBlbGluZSBvcmNoZXN0cmF0aW9uXG4gICAgY29uc3QgbWV0YWRhdGFUYXNrID0gbmV3IHN0ZXBmdW5jdGlvbnNUYXNrcy5FY3NSdW5UYXNrKHRoaXMsICdNZXRhZGF0YVRhc2snLCB7XG4gICAgICBpbnRlZ3JhdGlvblBhdHRlcm46IHN0ZXBmdW5jdGlvbnMuSW50ZWdyYXRpb25QYXR0ZXJuLlJVTl9KT0IsXG4gICAgICBjbHVzdGVyOiB0aGlzLmNsdXN0ZXIsXG4gICAgICB0YXNrRGVmaW5pdGlvbjogdGhpcy5tZXRhZGF0YVRhc2tEZWZpbml0aW9uLFxuICAgICAgbGF1bmNoVGFyZ2V0OiBuZXcgc3RlcGZ1bmN0aW9uc1Rhc2tzLkVjc0ZhcmdhdGVMYXVuY2hUYXJnZXQoKSxcbiAgICAgIGFzc2lnblB1YmxpY0lwOiB0cnVlLFxuICAgIH0pO1xuXG4gICAgY29uc3QgZW1iZWRkaW5nVGFzayA9IG5ldyBzdGVwZnVuY3Rpb25zVGFza3MuRWNzUnVuVGFzayh0aGlzLCAnRW1iZWRkaW5nVGFzaycsIHtcbiAgICAgIGludGVncmF0aW9uUGF0dGVybjogc3RlcGZ1bmN0aW9ucy5JbnRlZ3JhdGlvblBhdHRlcm4uUlVOX0pPQixcbiAgICAgIGNsdXN0ZXI6IHRoaXMuY2x1c3RlcixcbiAgICAgIHRhc2tEZWZpbml0aW9uOiB0aGlzLmVtYmVkZGluZ1Rhc2tEZWZpbml0aW9uLFxuICAgICAgbGF1bmNoVGFyZ2V0OiBuZXcgc3RlcGZ1bmN0aW9uc1Rhc2tzLkVjc0ZhcmdhdGVMYXVuY2hUYXJnZXQoKSxcbiAgICAgIGFzc2lnblB1YmxpY0lwOiB0cnVlLFxuICAgIH0pO1xuXG4gICAgLy8gV2FpdCBzdGF0ZSBiZXR3ZWVuIHRhc2tzXG4gICAgY29uc3Qgd2FpdEZvclByb2Nlc3NpbmcgPSBuZXcgc3RlcGZ1bmN0aW9ucy5XYWl0KHRoaXMsICdXYWl0Rm9yUHJvY2Vzc2luZycsIHtcbiAgICAgIHRpbWU6IHN0ZXBmdW5jdGlvbnMuV2FpdFRpbWUuZHVyYXRpb24oRHVyYXRpb24ubWludXRlcyg1KSksXG4gICAgfSk7XG5cbiAgICAvLyBEZWZpbmUgdGhlIHN0YXRlIG1hY2hpbmVcbiAgICBjb25zdCBkZWZpbml0aW9uID0gbWV0YWRhdGFUYXNrXG4gICAgICAubmV4dCh3YWl0Rm9yUHJvY2Vzc2luZylcbiAgICAgIC5uZXh0KGVtYmVkZGluZ1Rhc2spO1xuXG4gICAgdGhpcy5waXBlbGluZVN0YXRlTWFjaGluZSA9IG5ldyBzdGVwZnVuY3Rpb25zLlN0YXRlTWFjaGluZSh0aGlzLCAnUGlwZWxpbmVTdGF0ZU1hY2hpbmUnLCB7XG4gICAgICBzdGF0ZU1hY2hpbmVOYW1lOiAnZmRuaXgtZGFpbHktcGlwZWxpbmUnLFxuICAgICAgZGVmaW5pdGlvbixcbiAgICAgIHRpbWVvdXQ6IER1cmF0aW9uLmhvdXJzKDYpLFxuICAgIH0pO1xuXG4gICAgLy8gRXZlbnRCcmlkZ2UgcnVsZSBmb3IgZGFpbHkgZXhlY3V0aW9uXG4gICAgY29uc3QgZGFpbHlSdWxlID0gbmV3IGV2ZW50cy5SdWxlKHRoaXMsICdEYWlseVBpcGVsaW5lUnVsZScsIHtcbiAgICAgIHJ1bGVOYW1lOiAnZmRuaXgtZGFpbHktcGlwZWxpbmUtdHJpZ2dlcicsXG4gICAgICBkZXNjcmlwdGlvbjogJ1RyaWdnZXJzIHRoZSBmZG5peCBkYXRhIHByb2Nlc3NpbmcgcGlwZWxpbmUgZGFpbHknLFxuICAgICAgc2NoZWR1bGU6IGV2ZW50cy5TY2hlZHVsZS5jcm9uKHsgXG4gICAgICAgIGhvdXI6ICcyJywgXG4gICAgICAgIG1pbnV0ZTogJzAnIFxuICAgICAgfSksXG4gICAgfSk7XG5cbiAgICAvLyBBZGQgdGhlIHN0YXRlIG1hY2hpbmUgYXMgYSB0YXJnZXRcbiAgICBkYWlseVJ1bGUuYWRkVGFyZ2V0KG5ldyB0YXJnZXRzLlNmblN0YXRlTWFjaGluZSh0aGlzLnBpcGVsaW5lU3RhdGVNYWNoaW5lKSk7XG5cbiAgICAvLyBHcmFudCBTdGVwIEZ1bmN0aW9ucyBwZXJtaXNzaW9ucyB0byBydW4gRUNTIHRhc2tzXG4gICAgdGhpcy5waXBlbGluZVN0YXRlTWFjaGluZS5hZGRUb1JvbGVQb2xpY3kobmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xuICAgICAgZWZmZWN0OiBpYW0uRWZmZWN0LkFMTE9XLFxuICAgICAgYWN0aW9uczogW1xuICAgICAgICAnZWNzOlJ1blRhc2snLFxuICAgICAgICAnZWNzOlN0b3BUYXNrJyxcbiAgICAgICAgJ2VjczpEZXNjcmliZVRhc2tzJyxcbiAgICAgIF0sXG4gICAgICByZXNvdXJjZXM6IFtcbiAgICAgICAgdGhpcy5tZXRhZGF0YVRhc2tEZWZpbml0aW9uLnRhc2tEZWZpbml0aW9uQXJuLFxuICAgICAgICB0aGlzLmVtYmVkZGluZ1Rhc2tEZWZpbml0aW9uLnRhc2tEZWZpbml0aW9uQXJuLFxuICAgICAgICBgYXJuOmF3czplY3M6JHt0aGlzLnJlZ2lvbn06JHt0aGlzLmFjY291bnR9OnRhc2svJHt0aGlzLmNsdXN0ZXIuY2x1c3Rlck5hbWV9LypgLFxuICAgICAgXSxcbiAgICB9KSk7XG5cbiAgICB0aGlzLnBpcGVsaW5lU3RhdGVNYWNoaW5lLmFkZFRvUm9sZVBvbGljeShuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XG4gICAgICBlZmZlY3Q6IGlhbS5FZmZlY3QuQUxMT1csXG4gICAgICBhY3Rpb25zOiBbXG4gICAgICAgICdpYW06UGFzc1JvbGUnLFxuICAgICAgXSxcbiAgICAgIHJlc291cmNlczogW1xuICAgICAgICBmYXJnYXRlRXhlY3V0aW9uUm9sZS5yb2xlQXJuLFxuICAgICAgICBmYXJnYXRlVGFza1JvbGUucm9sZUFybixcbiAgICAgIF0sXG4gICAgfSkpO1xuICB9XG59Il19