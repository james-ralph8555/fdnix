import { Stack, StackProps, Duration, CfnOutput, Fn } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as path from 'path';
import * as fs from 'fs';
import { FdnixFrontendStack } from './frontend-stack';
import { FdnixSearchApiStack } from './search-api-stack';

export interface FdnixCloudFrontStackProps extends StackProps {
  frontendStack: FdnixFrontendStack;
  searchApiStack: FdnixSearchApiStack;
  certificateArn: string;
}

export class FdnixCloudFrontStack extends Stack {
  public readonly distribution: cloudfront.Distribution;
  public readonly oac: cloudfront.S3OriginAccessControl;

  constructor(scope: Construct, id: string, props: FdnixCloudFrontStackProps) {
    super(scope, id, props);

    const { frontendStack, searchApiStack, certificateArn } = props;

    // Import processed files bucket from DatabaseStack outputs
    const processedFilesBucketName = Fn.importValue('FdnixProcessedFilesBucketName');
    const processedFilesBucket = s3.Bucket.fromBucketName(this, 'ProcessedFilesBucket', processedFilesBucketName);

    // Import frontend hosting bucket from FrontendStack outputs
    const frontendBucketName = Fn.importValue('FdnixFrontendBucketName');
    const frontendHostingBucket = s3.Bucket.fromBucketName(this, 'FrontendHostingBucket', frontendBucketName);

    // Origin Access Control for CloudFront
    this.oac = new cloudfront.S3OriginAccessControl(this, 'OriginAccessControl', {
      description: 'Origin Access Control for fdnix frontend',
    });

    // CloudFront distribution
    const defaultBehavior: cloudfront.BehaviorOptions = {
      origin: origins.S3BucketOrigin.withOriginAccessControl(frontendHostingBucket, {
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
        '/nodes/*': s3Behavior,
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
      },
      certificate: acm.Certificate.fromCertificateArn(this, 'ImportedCertificate', certificateArn),
      domainNames: [domainName, `www.${domainName}`],
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      enabled: true,
    });

    // Grant CloudFront OAC access to processed files bucket for dependency graph files
    processedFilesBucket.grantRead(
      new iam.ServicePrincipal('cloudfront.amazonaws.com'),
      'nodes/*'
    );

    // DNS is managed by Cloudflare
    // After deployment, configure Cloudflare DNS to point to the CloudFront distribution:
    // - A record: fdnix.com -> <CloudFront distribution domain>
    // - CNAME record: www.fdnix.com -> <CloudFront distribution domain>
    // The distribution domain will be available in the stack outputs

    // Outputs
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