import { Stack, StackProps, Duration } from 'aws-cdk-lib';
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
      compatibleRuntimes: [lambda.Runtime.NODEJS_22_X],
      description: 'Dependencies for fdnix search API including Faiss bindings',
    });

    // Lambda function for hybrid search API
    this.searchFunction = new lambda.Function(this, 'SearchFunction', {
      functionName: 'fdnix-search-api',
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../search-lambda/dist')),
      timeout: Duration.seconds(30),
      memorySize: 1024,
      role: this.lambdaExecutionRole,
      layers: [dependenciesLayer],
      logGroup,
      environment: {
        DYNAMODB_TABLE: databaseStack.packagesTable.tableName,
        S3_BUCKET: databaseStack.vectorIndexBucket.bucketName,
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
