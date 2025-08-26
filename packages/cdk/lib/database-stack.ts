import { Stack, StackProps, RemovalPolicy, Duration } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as opensearch from 'aws-cdk-lib/aws-opensearchserverless';
import * as iam from 'aws-cdk-lib/aws-iam';

export class FdnixDatabaseStack extends Stack {
  public readonly packagesTable: dynamodb.Table;
  public readonly vectorIndexBucket: s3.Bucket;
  public readonly searchCollection: opensearch.CfnCollection;
  public readonly databaseAccessRole: iam.Role;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // DynamoDB table for package metadata
    this.packagesTable = new dynamodb.Table(this, 'PackagesTable', {
      tableName: 'fdnix-packages',
      partitionKey: { name: 'packageName', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'version', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.RETAIN,
      pointInTimeRecovery: true,
      deletionProtection: true,
    });

    // Add GSI for querying by last updated timestamp
    this.packagesTable.addGlobalSecondaryIndex({
      indexName: 'lastUpdated-index',
      partitionKey: { name: 'lastUpdated', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // S3 bucket for vector index storage (Faiss files)
    this.vectorIndexBucket = new s3.Bucket(this, 'VectorIndexBucket', {
      bucketName: 'fdnix-vector-index',
      versioned: true,
      lifecycleRules: [{
        id: 'delete-old-versions',
        noncurrentVersionExpiration: Duration.days(30),
      }],
      removalPolicy: RemovalPolicy.RETAIN,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
    });

    // OpenSearch Serverless collection for keyword search
    const dataAccessPolicy = new opensearch.CfnAccessPolicy(this, 'DataAccessPolicy', {
      name: 'fdnix-search-data-access',
      type: 'data',
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: 'collection',
              Resource: ['collection/fdnix-search'],
              Permission: [
                'aoss:CreateCollectionItems',
                'aoss:DeleteCollectionItems',
                'aoss:UpdateCollectionItems',
                'aoss:DescribeCollectionItems',
              ],
            },
            {
              ResourceType: 'index',
              Resource: ['index/fdnix-search/*'],
              Permission: [
                'aoss:CreateIndex',
                'aoss:DeleteIndex',
                'aoss:UpdateIndex',
                'aoss:DescribeIndex',
                'aoss:ReadDocument',
                'aoss:WriteDocument',
              ],
            },
          ],
          Principal: [`arn:aws:iam::${this.account}:root`],
        },
      ]),
    });

    const networkPolicy = new opensearch.CfnSecurityPolicy(this, 'NetworkPolicy', {
      name: 'fdnix-search-network',
      type: 'network',
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: 'collection',
              Resource: ['collection/fdnix-search'],
            },
            {
              ResourceType: 'dashboard',
              Resource: ['collection/fdnix-search'],
            },
          ],
          AllowFromPublic: false,
        },
      ]),
    });

    const encryptionPolicy = new opensearch.CfnSecurityPolicy(this, 'EncryptionPolicy', {
      name: 'fdnix-search-encryption',
      type: 'encryption',
      policy: JSON.stringify({
        Rules: [
          {
            ResourceType: 'collection',
            Resource: ['collection/fdnix-search'],
          },
        ],
        AWSOwnedKey: true,
      }),
    });

    this.searchCollection = new opensearch.CfnCollection(this, 'SearchCollection', {
      name: 'fdnix-search',
      description: 'OpenSearch Serverless collection for fdnix keyword search',
      type: 'SEARCH',
    });

    // Ensure policies are created before the collection
    this.searchCollection.addDependency(dataAccessPolicy);
    this.searchCollection.addDependency(networkPolicy);
    this.searchCollection.addDependency(encryptionPolicy);

    // IAM role for database access
    this.databaseAccessRole = new iam.Role(this, 'DatabaseAccessRole', {
      roleName: 'fdnix-database-access-role',
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('lambda.amazonaws.com'),
        new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      ),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Grant permissions to the role
    this.packagesTable.grantReadWriteData(this.databaseAccessRole);
    this.vectorIndexBucket.grantReadWrite(this.databaseAccessRole);

    // Grant OpenSearch permissions
    this.databaseAccessRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'aoss:APIAccessAll',
      ],
      resources: [this.searchCollection.attrArn],
    }));

    // Grant Bedrock access for embeddings
    this.databaseAccessRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock:InvokeModel',
      ],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-english-v3`,
        `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-multilingual-v3`,
      ],
    }));
  }
}