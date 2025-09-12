#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { FdnixDatabaseStack } from '../lib/database-stack';
import { FdnixPipelineStack } from '../lib/pipeline-stack';
import { FdnixSearchApiStack } from '../lib/search-api-stack';
import { FdnixFrontendStack } from '../lib/frontend-stack';
import { FdnixCloudFrontStack } from '../lib/cloudfront-stack';
import { FdnixCertificateStack } from '../lib/certificate-stack';

const app = new cdk.App();

// Environment configuration
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
};

// Domain configuration - using Cloudflare for DNS management
const domainName = process.env.FDNIX_DOMAIN_NAME || 'fdnix.com';

// Environment-stage and stack naming prefix (optional)
const stage = process.env.FDNIX_STAGE || process.env.FDNIX_ENV || '';
const stackPrefix = process.env.FDNIX_STACK_PREFIX || 'Fdnix';

const toPascal = (s: string) => (s ? s.replace(/(^|[-_\s]+)([a-zA-Z])/g, (_, __, c) => c.toUpperCase()) : '');
const stagePascal = toPascal(stage);
const stackId = (name: string) => (stagePascal ? `${stackPrefix}${stagePascal}${name}` : `${stackPrefix}${name}`);
const envTag = stage || 'production';

// Database Stack - Core data storage resources
const databaseStack = new FdnixDatabaseStack(app, stackId('DatabaseStack'), {
  env,
  description: 'Database resources for fdnix hybrid search engine',
  tags: {
    Project: 'fdnix',
    Component: 'database',
    Environment: envTag,
  },
});

// Pipeline Stack - Data processing pipeline
const pipelineStack = new FdnixPipelineStack(app, stackId('PipelineStack'), {
  env,
  databaseStack,
  description: 'Data processing pipeline for fdnix',
  tags: {
    Project: 'fdnix',
    Component: 'pipeline',
    Environment: envTag,
  },
});


// Search API Stack - Lambda-based search API
const searchApiStack = new FdnixSearchApiStack(app, stackId('SearchApiStack'), {
  env,
  databaseStack,
  description: 'Search API for fdnix hybrid search engine',
  tags: {
    Project: 'fdnix',
    Component: 'api',
    Environment: envTag,
  },
});

// Frontend Stack - Static site hosting
const frontendStack = new FdnixFrontendStack(app, stackId('FrontendStack'), {
  env,
  searchApiStack,
  description: 'Frontend hosting for fdnix search interface',
  tags: {
    Project: 'fdnix',
    Component: 'frontend',
    Environment: envTag,
  },
});

// Certificate Stack - ACM certificate for custom domain (us-east-1)
const certificateStack = new FdnixCertificateStack(app, stackId('CertificateStack'), {
  env,
  description: 'ACM certificate for fdnix frontend custom domain',
  tags: {
    Project: 'fdnix',
    Component: 'certificate',
    Environment: envTag,
  },
  domainName,
});

// CloudFront Stack - CDN distribution
const cloudFrontStack = new FdnixCloudFrontStack(app, stackId('CloudFrontStack'), {
  env,
  frontendStack,
  searchApiStack,
  certificateArn: Fn.importValue('FdnixCertificateArn'),
  description: 'CloudFront distribution for fdnix frontend',
  tags: {
    Project: 'fdnix',
    Component: 'cloudfront',
    Environment: envTag,
  },
});

// Stack dependencies
pipelineStack.addDependency(databaseStack);
searchApiStack.addDependency(databaseStack);
frontendStack.addDependency(searchApiStack);
cloudFrontStack.addDependency(frontendStack);
cloudFrontStack.addDependency(certificateStack);

// Cross-stack outputs are handled within each individual stack


// Application-level tags
cdk.Tags.of(app).add('Project', 'fdnix');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
cdk.Tags.of(app).add('Repository', 'fdnix');

// Synthesize the app
app.synth();
