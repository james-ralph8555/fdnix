import { Stack, StackProps, Duration, CfnOutput } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';
import { FdnixDatabaseStack } from './database-stack';

export interface FdnixSearchApiStackProps extends StackProps {
  databaseStack: FdnixDatabaseStack;
}

export class FdnixSearchApiStack extends Stack {
  public readonly searchFunction: lambda.Function;
  public readonly api: apigateway.RestApi;
  public readonly lambdaExecutionRole: iam.Role;

  constructor(scope: Construct, id: string, props: FdnixSearchApiStackProps) {
    super(scope, id, props);

    const { databaseStack } = props;

    // IAM role for Lambda execution
    this.lambdaExecutionRole = new iam.Role(this, 'LambdaExecutionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Bedrock permissions for real-time embedding generation
    const bedrockModelId = 'amazon.titan-embed-text-v2:0';
    const bedrockModelArn = `arn:aws:bedrock:${Stack.of(this).region}::foundation-model/${bedrockModelId}`;
    this.lambdaExecutionRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
      ],
      resources: [bedrockModelArn],
    }));

    // Lambda function for hybrid search API
    // Implemented in C++ using the custom runtime (PROVIDED_AL2023).
    // The build places a `bootstrap` binary in `packages/search-lambda/dist`.
    this.searchFunction = new lambda.Function(this, 'SearchFunction', {
      runtime: lambda.Runtime.PROVIDED_AL2023,
      architecture: lambda.Architecture.ARM_64,
      handler: 'bootstrap',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../search-lambda/dist')),
      timeout: Duration.seconds(30),
      memorySize: 1024,
      role: this.lambdaExecutionRole,
      layers: [databaseStack.databaseLayer, databaseStack.duckdbLibraryLayer],
      environment: {
        DUCKDB_PATH: '/opt/fdnix/fdnix.duckdb',
        BEDROCK_MODEL_ID: bedrockModelId,
        BEDROCK_OUTPUT_DIMENSIONS: '256',
      },
    });

    // CloudWatch Log Group for Lambda with retention aligned to function name
    new logs.LogGroup(this, 'SearchLambdaLogGroup', {
      logGroupName: `/aws/lambda/${this.searchFunction.functionName}`,
      retention: logs.RetentionDays.ONE_MONTH,
    });

    // API Gateway
    this.api = new apigateway.RestApi(this, 'SearchApiGateway', {
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

    // Outputs
    new CfnOutput(this, 'ApiUrl', {
      value: this.api.url,
      description: 'URL of the Search API Gateway',
      exportName: 'FdnixSearchApiUrl',
    });

    new CfnOutput(this, 'SearchFunctionName', {
      value: this.searchFunction.functionName,
      description: 'Name of the search Lambda function',
      exportName: 'FdnixSearchFunctionName',
    });

    new CfnOutput(this, 'SearchFunctionArn', {
      value: this.searchFunction.functionArn,
      description: 'ARN of the search Lambda function',
      exportName: 'FdnixSearchFunctionArn',
    });
  }
}
