import { Stack, StackProps, RemovalPolicy, Duration } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';

export class FdnixDatabaseStack extends Stack {
  public readonly packagesTable: dynamodb.Table;
  public readonly vectorIndexBucket: s3.Bucket;
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
      bucketName: 'fdnix-vec',
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