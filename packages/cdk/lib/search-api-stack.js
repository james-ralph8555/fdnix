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
exports.FdnixSearchApiStack = void 0;
const aws_cdk_lib_1 = require("aws-cdk-lib");
const lambda = __importStar(require("aws-cdk-lib/aws-lambda"));
const apigateway = __importStar(require("aws-cdk-lib/aws-apigateway"));
const iam = __importStar(require("aws-cdk-lib/aws-iam"));
const logs = __importStar(require("aws-cdk-lib/aws-logs"));
const path = __importStar(require("path"));
class FdnixSearchApiStack extends aws_cdk_lib_1.Stack {
    searchFunction;
    api;
    lambdaExecutionRole;
    constructor(scope, id, props) {
        super(scope, id, props);
        const { databaseStack } = props;
        // CloudWatch Log Group for Lambda
        const logGroup = new logs.LogGroup(this, 'SearchLambdaLogGroup', {
            logGroupName: '/aws/lambda/fdnix-search-api',
            retention: logs.RetentionDays.ONE_MONTH,
        });
        // IAM role for Lambda execution
        this.lambdaExecutionRole = new iam.Role(this, 'LambdaExecutionRole', {
            roleName: 'fdnix-lambda-execution-role',
            assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
            ],
        });
        // Grant database access
        databaseStack.packagesTable.grantReadData(this.lambdaExecutionRole);
        databaseStack.vectorIndexBucket.grantRead(this.lambdaExecutionRole);
        // Grant Bedrock access for query embeddings
        this.lambdaExecutionRole.addToPolicy(new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                'bedrock:InvokeModel',
            ],
            resources: [
                `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-english-v3`,
                `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-multilingual-v3`,
            ],
        }));
        // Lambda Layer for dependencies (Faiss, AWS SDK, etc.)
        const dependenciesLayer = new lambda.LayerVersion(this, 'SearchDependenciesLayer', {
            layerVersionName: 'fdnix-search-dependencies',
            code: lambda.Code.fromAsset(path.join(__dirname, '../../search-lambda/layer')),
            compatibleRuntimes: [lambda.Runtime.NODEJS_18_X],
            description: 'Dependencies for fdnix search API including Faiss bindings',
        });
        // Lambda function for hybrid search API
        this.searchFunction = new lambda.Function(this, 'SearchFunction', {
            functionName: 'fdnix-search-api',
            runtime: lambda.Runtime.NODEJS_18_X,
            handler: 'index.handler',
            code: lambda.Code.fromAsset(path.join(__dirname, '../../search-lambda/dist')),
            timeout: aws_cdk_lib_1.Duration.seconds(30),
            memorySize: 1024,
            role: this.lambdaExecutionRole,
            layers: [dependenciesLayer],
            logGroup,
            environment: {
                DYNAMODB_TABLE: databaseStack.packagesTable.tableName,
                S3_BUCKET: databaseStack.vectorIndexBucket.bucketName,
                AWS_REGION: this.region,
                BEDROCK_MODEL_ID: 'cohere.embed-english-v3',
                VECTOR_INDEX_KEY: 'faiss-index/packages.index',
                VECTOR_MAPPING_KEY: 'faiss-index/package-mapping.json',
            },
        });
        // API Gateway
        this.api = new apigateway.RestApi(this, 'SearchApiGateway', {
            restApiName: 'fdnix-search-api-gateway',
            description: 'API Gateway for fdnix hybrid search engine',
            defaultCorsPreflightOptions: {
                allowOrigins: apigateway.Cors.ALL_ORIGINS,
                allowMethods: apigateway.Cors.ALL_METHODS,
                allowHeaders: ['Content-Type', 'X-Amz-Date', 'Authorization', 'X-Api-Key'],
            },
            deployOptions: {
                stageName: 'v1',
                loggingLevel: apigateway.MethodLoggingLevel.INFO,
                dataTraceEnabled: true,
                metricsEnabled: true,
            },
        });
        // Lambda integration
        const lambdaIntegration = new apigateway.LambdaIntegration(this.searchFunction, {
            proxy: true,
            allowTestInvoke: true,
        });
        // API resources and methods
        const searchResource = this.api.root.addResource('search');
        searchResource.addMethod('GET', lambdaIntegration, {
            requestParameters: {
                'method.request.querystring.q': true,
                'method.request.querystring.limit': false,
                'method.request.querystring.offset': false,
                'method.request.querystring.license': false,
                'method.request.querystring.category': false,
            },
            methodResponses: [
                {
                    statusCode: '200',
                },
                {
                    statusCode: '400',
                },
                {
                    statusCode: '500',
                },
            ],
        });
        // Health check endpoint
        const healthResource = this.api.root.addResource('health');
        const healthIntegration = new apigateway.MockIntegration({
            integrationResponses: [
                {
                    statusCode: '200',
                    responseTemplates: {
                        'application/json': JSON.stringify({
                            status: 'healthy',
                            timestamp: '$context.requestTime',
                            version: '1.0.0',
                        }),
                    },
                },
            ],
            requestTemplates: {
                'application/json': '{"statusCode": 200}',
            },
        });
        healthResource.addMethod('GET', healthIntegration, {
            methodResponses: [
                {
                    statusCode: '200',
                },
            ],
        });
        // Usage plan for rate limiting
        const usagePlan = this.api.addUsagePlan('SearchApiUsagePlan', {
            name: 'fdnix-search-usage-plan',
            description: 'Usage plan for fdnix search API',
            throttle: {
                rateLimit: 100,
                burstLimit: 200,
            },
            quota: {
                limit: 10000,
                period: apigateway.Period.DAY,
            },
        });
        usagePlan.addApiStage({
            stage: this.api.deploymentStage,
        });
        // CloudWatch dashboard for monitoring
        // Note: Dashboard creation would be added here if needed
    }
}
exports.FdnixSearchApiStack = FdnixSearchApiStack;
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoic2VhcmNoLWFwaS1zdGFjay5qcyIsInNvdXJjZVJvb3QiOiIiLCJzb3VyY2VzIjpbInNlYXJjaC1hcGktc3RhY2sudHMiXSwibmFtZXMiOltdLCJtYXBwaW5ncyI6Ijs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7O0FBQUEsNkNBQTBEO0FBRTFELCtEQUFpRDtBQUNqRCx1RUFBeUQ7QUFDekQseURBQTJDO0FBQzNDLDJEQUE2QztBQUM3QywyQ0FBNkI7QUFPN0IsTUFBYSxtQkFBb0IsU0FBUSxtQkFBSztJQUM1QixjQUFjLENBQWtCO0lBQ2hDLEdBQUcsQ0FBcUI7SUFDeEIsbUJBQW1CLENBQVc7SUFFOUMsWUFBWSxLQUFnQixFQUFFLEVBQVUsRUFBRSxLQUErQjtRQUN2RSxLQUFLLENBQUMsS0FBSyxFQUFFLEVBQUUsRUFBRSxLQUFLLENBQUMsQ0FBQztRQUV4QixNQUFNLEVBQUUsYUFBYSxFQUFFLEdBQUcsS0FBSyxDQUFDO1FBRWhDLGtDQUFrQztRQUNsQyxNQUFNLFFBQVEsR0FBRyxJQUFJLElBQUksQ0FBQyxRQUFRLENBQUMsSUFBSSxFQUFFLHNCQUFzQixFQUFFO1lBQy9ELFlBQVksRUFBRSw4QkFBOEI7WUFDNUMsU0FBUyxFQUFFLElBQUksQ0FBQyxhQUFhLENBQUMsU0FBUztTQUN4QyxDQUFDLENBQUM7UUFFSCxnQ0FBZ0M7UUFDaEMsSUFBSSxDQUFDLG1CQUFtQixHQUFHLElBQUksR0FBRyxDQUFDLElBQUksQ0FBQyxJQUFJLEVBQUUscUJBQXFCLEVBQUU7WUFDbkUsUUFBUSxFQUFFLDZCQUE2QjtZQUN2QyxTQUFTLEVBQUUsSUFBSSxHQUFHLENBQUMsZ0JBQWdCLENBQUMsc0JBQXNCLENBQUM7WUFDM0QsZUFBZSxFQUFFO2dCQUNmLEdBQUcsQ0FBQyxhQUFhLENBQUMsd0JBQXdCLENBQUMsMENBQTBDLENBQUM7YUFDdkY7U0FDRixDQUFDLENBQUM7UUFFSCx3QkFBd0I7UUFDeEIsYUFBYSxDQUFDLGFBQWEsQ0FBQyxhQUFhLENBQUMs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QUMsU0FBUyxDQUFDLEtBQUssRUFBRSxpQkFBaUIsRUFBRTtZQUNqRCxlQUFlLEVBQUU7Z0JBQ2Y7b0JBQ0UsVUFBVSxFQUFFLEtBQUs7aUJBQ2xCO2FBQ0Y7U0FDRixDQUFDLENBQUM7UUFFSCwrQkFBK0I7UUFDL0IsTUFBTSxTQUFTLEdBQUcsSUFBSSxDQUFDLEdBQUcsQ0FBQyxZQUFZLENBQUMsb0JBQW9CLEVBQUU7WUFDNUQsSUFBSSxFQUFFLHlCQUF5QjtZQUMvQixXQUFXLEVBQUUsaUNBQWlDO1lBQzlDLFFBQVEsRUFBRTtnQkFDUixTQUFTLEVBQUUsR0FBRztnQkFDZCxVQUFVLEVBQUUsR0FBRzthQUNoQjtZQUNELEtBQUssRUFBRTtnQkFDTCxLQUFLLEVBQUUsS0FBSztnQkFDWixNQUFNLEVBQUUsVUFBVSxDQUFDLE1BQU0sQ0FBQyxHQUFHO2FBQzlCO1NBQ0YsQ0FBQyxDQUFDO1FBRUgsU0FBUyxDQUFDLFdBQVcsQ0FBQztZQUNwQixLQUFLLEVBQUUsSUFBSSxDQUFDLEdBQUcsQ0FBQyxlQUFlO1NBQ2hDLENBQUMsQ0FBQztRQUVILHNDQUFzQztRQUN0Qyx5REFBeUQ7SUFDM0QsQ0FBQztDQUNGO0FBdEtELGtEQXNLQyIsInNvdXJjZXNDb250ZW50IjpbImltcG9ydCB7IFN0YWNrLCBTdGFja1Byb3BzLCBEdXJhdGlvbiB9IGZyb20gJ2F3cy1jZGstbGliJztcbmltcG9ydCB7IENvbnN0cnVjdCB9IGZyb20gJ2NvbnN0cnVjdHMnO1xuaW1wb3J0ICogYXMgbGFtYmRhIGZyb20gJ2F3cy1jZGstbGliL2F3cy1sYW1iZGEnO1xuaW1wb3J0ICogYXMgYXBpZ2F0ZXdheSBmcm9tICdhd3MtY2RrLWxpYi9hd3MtYXBpZ2F0ZXdheSc7XG5pbXBvcnQgKiBhcyBpYW0gZnJvbSAnYXdzLWNkay1saWIvYXdzLWlhbSc7XG5pbXBvcnQgKiBhcyBsb2dzIGZyb20gJ2F3cy1jZGstbGliL2F3cy1sb2dzJztcbmltcG9ydCAqIGFzIHBhdGggZnJvbSAncGF0aCc7XG5pbXBvcnQgeyBGZG5peERhdGFiYXNlU3RhY2sgfSBmcm9tICcuL2RhdGFiYXNlLXN0YWNrJztcblxuZXhwb3J0IGludGVyZmFjZSBGZG5peFNlYXJjaEFwaVN0YWNrUHJvcHMgZXh0ZW5kcyBTdGFja1Byb3BzIHtcbiAgZGF0YWJhc2VTdGFjazogRmRuaXhEYXRhYmFzZVN0YWNrO1xufVxuXG5leHBvcnQgY2xhc3MgRmRuaXhTZWFyY2hBcGlTdGFjayBleHRlbmRzIFN0YWNrIHtcbiAgcHVibGljIHJlYWRvbmx5IHNlYXJjaEZ1bmN0aW9uOiBsYW1iZGEuRnVuY3Rpb247XG4gIHB1YmxpYyByZWFkb25seSBhcGk6IGFwaWdhdGV3YXkuUmVzdEFwaTtcbiAgcHVibGljIHJlYWRvbmx5IGxhbWJkYUV4ZWN1dGlvblJvbGU6IGlhbS5Sb2xlO1xuXG4gIGNvbnN0cnVjdG9yKHNjb3BlOiBDb25zdHJ1Y3QsIGlkOiBzdHJpbmcsIHByb3BzOiBGZG5peFNlYXJjaEFwaVN0YWNrUHJvcHMpIHtcbiAgICBzdXBlcihzY29wZSwgaWQsIHByb3BzKTtcblxuICAgIGNvbnN0IHsgZGF0YWJhc2VTdGFjayB9ID0gcHJvcHM7XG5cbiAgICAvLyBDbG91ZFdhdGNoIExvZyBHcm91cCBmb3IgTGFtYmRhXG4gICAgY29uc3QgbG9nR3JvdXAgPSBuZXcgbG9ncy5Mb2dHcm91cCh0aGlzLCAnU2VhcmNoTGFtYmRhTG9nR3JvdXAnLCB7XG4gICAgICBsb2dHcm91cE5hbWU6ICcvYXdzL2xhbWJkYS9mZG5peC1zZWFyY2gtYXBpJyxcbiAgICAgIHJldGVudGlvbjogbG9ncy5SZXRlbnRpb25EYXlzLk9ORV9NT05USCxcbiAgICB9KTtcblxuICAgIC8vIElBTSByb2xlIGZvciBMYW1iZGEgZXhlY3V0aW9uXG4gICAgdGhpcy5sYW1iZGFFeGVjdXRpb25Sb2xlID0gbmV3IGlhbS5Sb2xlKHRoaXMsICdMYW1iZGFFeGVjdXRpb25Sb2xlJywge1xuICAgICAgcm9sZU5hbWU6ICdmZG5peC1sYW1iZGEtZXhlY3V0aW9uLXJvbGUnLFxuICAgICAgYXNzdW1lZEJ5OiBuZXcgaWFtLlNlcnZpY2VQcmluY2lwYWwoJ2xhbWJkYS5hbWF6b25hd3MuY29tJyksXG4gICAgICBtYW5hZ2VkUG9saWNpZXM6IFtcbiAgICAgICAgaWFtLk1hbmFnZWRQb2xpY3kuZnJvbUF3c01hbmFnZWRQb2xpY3lOYW1lKCdzZXJ2aWNlLXJvbGUvQVdTTGFtYmRhQmFzaWNFeGVjdXRpb25Sb2xlJyksXG4gICAgICBdLFxuICAgIH0pO1xuXG4gICAgLy8gR3JhbnQgZGF0YWJhc2UgYWNjZXNzXG4gICAgZGF0YWJhc2VTdGFjay5wYWNrYWdlc1RhYmxlLmdyYW50UmVhZERhdGEodGhpcy5sYW1iZGFFeGVjdXRpb25Sb2xlKTtcbiAgICBkYXRhYmFzZVN0YWNrLnZlY3RvckluZGV4QnVja2V0LmdyYW50UmVhZCh0aGlzLmxhbWJkYUV4ZWN1dGlvblJvbGUpO1xuXG5cbiAgICAvLyBHcmFudCBCZWRyb2NrIGFjY2VzcyBmb3IgcXVlcnkgZW1iZWRkaW5nc1xuICAgIHRoaXMubGFtYmRhRXhlY3V0aW9uUm9sZS5hZGRUb1BvbGljeShuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XG4gICAgICBlZmZlY3Q6IGlhbS5FZmZlY3QuQUxMT1csXG4gICAgICBhY3Rpb25zOiBbXG4gICAgICAgICdiZWRyb2NrOkludm9rZU1vZGVsJyxcbiAgICAgIF0sXG4gICAgICByZXNvdXJjZXM6IFtcbiAgICAgICAgYGFybjphd3M6YmVkcm9jazoke3RoaXMucmVnaW9ufTo6Zm91bmRhdGlvbi1tb2RlbC9jb2hlcmUuZW1iZWQtZW5nbGlzaC12M2AsXG4gICAgICAgIGBhcm46YXdzOmJlZHJvY2s6JHt0aGlzLnJlZ2lvbn06OmZvdW5kYXRpb24tbW9kZWwvY29oZXJlLmVtYmVkLW11bHRpbGluZ3VhbC12M2AsXG4gICAgICBdLFxuICAgIH0pKTtcblxuICAgIC8vIExhbWJkYSBMYXllciBmb3IgZGVwZW5kZW5jaWVzIChGYWlzcywgQVdTIFNESywgZXRjLilcbiAgICBjb25zdCBkZXBlbmRlbmNpZXNMYXllciA9IG5ldyBsYW1iZGEuTGF5ZXJWZXJzaW9uKHRoaXMsICdTZWFyY2hEZXBlbmRlbmNpZXNMYXllcicsIHtcbiAgICAgIGxheWVyVmVyc2lvbk5hbWU6ICdmZG5peC1zZWFyY2gtZGVwZW5kZW5jaWVzJyxcbiAgICAgIGNvZGU6IGxhbWJkYS5Db2RlLmZyb21Bc3NldChwYXRoLmpvaW4oX19kaXJuYW1lLCAnLi4vLi4vc2VhcmNoLWxhbWJkYS9sYXllcicpKSxcbiAgICAgIGNvbXBhdGlibGVSdW50aW1lczogW2xhbWJkYS5SdW50aW1lLk5PREVKU18xOF9YXSxcbiAgICAgIGRlc2NyaXB0aW9uOiAnRGVwZW5kZW5jaWVzIGZvciBmZG5peCBzZWFyY2ggQVBJIGluY2x1ZGluZyBGYWlzcyBiaW5kaW5ncycsXG4gICAgfSk7XG5cbiAgICAvLyBMYW1iZGEgZnVuY3Rpb24gZm9yIGh5YnJpZCBzZWFyY2ggQVBJXG4gICAgdGhpcy5zZWFyY2hGdW5jdGlvbiA9IG5ldyBsYW1iZGEuRnVuY3Rpb24odGhpcywgJ1NlYXJjaEZ1bmN0aW9uJywge1xuICAgICAgZnVuY3Rpb25OYW1lOiAnZmRuaXgtc2VhcmNoLWFwaScsXG4gICAgICBydW50aW1lOiBsYW1iZGEuUnVudGltZS5OT0RFSlNfMThfWCxcbiAgICAgIGhhbmRsZXI6ICdpbmRleC5oYW5kbGVyJyxcbiAgICAgIGNvZGU6IGxhbWJkYS5Db2RlLmZyb21Bc3NldChwYXRoLmpvaW4oX19kaXJuYW1lLCAnLi4vLi4vc2VhcmNoLWxhbWJkYS9kaXN0JykpLFxuICAgICAgdGltZW91dDogRHVyYXRpb24uc2Vjb25kcygzMCksXG4gICAgICBtZW1vcnlTaXplOiAxMDI0LFxuICAgICAgcm9sZTogdGhpcy5sYW1iZGFFeGVjdXRpb25Sb2xlLFxuICAgICAgbGF5ZXJzOiBbZGVwZW5kZW5jaWVzTGF5ZXJdLFxuICAgICAgbG9nR3JvdXAsXG4gICAgICBlbnZpcm9ubWVudDoge1xuICAgICAgICBEWU5BTU9EQl9UQUJMRTogZGF0YWJhc2VTdGFjay5wYWNrYWdlc1RhYmxlLnRhYmxlTmFtZSxcbiAgICAgICAgUzNfQlVDS0VUOiBkYXRhYmFzZVN0YWNrLnZlY3RvckluZGV4QnVja2V0LmJ1Y2tldE5hbWUsXG4gICAgICAgIEFXU19SRUdJT046IHRoaXMucmVnaW9uLFxuICAgICAgICBCRURST0NLX01PREVMX0lEOiAnY29oZXJlLmVtYmVkLWVuZ2xpc2gtdjMnLFxuICAgICAgICBWRUNUT1JfSU5ERVhfS0VZOiAnZmFpc3MtaW5kZXgvcGFja2FnZXMuaW5kZXgnLFxuICAgICAgICBWRUNUT1JfTUFQUElOR19LRVk6ICdmYWlzcy1pbmRleC9wYWNrYWdlLW1hcHBpbmcuanNvbicsXG4gICAgICB9LFxuICAgIH0pO1xuXG4gICAgLy8gQVBJIEdhdGV3YXlcbiAgICB0aGlzLmFwaSA9IG5ldyBhcGlnYXRld2F5LlJlc3RBcGkodGhpcywgJ1NlYXJjaEFwaUdhdGV3YXknLCB7XG4gICAgICByZXN0QXBpTmFtZTogJ2Zkbml4LXNlYXJjaC1hcGktZ2F0ZXdheScsXG4gICAgICBkZXNjcmlwdGlvbjogJ0FQSSBHYXRld2F5IGZvciBmZG5peCBoeWJyaWQgc2VhcmNoIGVuZ2luZScsXG4gICAgICBkZWZhdWx0Q29yc1ByZWZsaWdodE9wdGlvbnM6IHtcbiAgICAgICAgYWxsb3dPcmlnaW5zOiBhcGlnYXRld2F5LkNvcnMuQUxMX09SSUdJTlMsXG4gICAgICAgIGFsbG93TWV0aG9kczogYXBpZ2F0ZXdheS5Db3JzLkFMTF9NRVRIT0RTLFxuICAgICAgICBhbGxvd0hlYWRlcnM6IFsnQ29udGVudC1UeXBlJywgJ1gtQW16LURhdGUnLCAnQXV0aG9yaXphdGlvbicsICdYLUFwaS1LZXknXSxcbiAgICAgIH0sXG4gICAgICBkZXBsb3lPcHRpb25zOiB7XG4gICAgICAgIHN0YWdlTmFtZTogJ3YxJyxcbiAgICAgICAgbG9nZ2luZ0xldmVsOiBhcGlnYXRld2F5Lk1ldGhvZExvZ2dpbmdMZXZlbC5JTkZPLFxuICAgICAgICBkYXRhVHJhY2VFbmFibGVkOiB0cnVlLFxuICAgICAgICBtZXRyaWNzRW5hYmxlZDogdHJ1ZSxcbiAgICAgIH0sXG4gICAgfSk7XG5cbiAgICAvLyBMYW1iZGEgaW50ZWdyYXRpb25cbiAgICBjb25zdCBsYW1iZGFJbnRlZ3JhdGlvbiA9IG5ldyBhcGlnYXRld2F5LkxhbWJkYUludGVncmF0aW9uKHRoaXMuc2VhcmNoRnVuY3Rpb24sIHtcbiAgICAgIHByb3h5OiB0cnVlLFxuICAgICAgYWxsb3dUZXN0SW52b2tlOiB0cnVlLFxuICAgIH0pO1xuXG4gICAgLy8gQVBJIHJlc291cmNlcyBhbmQgbWV0aG9kc1xuICAgIGNvbnN0IHNlYXJjaFJlc291cmNlID0gdGhpcy5hcGkucm9vdC5hZGRSZXNvdXJjZSgnc2VhcmNoJyk7XG4gICAgc2VhcmNoUmVzb3VyY2UuYWRkTWV0aG9kKCdHRVQnLCBsYW1iZGFJbnRlZ3JhdGlvbiwge1xuICAgICAgcmVxdWVzdFBhcmFtZXRlcnM6IHtcbiAgICAgICAgJ21ldGhvZC5yZXF1ZXN0LnF1ZXJ5c3RyaW5nLnEnOiB0cnVlLFxuICAgICAgICAnbWV0aG9kLnJlcXVlc3QucXVlcnlzdHJpbmcubGltaXQnOiBmYWxzZSxcbiAgICAgICAgJ21ldGhvZC5yZXF1ZXN0LnF1ZXJ5c3RyaW5nLm9mZnNldCc6IGZhbHNlLFxuICAgICAgICAnbWV0aG9kLnJlcXVlc3QucXVlcnlzdHJpbmcubGljZW5zZSc6IGZhbHNlLFxuICAgICAgICAnbWV0aG9kLnJlcXVlc3QucXVlcnlzdHJpbmcuY2F0ZWdvcnknOiBmYWxzZSxcbiAgICAgIH0sXG4gICAgICBtZXRob2RSZXNwb25zZXM6IFtcbiAgICAgICAge1xuICAgICAgICAgIHN0YXR1c0NvZGU6ICcyMDAnLFxuICAgICAgICB9LFxuICAgICAgICB7XG4gICAgICAgICAgc3RhdHVzQ29kZTogJzQwMCcsXG4gICAgICAgIH0sXG4gICAgICAgIHtcbiAgICAgICAgICBzdGF0dXNDb2RlOiAnNTAwJyxcbiAgICAgICAgfSxcbiAgICAgIF0sXG4gICAgfSk7XG5cbiAgICAvLyBIZWFsdGggY2hlY2sgZW5kcG9pbnRcbiAgICBjb25zdCBoZWFsdGhSZXNvdXJjZSA9IHRoaXMuYXBpLnJvb3QuYWRkUmVzb3VyY2UoJ2hlYWx0aCcpO1xuICAgIGNvbnN0IGhlYWx0aEludGVncmF0aW9uID0gbmV3IGFwaWdhdGV3YXkuTW9ja0ludGVncmF0aW9uKHtcbiAgICAgIGludGVncmF0aW9uUmVzcG9uc2VzOiBbXG4gICAgICAgIHtcbiAgICAgICAgICBzdGF0dXNDb2RlOiAnMjAwJyxcbiAgICAgICAgICByZXNwb25zZVRlbXBsYXRlczoge1xuICAgICAgICAgICAgJ2FwcGxpY2F0aW9uL2pzb24nOiBKU09OLnN0cmluZ2lmeSh7XG4gICAgICAgICAgICAgIHN0YXR1czogJ2hlYWx0aHknLFxuICAgICAgICAgICAgICB0aW1lc3RhbXA6ICckY29udGV4dC5yZXF1ZXN0VGltZScsXG4gICAgICAgICAgICAgIHZlcnNpb246ICcxLjAuMCcsXG4gICAgICAgICAgICB9KSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgXSxcbiAgICAgIHJlcXVlc3RUZW1wbGF0ZXM6IHtcbiAgICAgICAgJ2FwcGxpY2F0aW9uL2pzb24nOiAne1wic3RhdHVzQ29kZVwiOiAyMDB9JyxcbiAgICAgIH0sXG4gICAgfSk7XG5cbiAgICBoZWFsdGhSZXNvdXJjZS5hZGRNZXRob2QoJ0dFVCcsIGhlYWx0aEludGVncmF0aW9uLCB7XG4gICAgICBtZXRob2RSZXNwb25zZXM6IFtcbiAgICAgICAge1xuICAgICAgICAgIHN0YXR1c0NvZGU6ICcyMDAnLFxuICAgICAgICB9LFxuICAgICAgXSxcbiAgICB9KTtcblxuICAgIC8vIFVzYWdlIHBsYW4gZm9yIHJhdGUgbGltaXRpbmdcbiAgICBjb25zdCB1c2FnZVBsYW4gPSB0aGlzLmFwaS5hZGRVc2FnZVBsYW4oJ1NlYXJjaEFwaVVzYWdlUGxhbicsIHtcbiAgICAgIG5hbWU6ICdmZG5peC1zZWFyY2gtdXNhZ2UtcGxhbicsXG4gICAgICBkZXNjcmlwdGlvbjogJ1VzYWdlIHBsYW4gZm9yIGZkbml4IHNlYXJjaCBBUEknLFxuICAgICAgdGhyb3R0bGU6IHtcbiAgICAgICAgcmF0ZUxpbWl0OiAxMDAsXG4gICAgICAgIGJ1cnN0TGltaXQ6IDIwMCxcbiAgICAgIH0sXG4gICAgICBxdW90YToge1xuICAgICAgICBsaW1pdDogMTAwMDAsXG4gICAgICAgIHBlcmlvZDogYXBpZ2F0ZXdheS5QZXJpb2QuREFZLFxuICAgICAgfSxcbiAgICB9KTtcblxuICAgIHVzYWdlUGxhbi5hZGRBcGlTdGFnZSh7XG4gICAgICBzdGFnZTogdGhpcy5hcGkuZGVwbG95bWVudFN0YWdlLFxuICAgIH0pO1xuXG4gICAgLy8gQ2xvdWRXYXRjaCBkYXNoYm9hcmQgZm9yIG1vbml0b3JpbmdcbiAgICAvLyBOb3RlOiBEYXNoYm9hcmQgY3JlYXRpb24gd291bGQgYmUgYWRkZWQgaGVyZSBpZiBuZWVkZWRcbiAgfVxufSJdfQ==