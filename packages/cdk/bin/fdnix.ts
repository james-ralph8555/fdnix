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

// Cross-stack outputs are handled within each individual stack


// Application-level tags
cdk.Tags.of(app).add('Project', 'fdnix');
cdk.Tags.of(app).add('ManagedBy', 'CDK');
cdk.Tags.of(app).add('Repository', 'fdnix');

// Synthesize the app
app.synth();
