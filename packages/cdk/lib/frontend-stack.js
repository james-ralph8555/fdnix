"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.FdnixFrontendStack = void 0;
const aws_cdk_lib_1 = require("aws-cdk-lib");
const s3 = __importStar(require("aws-cdk-lib/aws-s3"));
const cloudfront = __importStar(require("aws-cdk-lib/aws-cloudfront"));
const origins = __importStar(require("aws-cdk-lib/aws-cloudfront-origins"));
const s3deploy = __importStar(require("aws-cdk-lib/aws-s3-deployment"));
const iam = __importStar(require("aws-cdk-lib/aws-iam"));
const certificatemanager = __importStar(require("aws-cdk-lib/aws-certificatemanager"));
const path = __importStar(require("path"));
class FdnixFrontendStack extends aws_cdk_lib_1.Stack {
    hostingBucket;
    distribution;
    oac;
    constructor(scope, id, props) {
        super(scope, id, props);
        const { searchApiStack, domainName } = props;
        // S3 bucket for static site hosting
        this.hostingBucket = new s3.Bucket(this, 'FrontendHostingBucket', {
            bucketName: 'fdnix-frontend-hosting',
            removalPolicy: aws_cdk_lib_1.RemovalPolicy.RETAIN,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            encryption: s3.BucketEncryption.S3_MANAGED,
            versioned: true,
            lifecycleRules: [{
                    id: 'delete-old-versions',
                    noncurrentVersionExpiration: aws_cdk_lib_1.Duration.days(30),
                }],
        });
        // Origin Access Control for CloudFront
        this.oac = new cloudfront.S3OriginAccessControl(this, 'OriginAccessControl', {
            originAccessControlName: 'fdnix-oac',
            description: 'Origin Access Control for fdnix frontend',
        });
        // SSL Certificate (if domain is provided)
        // For fdnix.com, create certificate for CloudFront (must be in us-east-1)
        let certificate;
        if (domainName) {
            certificate = new certificatemanager.Certificate(this, 'SslCertificate', {
                domainName,
                subjectAlternativeNames: [`www.${domainName}`],
                certificateName: 'fdnix-ssl-certificate',
                validation: certificatemanager.CertificateValidation.fromDns(),
            });
        }
        // CloudFront distribution
        const defaultBehavior = {
            origin: origins.S3BucketOrigin.withOriginAccessControl(this.hostingBucket, {
                originAccessControl: this.oac,
            }),
            viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
            compress: true,
            allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
        };
        // API behavior for proxying search requests
        const apiBehavior = {
            origin: new origins.RestApiOrigin(searchApiStack.api),
            viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
            originRequestPolicy: cloudfront.OriginRequestPolicy.CORS_S3_ORIGIN,
            allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        };
        this.distribution = new cloudfront.Distribution(this, 'CloudFrontDistribution', {
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
                    ttl: aws_cdk_lib_1.Duration.minutes(5),
                },
                {
                    httpStatus: 403,
                    responseHttpStatus: 200,
                    responsePagePath: '/index.html',
                    ttl: aws_cdk_lib_1.Duration.minutes(5),
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
            ephemeralStorageSize: aws_cdk_lib_1.Size.mebibytes(1024),
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
exports.FdnixFrontendStack = FdnixFrontendStack;
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiZnJvbnRlbmQtc3RhY2suanMiLCJzb3VyY2VSb290IjoiIiwic291cmNlcyI6WyJmcm9udGVuZC1zdGFjay50cyJdLCJuYW1lcyI6W10sIm1hcHBpbmdzIjoiOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7QUFBQSw2Q0FBK0U7QUFFL0UsdURBQXlDO0FBQ3pDLHVFQUF5RDtBQUN6RCw0RUFBOEQ7QUFDOUQsd0VBQTBEO0FBQzFELHlEQUEyQztBQUMzQyx1RkFBeUU7QUFDekUsMkNBQTZCO0FBUTdCLE1BQWEsa0JBQW1CLFNBQVEsbUJBQUs7SUFDM0IsYUFBYSxDQUFZO0lBQ3pCLFlBQVksQ0FBMEI7SUFDdEMsR0FBRyxDQUFtQztJQUV0RCxZQUFZLEtBQWdCLEVBQUUsRUFBVSxFQUFFLEtBQThCO1FBQ3RFLEtBQUssQ0FBQyxLQUFLLEVBQUUsRUFBRSxFQUFFLEtBQUssQ0FBQyxDQUFDO1FBRXhCLE1BQU0sRUFBRSxjQUFjLEVBQUUsVUFBVSxFQUFFLEdBQUcsS0FBSyxDQUFDO1FBRTdDLG9DQUFvQztRQUNwQyxJQUFJLENBQUMsYUFBYSxHQUFHLElBQUksRUFBRSxDQUFDLE1BQU0sQ0FBQyxJQUFJLEVBQUUsdUJBQXVCLEVBQUU7WUFDaEUsVUFBVSxFQUFFLHdCQUF3QjtZQUNwQyxhQUFhLEVBQUUsMkJBQWEsQ0FBQyxNQUFNO1lBQ25DLGlCQUFpQixFQUFFLEVBQUUsQ0FBQyxpQkFBaUIsQ0FBQyxTQUFTO1lBQ2pELFVBQVUsRUFBRSxFQUFFLENBQUMsZ0JBQWdCLENBQUMsVUFBVTtZQUMxQyxTQUFTLEVBQUUsSUFBSTtZQUNmLGNBQWMsRUFBRSxDQUFDO29CQUNmLEVBQUUsRUFBRSxxQkFBcUI7b0JBQ3pCLDJCQUEyQixFQUFFLHNCQUFRLENBQUMsSUFBSSxDQUFDLEVBQUUsQ0FBQztpQkFDL0MsQ0FBQztTQUNILENBQUMsQ0FBQztRQUVILHVDQUF1QztRQUN2QyxJQUFJLENBQUMsR0FBRyxHQUFHLElBQUksVUFBVSxDQUFDLHFCQUFxQixD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QzlELFFBQVEsRUFBRSwrQkFBK0I7WUFDekMsU0FBUyxFQUFFLElBQUksR0FBRyxDQUFDLGdCQUFnQixDQUFDLHNCQUFzQixDQUFDO1lBQzNELGVBQWUsRUFBRTtnQkFDZixHQUFHLENBQUMsYUFBYSxDQUFDLHdCQUF3QixDQUFDLDBDQUEwQyxDQUFDO2FBQ3ZGO1NBQ0YsQ0FBQyxDQUFDO1FBRUgsZ0JBQWdCLENBQUMsV0FBVyxDQUFDLElBQUksR0FBRyxDQUFDLGVBQWUsQ0FBQztZQUNuRCxNQUFNLEVBQUUsR0FBRyxDQUFDLE1BQU0sQ0FBQyxLQUFLO1lBQ3hCLE9BQU8sRUFBRTtnQkFDUCwrQkFBK0I7YUFDaEM7WUFDRCxTQUFTLEVBQUU7Z0JBQ1QsdUJBQXVCLElBQUksQ0FBQyxPQUFPLGlCQUFpQixJQUFJLENBQUMsWUFBWSxDQUFDLGNBQWMsRUFBRTthQUN2RjtTQUNGLENBQUMsQ0FBQyxDQUFDO0lBQ04sQ0FBQztDQUNGO0FBOUlELGdEQThJQyIsInNvdXJjZXNDb250ZW50IjpbImltcG9ydCB7IFN0YWNrLCBTdGFja1Byb3BzLCBSZW1vdmFsUG9saWN5LCBEdXJhdGlvbiwgU2l6ZSB9IGZyb20gJ2F3cy1jZGstbGliJztcbmltcG9ydCB7IENvbnN0cnVjdCB9IGZyb20gJ2NvbnN0cnVjdHMnO1xuaW1wb3J0ICogYXMgczMgZnJvbSAnYXdzLWNkay1saWIvYXdzLXMzJztcbmltcG9ydCAqIGFzIGNsb3VkZnJvbnQgZnJvbSAnYXdzLWNkay1saWIvYXdzLWNsb3VkZnJvbnQnO1xuaW1wb3J0ICogYXMgb3JpZ2lucyBmcm9tICdhd3MtY2RrLWxpYi9hd3MtY2xvdWRmcm9udC1vcmlnaW5zJztcbmltcG9ydCAqIGFzIHMzZGVwbG95IGZyb20gJ2F3cy1jZGstbGliL2F3cy1zMy1kZXBsb3ltZW50JztcbmltcG9ydCAqIGFzIGlhbSBmcm9tICdhd3MtY2RrLWxpYi9hd3MtaWFtJztcbmltcG9ydCAqIGFzIGNlcnRpZmljYXRlbWFuYWdlciBmcm9tICdhd3MtY2RrLWxpYi9hd3MtY2VydGlmaWNhdGVtYW5hZ2VyJztcbmltcG9ydCAqIGFzIHBhdGggZnJvbSAncGF0aCc7XG5pbXBvcnQgeyBGZG5peFNlYXJjaEFwaVN0YWNrIH0gZnJvbSAnLi9zZWFyY2gtYXBpLXN0YWNrJztcblxuZXhwb3J0IGludGVyZmFjZSBGZG5peEZyb250ZW5kU3RhY2tQcm9wcyBleHRlbmRzIFN0YWNrUHJvcHMge1xuICBzZWFyY2hBcGlTdGFjazogRmRuaXhTZWFyY2hBcGlTdGFjaztcbiAgZG9tYWluTmFtZT86IHN0cmluZztcbn1cblxuZXhwb3J0IGNsYXNzIEZkbml4RnJvbnRlbmRTdGFjayBleHRlbmRzIFN0YWNrIHtcbiAgcHVibGljIHJlYWRvbmx5IGhvc3RpbmdCdWNrZXQ6IHMzLkJ1Y2tldDtcbiAgcHVibGljIHJlYWRvbmx5IGRpc3RyaWJ1dGlvbjogY2xvdWRmcm9udC5EaXN0cmlidXRpb247XG4gIHB1YmxpYyByZWFkb25seSBvYWM6IGNsb3VkZnJvbnQuUzNPcmlnaW5BY2Nlc3NDb250cm9sO1xuXG4gIGNvbnN0cnVjdG9yKHNjb3BlOiBDb25zdHJ1Y3QsIGlkOiBzdHJpbmcsIHByb3BzOiBGZG5peEZyb250ZW5kU3RhY2tQcm9wcykge1xuICAgIHN1cGVyKHNjb3BlLCBpZCwgcHJvcHMpO1xuXG4gICAgY29uc3QgeyBzZWFyY2hBcGlTdGFjaywgZG9tYWluTmFtZSB9ID0gcHJvcHM7XG5cbiAgICAvLyBTMyBidWNrZXQgZm9yIHN0YXRpYyBzaXRlIGhvc3RpbmdcbiAgICB0aGlzLmhvc3RpbmdCdWNrZXQgPSBuZXcgczMuQnVja2V0KHRoaXMsICdGcm9udGVuZEhvc3RpbmdCdWNrZXQnLCB7XG4gICAgICBidWNrZXROYW1lOiAnZmRuaXgtZnJvbnRlbmQtaG9zdGluZycsXG4gICAgICByZW1vdmFsUG9saWN5OiBSZW1vdmFsUG9saWN5LlJFVEFJTixcbiAgICAgIGJsb2NrUHVibGljQWNjZXNzOiBzMy5CbG9ja1B1YmxpY0FjY2Vzcy5CTE9DS19BTEwsXG4gICAgICBlbmNyeXB0aW9uOiBzMy5CdWNrZXRFbmNyeXB0aW9uLlMzX01BTkFHRUQsXG4gICAgICB2ZXJzaW9uZWQ6IHRydWUsXG4gICAgICBsaWZlY3ljbGVSdWxlczogW3tcbiAgICAgICAgaWQ6ICdkZWxldGUtb2xkLXZlcnNpb25zJyxcbiAgICAgICAgbm9uY3VycmVudFZlcnNpb25FeHBpcmF0aW9uOiBEdXJhdGlvbi5kYXlzKDMwKSxcbiAgICAgIH1dLFxuICAgIH0pO1xuXG4gICAgLy8gT3JpZ2luIEFjY2VzcyBDb250cm9sIGZvciBDbG91ZEZyb250XG4gICAgdGhpcy5vYWMgPSBuZXcgY2xvdWRmcm9udC5TM09yaWdpbkFjY2Vzc0NvbnRyb2wodGhpcywgJ09yaWdpbkFjY2Vzc0NvbnRyb2wnLCB7XG4gICAgICBvcmlnaW5BY2Nlc3NDb250cm9sTmFtZTogJ2Zkbml4LW9hYycsXG4gICAgICBkZXNjcmlwdGlvbjogJ09yaWdpbiBBY2Nlc3MgQ29udHJvbCBmb3IgZmRuaXggZnJvbnRlbmQnLFxuICAgIH0pO1xuXG4gICAgLy8gU1NMIENlcnRpZmljYXRlIChpZiBkb21haW4gaXMgcHJvdmlkZWQpXG4gICAgLy8gRm9yIGZkbml4LmNvbSwgY3JlYXRlIGNlcnRpZmljYXRlIGZvciBDbG91ZEZyb250IChtdXN0IGJlIGluIHVzLWVhc3QtMSlcbiAgICBsZXQgY2VydGlmaWNhdGU6IGNlcnRpZmljYXRlbWFuYWdlci5DZXJ0aWZpY2F0ZSB8IHVuZGVmaW5lZDtcbiAgICBpZiAoZG9tYWluTmFtZSkge1xuICAgICAgY2VydGlmaWNhdGUgPSBuZXcgY2VydGlmaWNhdGVtYW5hZ2VyLkNlcnRpZmljYXRlKHRoaXMsICdTc2xDZXJ0aWZpY2F0ZScsIHtcbiAgICAgICAgZG9tYWluTmFtZSxcbiAgICAgICAgc3ViamVjdEFsdGVybmF0aXZlTmFtZXM6IFtgd3d3LiR7ZG9tYWluTmFtZX1gXSxcbiAgICAgICAgY2VydGlmaWNhdGVOYW1lOiAnZmRuaXgtc3NsLWNlcnRpZmljYXRlJyxcbiAgICAgICAgdmFsaWRhdGlvbjogY2VydGlmaWNhdGVtYW5hZ2VyLkNlcnRpZmljYXRlVmFsaWRhdGlvbi5mcm9tRG5zKCksXG4gICAgICB9KTtcbiAgICB9XG5cbiAgICAvLyBDbG91ZEZyb250IGRpc3RyaWJ1dGlvblxuICAgIGNvbnN0IGRlZmF1bHRCZWhhdmlvcjogY2xvdWRmcm9udC5CZWhhdmlvck9wdGlvbnMgPSB7XG4gICAgICBvcmlnaW46IG9yaWdpbnMuUzNCdWNrZXRPcmlnaW4ud2l0aE9yaWdpbkFjY2Vzc0NvbnRyb2wodGhpcy5ob3N0aW5nQnVja2V0LCB7XG4gICAgICAgIG9yaWdpbkFjY2Vzc0NvbnRyb2w6IHRoaXMub2FjLFxuICAgICAgfSksXG4gICAgICB2aWV3ZXJQcm90b2NvbFBvbGljeTogY2xvdWRmcm9udC5WaWV3ZXJQcm90b2NvbFBvbGljeS5SRURJUkVDVF9UT19IVFRQUyxcbiAgICAgIGNhY2hlUG9saWN5OiBjbG91ZGZyb250LkNhY2hlUG9saWN5LkNBQ0hJTkdfT1BUSU1JWkVELFxuICAgICAgY29tcHJlc3M6IHRydWUsXG4gICAgICBhbGxvd2VkTWV0aG9kczogY2xvdWRmcm9udC5BbGxvd2VkTWV0aG9kcy5BTExPV19HRVRfSEVBRF9PUFRJT05TLFxuICAgIH07XG5cbiAgICAvLyBBUEkgYmVoYXZpb3IgZm9yIHByb3h5aW5nIHNlYXJjaCByZXF1ZXN0c1xuICAgIGNvbnN0IGFwaUJlaGF2aW9yOiBjbG91ZGZyb250LkJlaGF2aW9yT3B0aW9ucyA9IHtcbiAgICAgIG9yaWdpbjogbmV3IG9yaWdpbnMuUmVzdEFwaU9yaWdpbihzZWFyY2hBcGlTdGFjay5hcGkpLFxuICAgICAgdmlld2VyUHJvdG9jb2xQb2xpY3k6IGNsb3VkZnJvbnQuVmlld2VyUHJvdG9jb2xQb2xpY3kuUkVESVJFQ1RfVE9fSFRUUFMsXG4gICAgICBjYWNoZVBvbGljeTogY2xvdWRmcm9udC5DYWNoZVBvbGljeS5DQUNISU5HX0RJU0FCTEVELFxuICAgICAgb3JpZ2luUmVxdWVzdFBvbGljeTogY2xvdWRmcm9udC5PcmlnaW5SZXF1ZXN0UG9saWN5LkNPUlNfUzNfT1JJR0lOLFxuICAgICAgYWxsb3dlZE1ldGhvZHM6IGNsb3VkZnJvbnQuQWxsb3dlZE1ldGhvZHMuQUxMT1dfQUxMLFxuICAgIH07XG5cbiAgICB0aGlzLmRpc3RyaWJ1dGlvbiA9IG5ldyBjbG91ZGZyb250LkRpc3RyaWJ1dGlvbih0aGlzLCAnQ2xvdWRGcm9udERpc3RyaWJ1dGlvbicsIHtcbiAgICAgIGNvbW1lbnQ6ICdDbG91ZEZyb250IGRpc3RyaWJ1dGlvbiBmb3IgZmRuaXggZnJvbnRlbmQnLFxuICAgICAgZGVmYXVsdEJlaGF2aW9yLFxuICAgICAgYWRkaXRpb25hbEJlaGF2aW9yczoge1xuICAgICAgICAnL2FwaS8qJzogYXBpQmVoYXZpb3IsXG4gICAgICB9LFxuICAgICAgZGVmYXVsdFJvb3RPYmplY3Q6ICdpbmRleC5odG1sJyxcbiAgICAgIGVycm9yUmVzcG9uc2VzOiBbXG4gICAgICAgIHtcbiAgICAgICAgICBodHRwU3RhdHVzOiA0MDQsXG4gICAgICAgICAgcmVzcG9uc2VIdHRwU3RhdHVzOiAyMDAsXG4gICAgICAgICAgcmVzcG9uc2VQYWdlUGF0aDogJy9pbmRleC5odG1sJyxcbiAgICAgICAgICB0dGw6IER1cmF0aW9uLm1pbnV0ZXMoNSksXG4gICAgICAgIH0sXG4gICAgICAgIHtcbiAgICAgICAgICBodHRwU3RhdHVzOiA0MDMsXG4gICAgICAgICAgcmVzcG9uc2VIdHRwU3RhdHVzOiAyMDAsXG4gICAgICAgICAgcmVzcG9uc2VQYWdlUGF0aDogJy9pbmRleC5odG1sJyxcbiAgICAgICAgICB0dGw6IER1cmF0aW9uLm1pbnV0ZXMoNSksXG4gICAgICAgIH0sXG4gICAgICBdLFxuICAgICAgZG9tYWluTmFtZXM6IGRvbWFpbk5hbWUgPyBbZG9tYWluTmFtZSwgYHd3dy4ke2RvbWFpbk5hbWV9YF0gOiB1bmRlZmluZWQsXG4gICAgICBjZXJ0aWZpY2F0ZSxcbiAgICAgIG1pbmltdW1Qcm90b2NvbFZlcnNpb246IGNsb3VkZnJvbnQuU2VjdXJpdHlQb2xpY3lQcm90b2NvbC5UTFNfVjFfMl8yMDIxLFxuICAgICAgaHR0cFZlcnNpb246IGNsb3VkZnJvbnQuSHR0cFZlcnNpb24uSFRUUDJfQU5EXzMsXG4gICAgICBwcmljZUNsYXNzOiBjbG91ZGZyb250LlByaWNlQ2xhc3MuUFJJQ0VfQ0xBU1NfMTAwLFxuICAgICAgZW5hYmxlZDogdHJ1ZSxcbiAgICAgIGdlb1Jlc3RyaWN0aW9uOiBjbG91ZGZyb250Lkdlb1Jlc3RyaWN0aW9uLmFsbG93bGlzdCgnVVMnLCAnQ0EnLCAnRVUnLCAnR0InKSxcbiAgICB9KTtcblxuICAgIC8vIEJ1Y2tldCBwb2xpY3kgdG8gYWxsb3cgQ2xvdWRGcm9udCBhY2Nlc3MgdmlhIE9BQ1xuICAgIHRoaXMuaG9zdGluZ0J1Y2tldC5hZGRUb1Jlc291cmNlUG9saWN5KG5ldyBpYW0uUG9saWN5U3RhdGVtZW50KHtcbiAgICAgIGVmZmVjdDogaWFtLkVmZmVjdC5BTExPVyxcbiAgICAgIHByaW5jaXBhbHM6IFtuZXcgaWFtLlNlcnZpY2VQcmluY2lwYWwoJ2Nsb3VkZnJvbnQuYW1hem9uYXdzLmNvbScpXSxcbiAgICAgIGFjdGlvbnM6IFsnczM6R2V0T2JqZWN0J10sXG4gICAgICByZXNvdXJjZXM6IFtgJHt0aGlzLmhvc3RpbmdCdWNrZXQuYnVja2V0QXJufS8qYF0sXG4gICAgICBjb25kaXRpb25zOiB7XG4gICAgICAgIFN0cmluZ0VxdWFsczoge1xuICAgICAgICAgICdBV1M6U291cmNlQXJuJzogYGFybjphd3M6Y2xvdWRmcm9udDo6JHt0aGlzLmFjY291bnR9OmRpc3RyaWJ1dGlvbi8ke3RoaXMuZGlzdHJpYnV0aW9uLmRpc3RyaWJ1dGlvbklkfWAsXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgIH0pKTtcblxuICAgIC8vIEROUyBpcyBtYW5hZ2VkIGJ5IENsb3VkZmxhcmVcbiAgICAvLyBBZnRlciBkZXBsb3ltZW50LCBjb25maWd1cmUgQ2xvdWRmbGFyZSBETlMgdG8gcG9pbnQgdG8gdGhlIENsb3VkRnJvbnQgZGlzdHJpYnV0aW9uOlxuICAgIC8vIC0gQSByZWNvcmQ6IGZkbml4LmNvbSAtPiA8Q2xvdWRGcm9udCBkaXN0cmlidXRpb24gZG9tYWluPlxuICAgIC8vIC0gQ05BTUUgcmVjb3JkOiB3d3cuZmRuaXguY29tIC0+IDxDbG91ZEZyb250IGRpc3RyaWJ1dGlvbiBkb21haW4+XG4gICAgLy8gVGhlIGRpc3RyaWJ1dGlvbiBkb21haW4gd2lsbCBiZSBhdmFpbGFibGUgaW4gdGhlIHN0YWNrIG91dHB1dHNcblxuICAgIC8vIFMzIGRlcGxveW1lbnQgZm9yIHN0YXRpYyBhc3NldHMgKHBsYWNlaG9sZGVyIGZvciBub3cpXG4gICAgLy8gVGhpcyB3aWxsIGJlIHJlcGxhY2VkIHdpdGggYWN0dWFsIGJ1aWxkIGFydGlmYWN0cyBpbiBQaGFzZSA0XG4gICAgbmV3IHMzZGVwbG95LkJ1Y2tldERlcGxveW1lbnQodGhpcywgJ0RlcGxveVdlYnNpdGUnLCB7XG4gICAgICBzb3VyY2VzOiBbXG4gICAgICAgIHMzZGVwbG95LlNvdXJjZS5hc3NldChwYXRoLmpvaW4oX19kaXJuYW1lLCAnLi4vLi4vZnJvbnRlbmQvZGlzdCcpKSxcbiAgICAgIF0sXG4gICAgICBkZXN0aW5hdGlvbkJ1Y2tldDogdGhpcy5ob3N0aW5nQnVja2V0LFxuICAgICAgZGlzdHJpYnV0aW9uOiB0aGlzLmRpc3RyaWJ1dGlvbixcbiAgICAgIGRpc3RyaWJ1dGlvblBhdGhzOiBbJy8qJ10sXG4gICAgICBtZW1vcnlMaW1pdDogNTEyLFxuICAgICAgZXBoZW1lcmFsU3RvcmFnZVNpemU6IFNpemUubWViaWJ5dGVzKDEwMjQpLFxuICAgIH0pO1xuXG4gICAgLy8gQ2FjaGUgaW52YWxpZGF0aW9uIGZ1bmN0aW9uIGZvciBmdXR1cmUgQ0kvQ0QgaW50ZWdyYXRpb25cbiAgICBjb25zdCBpbnZhbGlkYXRpb25Sb2xlID0gbmV3IGlhbS5Sb2xlKHRoaXMsICdJbnZhbGlkYXRpb25Sb2xlJywge1xuICAgICAgcm9sZU5hbWU6ICdmZG5peC1jYWNoZS1pbnZhbGlkYXRpb24tcm9sZScsXG4gICAgICBhc3N1bWVkQnk6IG5ldyBpYW0uU2VydmljZVByaW5jaXBhbCgnbGFtYmRhLmFtYXpvbmF3cy5jb20nKSxcbiAgICAgIG1hbmFnZWRQb2xpY2llczogW1xuICAgICAgICBpYW0uTWFuYWdlZFBvbGljeS5mcm9tQXdzTWFuYWdlZFBvbGljeU5hbWUoJ3NlcnZpY2Utcm9sZS9BV1NMYW1iZGFCYXNpY0V4ZWN1dGlvblJvbGUnKSxcbiAgICAgIF0sXG4gICAgfSk7XG5cbiAgICBpbnZhbGlkYXRpb25Sb2xlLmFkZFRvUG9saWN5KG5ldyBpYW0uUG9saWN5U3RhdGVtZW50KHtcbiAgICAgIGVmZmVjdDogaWFtLkVmZmVjdC5BTExPVyxcbiAgICAgIGFjdGlvbnM6IFtcbiAgICAgICAgJ2Nsb3VkZnJvbnQ6Q3JlYXRlSW52YWxpZGF0aW9uJyxcbiAgICAgIF0sXG4gICAgICByZXNvdXJjZXM6IFtcbiAgICAgICAgYGFybjphd3M6Y2xvdWRmcm9udDo6JHt0aGlzLmFjY291bnR9OmRpc3RyaWJ1dGlvbi8ke3RoaXMuZGlzdHJpYnV0aW9uLmRpc3RyaWJ1dGlvbklkfWAsXG4gICAgICBdLFxuICAgIH0pKTtcbiAgfVxufSJdfQ==