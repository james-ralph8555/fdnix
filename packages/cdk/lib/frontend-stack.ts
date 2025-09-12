import { Stack, StackProps, RemovalPolicy, Duration, Size, CfnOutput, Fn } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as path from 'path';
import * as fs from 'fs';
import { FdnixSearchApiStack } from './search-api-stack';

export interface FdnixFrontendStackProps extends StackProps {
  searchApiStack: FdnixSearchApiStack;
}

export class FdnixFrontendStack extends Stack {
  public readonly hostingBucket: s3.IBucket;

  constructor(scope: Construct, id: string, props: FdnixFrontendStackProps) {
    super(scope, id, props);

    const { searchApiStack } = props;

    // Import processed files bucket from DatabaseStack outputs
    const processedFilesBucketName = Fn.importValue('FdnixProcessedFilesBucketName');
    const processedFilesBucket = s3.Bucket.fromBucketName(this, 'ProcessedFilesBucket', processedFilesBucketName);

    // Validate that frontend build exists
    const frontendDistPath = path.join(__dirname, '../../frontend/dist');
    if (!fs.existsSync(frontendDistPath)) {
      throw new Error(
        `Frontend build not found at ${frontendDistPath}. ` +
        'Please run "cd packages/frontend && npm run build" before deploying the frontend stack.'
      );
    }

    // S3 bucket for static site hosting (CDK-managed for idempotency)
    this.hostingBucket = new s3.Bucket(this, 'FrontendHostingBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
      versioned: false,
    });

    // S3 deployment for static assets (placeholder for now)
    // This will be replaced with actual build artifacts in Phase 4
    new s3deploy.BucketDeployment(this, 'DeployWebsite', {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, '../../frontend/dist')),
      ],
      destinationBucket: this.hostingBucket,
      memoryLimit: 512,
      ephemeralStorageSize: Size.mebibytes(1024),
    });

    // Cache invalidation function for future CI/CD integration
    const invalidationRole = new iam.Role(this, 'InvalidationRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Note: CloudFront distribution ID is not available here since it's in a separate stack
    // Invalidation policy will be added to CloudFrontStack when it's created

    // Outputs
    new CfnOutput(this, 'FrontendBucketName', {
      value: this.hostingBucket.bucketName,
      description: 'Name of the S3 bucket hosting the frontend',
      exportName: 'FdnixFrontendBucketName',
    });
  }
}
