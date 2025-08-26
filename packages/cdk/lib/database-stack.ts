import { Stack, StackProps, RemovalPolicy, Duration, CfnOutput, Arn, ArnFormat, Fn } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as path from 'path';

export class FdnixDatabaseStack extends Stack {
  public readonly artifactsBucket: s3.Bucket;
  public readonly databaseLayer: lambda.LayerVersion;
  public readonly duckdbLibraryLayer: lambda.LayerVersion;
  public readonly databaseAccessRole: iam.Role;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // S3 bucket for pipeline artifacts (DuckDB file storage)
    this.artifactsBucket = new s3.Bucket(this, 'ArtifactsBucket', {
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

    // Lambda Layer for DuckDB file
    // This layer will contain the DuckDB file at /opt/fdnix/fdnix.duckdb
    // Initial version with empty layer - will be updated by pipeline
    this.databaseLayer = new lambda.LayerVersion(this, 'DatabaseLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, 'empty-layer')),
      description: 'DuckDB file containing nixpkgs metadata, embeddings, and search indexes',
      compatibleRuntimes: [lambda.Runtime.PROVIDED_AL2023],
      compatibleArchitectures: [lambda.Architecture.ARM_64],
    });

    // Lambda Layer for DuckDB shared library with extensions
    // This layer will contain the DuckDB library with FTS and VSS extensions
    this.duckdbLibraryLayer = new lambda.LayerVersion(this, 'DuckdbLibraryLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, 'duckdb-build')),
      description: 'DuckDB shared library with FTS and VSS extensions for C++ Lambda',
      compatibleRuntimes: [lambda.Runtime.PROVIDED_AL2023],
      compatibleArchitectures: [lambda.Architecture.ARM_64],
    });

    // IAM role for database access
    this.databaseAccessRole = new iam.Role(this, 'DatabaseAccessRole', {
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('lambda.amazonaws.com'),
        new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      ),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Grant S3 permissions for artifacts
    this.artifactsBucket.grantReadWrite(this.databaseAccessRole);

    // Grant Lambda layer permissions for publishing new versions
    // Use safe ARN splitting/formatting to derive unversioned layer ARNs
    const dbLayerComponents = Arn.split(this.databaseLayer.layerVersionArn, ArnFormat.COLON_RESOURCE_NAME);
    const dbLayerName = Fn.select(0, Fn.split(':', dbLayerComponents.resourceName!));
    const dbLayerUnversionedArn = Arn.format(
      {
        service: 'lambda',
        resource: 'layer',
        region: dbLayerComponents.region,
        account: dbLayerComponents.account,
        resourceName: dbLayerName,
        arnFormat: ArnFormat.COLON_RESOURCE_NAME,
      },
      this,
    );

    const libLayerComponents = Arn.split(this.duckdbLibraryLayer.layerVersionArn, ArnFormat.COLON_RESOURCE_NAME);
    const libLayerName = Fn.select(0, Fn.split(':', libLayerComponents.resourceName!));
    const libLayerUnversionedArn = Arn.format(
      {
        service: 'lambda',
        resource: 'layer',
        region: libLayerComponents.region,
        account: libLayerComponents.account,
        resourceName: libLayerName,
        arnFormat: ArnFormat.COLON_RESOURCE_NAME,
      },
      this,
    );

    this.databaseAccessRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'lambda:PublishLayerVersion',
          'lambda:GetLayerVersion',
          'lambda:ListLayerVersions',
        ],
        resources: [
          // Versioned ARNs for GetLayerVersion
          this.databaseLayer.layerVersionArn,
          this.duckdbLibraryLayer.layerVersionArn,
          // Unversioned ARNs for PublishLayerVersion and ListLayerVersions
          dbLayerUnversionedArn,
          libLayerUnversionedArn,
        ],
      }),
    );

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

    // Outputs
    new CfnOutput(this, 'ArtifactsBucketName', {
      value: this.artifactsBucket.bucketName,
      description: 'S3 bucket for pipeline artifacts',
      exportName: 'FdnixArtifactsBucketName',
    });

    new CfnOutput(this, 'DatabaseLayerArn', {
      value: this.databaseLayer.layerVersionArn,
      description: 'ARN of the DuckDB Lambda Layer',
      exportName: 'FdnixDatabaseLayerArn',
    });

    new CfnOutput(this, 'DuckdbLibraryLayerArn', {
      value: this.duckdbLibraryLayer.layerVersionArn,
      description: 'ARN of the DuckDB library Lambda Layer',
      exportName: 'FdnixDuckdbLibraryLayerArn',
    });
  }
}
