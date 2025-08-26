import { Stack, StackProps, RemovalPolicy, Duration } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as certificatemanager from 'aws-cdk-lib/aws-certificatemanager';
import * as path from 'path';
import { FdnixSearchApiStack } from './search-api-stack';

export interface FdnixFrontendStackProps extends StackProps {
  searchApiStack: FdnixSearchApiStack;
  domainName?: string;
}

export class FdnixFrontendStack extends Stack {
  public readonly hostingBucket: s3.Bucket;
  public readonly distribution: cloudfront.Distribution;
  public readonly oac: cloudfront.OriginAccessControl;

  constructor(scope: Construct, id: string, props: FdnixFrontendStackProps) {
    super(scope, id, props);

    const { searchApiStack, domainName } = props;

    // S3 bucket for static site hosting
    this.hostingBucket = new s3.Bucket(this, 'FrontendHostingBucket', {
      bucketName: 'fdnix-frontend-hosting',
      removalPolicy: RemovalPolicy.RETAIN,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      versioned: true,
      lifecycleRules: [{
        id: 'delete-old-versions',
        noncurrentVersionExpiration: Duration.days(30),
      }],
    });

    // Origin Access Control for CloudFront
    this.oac = new cloudfront.OriginAccessControl(this, 'OriginAccessControl', {
      originAccessControlName: 'fdnix-oac',
      description: 'Origin Access Control for fdnix frontend',
      originAccessControlOriginType: cloudfront.OriginAccessControlOriginType.S3,
      signing: {
        signingBehavior: cloudfront.SigningBehavior.ALWAYS,
        signingProtocol: cloudfront.SigningProtocol.SIGV4,
      },
    });

    // SSL Certificate (if domain is provided)
    // For fdnix.com, create certificate for CloudFront (must be in us-east-1)
    let certificate: certificatemanager.Certificate | undefined;
    if (domainName) {
      certificate = new certificatemanager.Certificate(this, 'SslCertificate', {
        domainName,
        subjectAlternativeNames: [`www.${domainName}`],
        certificateName: 'fdnix-ssl-certificate',
        validation: certificatemanager.CertificateValidation.fromDns(),
      });
    }

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
      originRequestPolicy: cloudfront.OriginRequestPolicy.CORS_S3_ORIGIN,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
    };

    this.distribution = new cloudfront.Distribution(this, 'CloudFrontDistribution', {
      distributionName: 'fdnix-cdn',
      comment: 'CloudFront distribution for fdnix frontend',
      defaultBehavior,
      additionalBehaviors: {
        '/api/*': apiBehavior,
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
      domainNames: domainName ? [domainName, `www.${domainName}`] : undefined,
      certificate,
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      enabled: true,
      geoRestriction: cloudfront.GeoRestriction.allowlist('US', 'CA', 'EU', 'GB'),
    });

    // Bucket policy to allow CloudFront access via OAC
    this.hostingBucket.addToResourcePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
      actions: ['s3:GetObject'],
      resources: [`${this.hostingBucket.bucketArn}/*`],
      conditions: {
        StringEquals: {
          'AWS:SourceArn': `arn:aws:cloudfront::${this.account}:distribution/${this.distribution.distributionId}`,
        },
      },
    }));

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
      ephemeralStorageSize: 1024,
    });

    // Cache invalidation function for future CI/CD integration
    const invalidationRole = new iam.Role(this, 'InvalidationRole', {
      roleName: 'fdnix-cache-invalidation-role',
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
  }
}