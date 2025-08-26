#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { FdnixDatabaseStack } from '../lib/database-stack';
import { FdnixPipelineStack } from '../lib/pipeline-stack';
import { FdnixSearchApiStack } from '../lib/search-api-stack';
import { FdnixFrontendStack } from '../lib/frontend-stack';
import { FdnixCertificateStack } from '../lib/certificate-stack';

const app = new cdk.App();

// Environment configuration
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
};

// Domain configuration - using Cloudflare for DNS management
const domainName = process.env.FDNIX_DOMAIN_NAME || 'fdnix.com';

// Stack naming prefix
const stackPrefix = 'Fdnix';

// Database Stack - Core data storage resources
const databaseStack = new FdnixDatabaseStack(app, `${stackPrefix}DatabaseStack`, {
  env,
  description: 'Database resources for fdnix hybrid search engine',
  stackName: 'fdnix-database-stack',
  tags: {
    Project: 'fdnix',
    Component: 'database',
    Environment: 'production',
  },
});

// Pipeline Stack - Data processing pipeline
const pipelineStack = new FdnixPipelineStack(app, `${stackPrefix}PipelineStack`, {
  env,
  databaseStack,
  description: 'Data processing pipeline for fdnix',
  stackName: 'fdnix-pipeline-stack',
  tags: {
    Project: 'fdnix',
    Component: 'pipeline',
    Environment: 'production',
  },
});

// Search API Stack - Lambda-based search API
const searchApiStack = new FdnixSearchApiStack(app, `${stackPrefix}SearchApiStack`, {
  env,
  databaseStack,
  description: 'Search API for fdnix hybrid search engine',
  stackName: 'fdnix-search-api-stack',
  tags: {
    Project: 'fdnix',
    Component: 'api',
    Environment: 'production',
  },
});

// Always create a separate Certificate Stack (validation can take time; does not block frontend)
new FdnixCertificateStack(app, `${stackPrefix}CertificateStack`, {
  env,
  description: 'ACM certificate for fdnix frontend custom domain',
  stackName: 'fdnix-certificate-stack',
  tags: {
    Project: 'fdnix',
    Component: 'certificate',
    Environment: 'production',
  },
  domainName,
});

// Frontend Stack - Static site hosting (does not require certificate)
const frontendStack = new FdnixFrontendStack(app, `${stackPrefix}FrontendStack`, {
  env,
  searchApiStack,
  domainName,
  // Intentionally not wiring the cert into CloudFront until issued
  description: 'Frontend hosting for fdnix search interface',
  stackName: 'fdnix-frontend-stack',
  tags: {
    Project: 'fdnix',
    Component: 'frontend',
    Environment: 'production',
  },
});

// Stack dependencies
pipelineStack.addDependency(databaseStack);
searchApiStack.addDependency(databaseStack);
frontendStack.addDependency(searchApiStack);

// Add cross-stack outputs
new cdk.CfnOutput(databaseStack, 'PackagesTableName', {
  value: databaseStack.packagesTable.tableName,
  description: 'Name of the DynamoDB table storing package metadata',
  exportName: 'FdnixPackagesTableName',
});

new cdk.CfnOutput(databaseStack, 'VectorIndexBucketName', {
  value: databaseStack.vectorIndexBucket.bucketName,
  description: 'Name of the S3 bucket storing vector indices',
  exportName: 'FdnixVectorIndexBucketName',
});


new cdk.CfnOutput(pipelineStack, 'MetadataRepositoryUri', {
  value: pipelineStack.metadataRepository.repositoryUri,
  description: 'URI of the ECR repository for metadata generator',
  exportName: 'FdnixMetadataRepositoryUri',
});

new cdk.CfnOutput(pipelineStack, 'EmbeddingRepositoryUri', {
  value: pipelineStack.embeddingRepository.repositoryUri,
  description: 'URI of the ECR repository for embedding generator',
  exportName: 'FdnixEmbeddingRepositoryUri',
});

new cdk.CfnOutput(pipelineStack, 'PipelineStateMachineArn', {
  value: pipelineStack.pipelineStateMachine.stateMachineArn,
  description: 'ARN of the Step Functions state machine for the data pipeline',
  exportName: 'FdnixPipelineStateMachineArn',
});

new cdk.CfnOutput(searchApiStack, 'SearchApiUrl', {
  value: searchApiStack.api.url,
  description: 'URL of the search API Gateway',
  exportName: 'FdnixSearchApiUrl',
});

new cdk.CfnOutput(searchApiStack, 'SearchFunctionName', {
  value: searchApiStack.searchFunction.functionName,
  description: 'Name of the search Lambda function',
  exportName: 'FdnixSearchFunctionName',
});

new cdk.CfnOutput(frontendStack, 'CloudFrontDistributionId', {
  value: frontendStack.distribution.distributionId,
  description: 'CloudFront distribution ID for the frontend',
  exportName: 'FdnixCloudFrontDistributionId',
});

new cdk.CfnOutput(frontendStack, 'CloudFrontDomainName', {
  value: frontendStack.distribution.distributionDomainName,
  description: 'CloudFront distribution domain name',
  exportName: 'FdnixCloudFrontDomainName',
});

new cdk.CfnOutput(frontendStack, 'CustomDomainName', {
  value: domainName,
  description: 'Custom domain name for the frontend (managed via Cloudflare)',
  exportName: 'FdnixCustomDomainName',
});

new cdk.CfnOutput(frontendStack, 'CloudflareSetupInstructions', {
  value: `Configure Cloudflare DNS: A record ${domainName} -> ${frontendStack.distribution.distributionDomainName}, CNAME record www.${domainName} -> ${frontendStack.distribution.distributionDomainName}`,
  description: 'DNS configuration instructions for Cloudflare',
  exportName: 'FdnixCloudflareInstructions',
});


// Application-level tags
cdk.Tags.of(app).add('Project', 'fdnix');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
cdk.Tags.of(app).add('Repository', 'fdnix');

// Synthesize the app
app.synth();
