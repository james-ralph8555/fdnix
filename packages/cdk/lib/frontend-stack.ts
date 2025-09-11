import { Stack, StackProps, RemovalPolicy, Duration, Size, CfnOutput, Fn } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
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
  public readonly distribution: cloudfront.Distribution;
  public readonly oac: cloudfront.S3OriginAccessControl;

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

    // Origin Access Control for CloudFront
    this.oac = new cloudfront.S3OriginAccessControl(this, 'OriginAccessControl', {
      description: 'Origin Access Control for fdnix frontend',
    });

    // No custom domain configured here (managed manually post-deploy)

    // CloudFront distribution
    const defaultBehavior: cloudfront.BehaviorOptions = {
      origin: origins.S3BucketOrigin.withOriginAccessControl(this.hostingBucket, {
        originAccessControl: this.oac,
      }),
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      compress: true,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
    };

    // API behavior for proxying search requests
    const apiBehavior: cloudfront.BehaviorOptions = {
      origin: new origins.RestApiOrigin(searchApiStack.api),
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      // Forward common CORS headers/querystrings for API origin
      originRequestPolicy: cloudfront.OriginRequestPolicy.CORS_CUSTOM_ORIGIN,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
    };

    // S3 behavior for dependency graph data (from processed files bucket)
    const s3Behavior: cloudfront.BehaviorOptions = {
      origin: origins.S3BucketOrigin.withOriginAccessControl(processedFilesBucket, {
        originAccessControl: this.oac,
      }),
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      compress: true,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
    };

    this.distribution = new cloudfront.Distribution(this, 'CloudFrontDistribution', {
      comment: 'CloudFront distribution for fdnix frontend',
      defaultBehavior,
      additionalBehaviors: {
        '/api/*': apiBehavior,
        '/graph/*': s3Behavior,
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: Duration.minutes(5),
        },
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: Duration.minutes(5),
        },
      ],
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      enabled: true,
    });

    // No explicit bucket policy needed: S3BucketOrigin.withOriginAccessControl
    // attaches least-privilege policies for the distribution automatically.
    
    // Grant CloudFront OAC access to processed files bucket for dependency graph files
    processedFilesBucket.grantRead(
      new iam.ServicePrincipal('cloudfront.amazonaws.com'),
      'graph/*'
    );

    // DNS is managed by Cloudflare
    // After deployment, configure Cloudflare DNS to point to the CloudFront distribution:
    // - A record: fdnix.com -> <CloudFront distribution domain>
    // - CNAME record: www.fdnix.com -> <CloudFront distribution domain>
    // The distribution domain will be available in the stack outputs

    // S3 deployment for static assets (placeholder for now)
    // This will be replaced with actual build artifacts in Phase 4
    new s3deploy.BucketDeployment(this, 'DeployWebsite', {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, '../../frontend/dist')),
      ],
      destinationBucket: this.hostingBucket,
      distribution: this.distribution,
      distributionPaths: ['/*'],
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

    invalidationRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'cloudfront:CreateInvalidation',
      ],
      resources: [
        `arn:aws:cloudfront::${this.account}:distribution/${this.distribution.distributionId}`,
      ],
    }));

    // Outputs
    new CfnOutput(this, 'FrontendBucketName', {
      value: this.hostingBucket.bucketName,
      description: 'Name of the S3 bucket hosting the frontend',
      exportName: 'FdnixFrontendBucketName',
    });

    new CfnOutput(this, 'FrontendDistributionId', {
      value: this.distribution.distributionId,
      description: 'CloudFront distribution ID for the frontend',
      exportName: 'FdnixFrontendDistributionId',
    });

    new CfnOutput(this, 'FrontendDistributionDomainName', {
      value: this.distribution.domainName,
      description: 'CloudFront distribution domain name',
      exportName: 'FdnixFrontendDistributionDomainName',
    });
  }
}
