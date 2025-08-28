import { Stack, StackProps, RemovalPolicy, Duration, CfnOutput, Arn, ArnFormat, Fn } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as path from 'path';

export class FdnixDatabaseStack extends Stack {
  public readonly artifactsBucket: s3.Bucket;
  public readonly databaseLayer: lambda.LayerVersion;
  public readonly databaseAccessRole: iam.Role;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // S3 bucket for pipeline artifacts (LanceDB file storage)
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

    // Lambda Layer for minified LanceDB file
    // This layer will contain the minified LanceDB dataset at /opt/fdnix/fdnix.lancedb/
    // Initial version with empty layer - will be updated by pipeline
    this.databaseLayer = new lambda.LayerVersion(this, 'DatabaseLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, 'empty-layer')),
      description: 'Minified LanceDB dataset optimized for Lambda with search indexes and essential data only',
      compatibleRuntimes: [lambda.Runtime.PROVIDED_AL2023],
      compatibleArchitectures: [lambda.Architecture.X86_64],
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
          // Unversioned ARNs for PublishLayerVersion and ListLayerVersions
          dbLayerUnversionedArn,
        ],
      }),
    );

    // Note: Bedrock permissions are granted in the Search API stack to the Lambda execution role

    // Outputs
    new CfnOutput(this, 'ArtifactsBucketName', {
      value: this.artifactsBucket.bucketName,
      description: 'S3 bucket for pipeline artifacts (stores both main and minified databases)',
      exportName: 'FdnixArtifactsBucketName',
    });

    new CfnOutput(this, 'DatabaseLayerArn', {
      value: this.databaseLayer.layerVersionArn,
      description: 'ARN of the minified LanceDB Lambda Layer',
      exportName: 'FdnixDatabaseLayerArn',
    });

  }
}
