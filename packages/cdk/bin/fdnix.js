#!/usr/bin/env node
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
require("source-map-support/register");
const cdk = __importStar(require("aws-cdk-lib"));
const database_stack_1 = require("../lib/database-stack");
const pipeline_stack_1 = require("../lib/pipeline-stack");
const search_api_stack_1 = require("../lib/search-api-stack");
const frontend_stack_1 = require("../lib/frontend-stack");
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
const databaseStack = new database_stack_1.FdnixDatabaseStack(app, `${stackPrefix}DatabaseStack`, {
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
const pipelineStack = new pipeline_stack_1.FdnixPipelineStack(app, `${stackPrefix}PipelineStack`, {
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
const searchApiStack = new search_api_stack_1.FdnixSearchApiStack(app, `${stackPrefix}SearchApiStack`, {
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
// Frontend Stack - Static site hosting
const frontendStack = new frontend_stack_1.FdnixFrontendStack(app, `${stackPrefix}FrontendStack`, {
    env,
    searchApiStack,
    domainName,
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
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiZmRuaXguanMiLCJzb3VyY2VSb290IjoiIiwic291cmNlcyI6WyJmZG5peC50cyJdLCJuYW1lcyI6W10sIm1hcHBpbmdzIjoiOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7QUFDQSx1Q0FBcUM7QUFDckMsaURBQW1DO0FBQ25DLDBEQUEyRDtBQUMzRCwwREFBMkQ7QUFDM0QsOERBQThEO0FBQzlELDBEQUEyRDtBQUUzRCxNQUFNLEdBQUcsR0FBRyxJQUFJLEdBQUcsQ0FBQyxHQUFHLEVBQUUsQ0FBQztBQUUxQiw0QkFBNEI7QUFDNUIsTUFBTSxHQUFHLEdBQUc7SUFDVixPQUFPLEVBQUUsT0FBTyxDQUFDLEdBQUcsQ0FBQyxtQkFBbUI7SUFDeEMsTUFBTSxFQUFFLE9BQU8sQ0FBQyxHQUFHLENBQUMsa0JBQWtCLElBQUksV0FBVztDQUN0RCxDQUFDO0FBRUYsNkRBQTZEO0FBQzdELE1BQU0sVUFBVSxHQUFHLE9BQU8sQ0FBQyxHQUFHLENBQUMsaUJBQWlCLElBQUksV0FBVyxDQUFDO0FBRWhFLHNCQUFzQjtBQUN0QixNQUFNLFdBQVcsR0FBRyxPQUFPLENBQUM7QUFFNUIsK0NBQStDO0FBQy9DLE1BQU0sYUFBYSxHQUFHLElBQUksbUNBQWtCLENBQUMsR0FBRyxFQUFFLEdBQUcsV0FBVyxlQUFlLEVBQUU7SUFDL0UsR0FBRztJQUNILFdBQVcsRUFBRSxtREFBbUQ7SUFDaEUsU0FBUyxFQUFFLHNCQUFzQjtJQUNqQyxJQUFJLEVBQUU7UUFDSixPQUFPLEVBQUUsT0FBTztRQUNoQixTQUFTLEVBQUUsVUFBVTtRQUNyQixXQUFXLEVBQUUsWUFBWTtLQUMxQjtDQUNGLENBQUMsQ0FBQztBQUVILDRDQUE0QztBQUM1QyxNQUFNLGFBQWEsR0FBRyxJQUFJLG1DQUFrQixDQUFDLEdBQUcsRUFBRSxHQUFHLFdBQVcsZUFBZSxFQUFFO0lBQy9FLEdBQUc7SUFDSCxhQUFhO0lBQ2IsV0FBVyxFQUFFLG9DQUFvQztJQUNqRCxTQUFTLEVBQUUsc0JBQXNCO0lB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QUFHLENBQUMsSUFBSSxDQUFDLEVBQUUsQ0FBQyxHQUFHLENBQUMsQ0FBQyxHQUFHLENBQUMsWUFBWSxFQUFFLE9BQU8sQ0FBQyxDQUFDO0FBRTVDLHFCQUFxQjtBQUNyQixHQUFHLENBQUMsS0FBSyxFQUFFLENBQUMiLCJzb3VyY2VzQ29udGVudCI6WyIjIS91c3IvYmluL2VudiBub2RlXG5pbXBvcnQgJ3NvdXJjZS1tYXAtc3VwcG9ydC9yZWdpc3Rlcic7XG5pbXBvcnQgKiBhcyBjZGsgZnJvbSAnYXdzLWNkay1saWInO1xuaW1wb3J0IHsgRmRuaXhEYXRhYmFzZVN0YWNrIH0gZnJvbSAnLi4vbGliL2RhdGFiYXNlLXN0YWNrJztcbmltcG9ydCB7IEZkbml4UGlwZWxpbmVTdGFjayB9IGZyb20gJy4uL2xpYi9waXBlbGluZS1zdGFjayc7XG5pbXBvcnQgeyBGZG5peFNlYXJjaEFwaVN0YWNrIH0gZnJvbSAnLi4vbGliL3NlYXJjaC1hcGktc3RhY2snO1xuaW1wb3J0IHsgRmRuaXhGcm9udGVuZFN0YWNrIH0gZnJvbSAnLi4vbGliL2Zyb250ZW5kLXN0YWNrJztcblxuY29uc3QgYXBwID0gbmV3IGNkay5BcHAoKTtcblxuLy8gRW52aXJvbm1lbnQgY29uZmlndXJhdGlvblxuY29uc3QgZW52ID0ge1xuICBhY2NvdW50OiBwcm9jZXNzLmVudi5DREtfREVGQVVMVF9BQ0NPVU5ULFxuICByZWdpb246IHByb2Nlc3MuZW52LkNES19ERUZBVUxUX1JFR0lPTiB8fCAndXMtZWFzdC0xJyxcbn07XG5cbi8vIERvbWFpbiBjb25maWd1cmF0aW9uIC0gdXNpbmcgQ2xvdWRmbGFyZSBmb3IgRE5TIG1hbmFnZW1lbnRcbmNvbnN0IGRvbWFpbk5hbWUgPSBwcm9jZXNzLmVudi5GRE5JWF9ET01BSU5fTkFNRSB8fCAnZmRuaXguY29tJztcblxuLy8gU3RhY2sgbmFtaW5nIHByZWZpeFxuY29uc3Qgc3RhY2tQcmVmaXggPSAnRmRuaXgnO1xuXG4vLyBEYXRhYmFzZSBTdGFjayAtIENvcmUgZGF0YSBzdG9yYWdlIHJlc291cmNlc1xuY29uc3QgZGF0YWJhc2VTdGFjayA9IG5ldyBGZG5peERhdGFiYXNlU3RhY2soYXBwLCBgJHtzdGFja1ByZWZpeH1EYXRhYmFzZVN0YWNrYCwge1xuICBlbnYsXG4gIGRlc2NyaXB0aW9uOiAnRGF0YWJhc2UgcmVzb3VyY2VzIGZvciBmZG5peCBoeWJyaWQgc2VhcmNoIGVuZ2luZScsXG4gIHN0YWNrTmFtZTogJ2Zkbml4LWRhdGFiYXNlLXN0YWNrJyxcbiAgdGFnczoge1xuICAgIFByb2plY3Q6ICdmZG5peCcsXG4gICAgQ29tcG9uZW50OiAnZGF0YWJhc2UnLFxuICAgIEVudmlyb25tZW50OiAncHJvZHVjdGlvbicsXG4gIH0sXG59KTtcblxuLy8gUGlwZWxpbmUgU3RhY2sgLSBEYXRhIHByb2Nlc3NpbmcgcGlwZWxpbmVcbmNvbnN0IHBpcGVsaW5lU3RhY2sgPSBuZXcgRmRuaXhQaXBlbGluZVN0YWNrKGFwcCwgYCR7c3RhY2tQcmVmaXh9UGlwZWxpbmVTdGFja2AsIHtcbiAgZW52LFxuICBkYXRhYmFzZVN0YWNrLFxuICBkZXNjcmlwdGlvbjogJ0RhdGEgcHJvY2Vzc2luZyBwaXBlbGluZSBmb3IgZmRuaXgnLFxuICBzdGFja05hbWU6ICdmZG5peC1waXBlbGluZS1zdGFjaycsXG4gIHRhZ3M6IHtcbiAgICBQcm9qZWN0OiAnZmRuaXgnLFxuICAgIENvbXBvbmVudDogJ3BpcGVsaW5lJyxcbiAgICBFbnZpcm9ubWVudDogJ3Byb2R1Y3Rpb24nLFxuICB9LFxufSk7XG5cbi8vIFNlYXJjaCBBUEkgU3RhY2sgLSBMYW1iZGEtYmFzZWQgc2VhcmNoIEFQSVxuY29uc3Qgc2VhcmNoQXBpU3RhY2sgPSBuZXcgRmRuaXhTZWFyY2hBcGlTdGFjayhhcHAsIGAke3N0YWNrUHJlZml4fVNlYXJjaEFwaVN0YWNrYCwge1xuICBlbnYsXG4gIGRhdGFiYXNlU3RhY2ssXG4gIGRlc2NyaXB0aW9uOiAnU2VhcmNoIEFQSSBmb3IgZmRuaXggaHlicmlkIHNlYXJjaCBlbmdpbmUnLFxuICBzdGFja05hbWU6ICdmZG5peC1zZWFyY2gtYXBpLXN0YWNrJyxcbiAgdGFnczoge1xuICAgIFByb2plY3Q6ICdmZG5peCcsXG4gICAgQ29tcG9uZW50OiAnYXBpJyxcbiAgICBFbnZpcm9ubWVudDogJ3Byb2R1Y3Rpb24nLFxuICB9LFxufSk7XG5cbi8vIEZyb250ZW5kIFN0YWNrIC0gU3RhdGljIHNpdGUgaG9zdGluZ1xuY29uc3QgZnJvbnRlbmRTdGFjayA9IG5ldyBGZG5peEZyb250ZW5kU3RhY2soYXBwLCBgJHtzdGFja1ByZWZpeH1Gcm9udGVuZFN0YWNrYCwge1xuICBlbnYsXG4gIHNlYXJjaEFwaVN0YWNrLFxuICBkb21haW5OYW1lLFxuICBkZXNjcmlwdGlvbjogJ0Zyb250ZW5kIGhvc3RpbmcgZm9yIGZkbml4IHNlYXJjaCBpbnRlcmZhY2UnLFxuICBzdGFja05hbWU6ICdmZG5peC1mcm9udGVuZC1zdGFjaycsXG4gIHRhZ3M6IHtcbiAgICBQcm9qZWN0OiAnZmRuaXgnLFxuICAgIENvbXBvbmVudDogJ2Zyb250ZW5kJyxcbiAgICBFbnZpcm9ubWVudDogJ3Byb2R1Y3Rpb24nLFxuICB9LFxufSk7XG5cbi8vIFN0YWNrIGRlcGVuZGVuY2llc1xucGlwZWxpbmVTdGFjay5hZGREZXBlbmRlbmN5KGRhdGFiYXNlU3RhY2spO1xuc2VhcmNoQXBpU3RhY2suYWRkRGVwZW5kZW5jeShkYXRhYmFzZVN0YWNrKTtcbmZyb250ZW5kU3RhY2suYWRkRGVwZW5kZW5jeShzZWFyY2hBcGlTdGFjayk7XG5cbi8vIEFkZCBjcm9zcy1zdGFjayBvdXRwdXRzXG5uZXcgY2RrLkNmbk91dHB1dChkYXRhYmFzZVN0YWNrLCAnUGFja2FnZXNUYWJsZU5hbWUnLCB7XG4gIHZhbHVlOiBkYXRhYmFzZVN0YWNrLnBhY2thZ2VzVGFibGUudGFibGVOYW1lLFxuICBkZXNjcmlwdGlvbjogJ05hbWUgb2YgdGhlIER5bmFtb0RCIHRhYmxlIHN0b3JpbmcgcGFja2FnZSBtZXRhZGF0YScsXG4gIGV4cG9ydE5hbWU6ICdGZG5peFBhY2thZ2VzVGFibGVOYW1lJyxcbn0pO1xuXG5uZXcgY2RrLkNmbk91dHB1dChkYXRhYmFzZVN0YWNrLCAnVmVjdG9ySW5kZXhCdWNrZXROYW1lJywge1xuICB2YWx1ZTogZGF0YWJhc2VTdGFjay52ZWN0b3JJbmRleEJ1Y2tldC5idWNrZXROYW1lLFxuICBkZXNjcmlwdGlvbjogJ05hbWUgb2YgdGhlIFMzIGJ1Y2tldCBzdG9yaW5nIHZlY3RvciBpbmRpY2VzJyxcbiAgZXhwb3J0TmFtZTogJ0Zkbml4VmVjdG9ySW5kZXhCdWNrZXROYW1lJyxcbn0pO1xuXG5cbm5ldyBjZGsuQ2ZuT3V0cHV0KHBpcGVsaW5lU3RhY2ssICdNZXRhZGF0YVJlcG9zaXRvcnlVcmknLCB7XG4gIHZhbHVlOiBwaXBlbGluZVN0YWNrLm1ldGFkYXRhUmVwb3NpdG9yeS5yZXBvc2l0b3J5VXJpLFxuICBkZXNjcmlwdGlvbjogJ1VSSSBvZiB0aGUgRUNSIHJlcG9zaXRvcnkgZm9yIG1ldGFkYXRhIGdlbmVyYXRvcicsXG4gIGV4cG9ydE5hbWU6ICdGZG5peE1ldGFkYXRhUmVwb3NpdG9yeVVyaScsXG59KTtcblxubmV3IGNkay5DZm5PdXRwdXQocGlwZWxpbmVTdGFjaywgJ0VtYmVkZGluZ1JlcG9zaXRvcnlVcmknLCB7XG4gIHZhbHVlOiBwaXBlbGluZVN0YWNrLmVtYmVkZGluZ1JlcG9zaXRvcnkucmVwb3NpdG9yeVVyaSxcbiAgZGVzY3JpcHRpb246ICdVUkkgb2YgdGhlIEVDUiByZXBvc2l0b3J5IGZvciBlbWJlZGRpbmcgZ2VuZXJhdG9yJyxcbiAgZXhwb3J0TmFtZTogJ0Zkbml4RW1iZWRkaW5nUmVwb3NpdG9yeVVyaScsXG59KTtcblxubmV3IGNkay5DZm5PdXRwdXQocGlwZWxpbmVTdGFjaywgJ1BpcGVsaW5lU3RhdGVNYWNoaW5lQXJuJywge1xuICB2YWx1ZTogcGlwZWxpbmVTdGFjay5waXBlbGluZVN0YXRlTWFjaGluZS5zdGF0ZU1hY2hpbmVBcm4sXG4gIGRlc2NyaXB0aW9uOiAnQVJOIG9mIHRoZSBTdGVwIEZ1bmN0aW9ucyBzdGF0ZSBtYWNoaW5lIGZvciB0aGUgZGF0YSBwaXBlbGluZScsXG4gIGV4cG9ydE5hbWU6ICdGZG5peFBpcGVsaW5lU3RhdGVNYWNoaW5lQXJuJyxcbn0pO1xuXG5uZXcgY2RrLkNmbk91dHB1dChzZWFyY2hBcGlTdGFjaywgJ1NlYXJjaEFwaVVybCcsIHtcbiAgdmFsdWU6IHNlYXJjaEFwaVN0YWNrLmFwaS51cmwsXG4gIGRlc2NyaXB0aW9uOiAnVVJMIG9mIHRoZSBzZWFyY2ggQVBJIEdhdGV3YXknLFxuICBleHBvcnROYW1lOiAnRmRuaXhTZWFyY2hBcGlVcmwnLFxufSk7XG5cbm5ldyBjZGsuQ2ZuT3V0cHV0KHNlYXJjaEFwaVN0YWNrLCAnU2VhcmNoRnVuY3Rpb25OYW1lJywge1xuICB2YWx1ZTogc2VhcmNoQXBpU3RhY2suc2VhcmNoRnVuY3Rpb24uZnVuY3Rpb25OYW1lLFxuICBkZXNjcmlwdGlvbjogJ05hbWUgb2YgdGhlIHNlYXJjaCBMYW1iZGEgZnVuY3Rpb24nLFxuICBleHBvcnROYW1lOiAnRmRuaXhTZWFyY2hGdW5jdGlvbk5hbWUnLFxufSk7XG5cbm5ldyBjZGsuQ2ZuT3V0cHV0KGZyb250ZW5kU3RhY2ssICdDbG91ZEZyb250RGlzdHJpYnV0aW9uSWQnLCB7XG4gIHZhbHVlOiBmcm9udGVuZFN0YWNrLmRpc3RyaWJ1dGlvbi5kaXN0cmlidXRpb25JZCxcbiAgZGVzY3JpcHRpb246ICdDbG91ZEZyb250IGRpc3RyaWJ1dGlvbiBJRCBmb3IgdGhlIGZyb250ZW5kJyxcbiAgZXhwb3J0TmFtZTogJ0Zkbml4Q2xvdWRGcm9udERpc3RyaWJ1dGlvbklkJyxcbn0pO1xuXG5uZXcgY2RrLkNmbk91dHB1dChmcm9udGVuZFN0YWNrLCAnQ2xvdWRGcm9udERvbWFpbk5hbWUnLCB7XG4gIHZhbHVlOiBmcm9udGVuZFN0YWNrLmRpc3RyaWJ1dGlvbi5kaXN0cmlidXRpb25Eb21haW5OYW1lLFxuICBkZXNjcmlwdGlvbjogJ0Nsb3VkRnJvbnQgZGlzdHJpYnV0aW9uIGRvbWFpbiBuYW1lJyxcbiAgZXhwb3J0TmFtZTogJ0Zkbml4Q2xvdWRGcm9udERvbWFpbk5hbWUnLFxufSk7XG5cbm5ldyBjZGsuQ2ZuT3V0cHV0KGZyb250ZW5kU3RhY2ssICdDdXN0b21Eb21haW5OYW1lJywge1xuICB2YWx1ZTogZG9tYWluTmFtZSxcbiAgZGVzY3JpcHRpb246ICdDdXN0b20gZG9tYWluIG5hbWUgZm9yIHRoZSBmcm9udGVuZCAobWFuYWdlZCB2aWEgQ2xvdWRmbGFyZSknLFxuICBleHBvcnROYW1lOiAnRmRuaXhDdXN0b21Eb21haW5OYW1lJyxcbn0pO1xuXG5uZXcgY2RrLkNmbk91dHB1dChmcm9udGVuZFN0YWNrLCAnQ2xvdWRmbGFyZVNldHVwSW5zdHJ1Y3Rpb25zJywge1xuICB2YWx1ZTogYENvbmZpZ3VyZSBDbG91ZGZsYXJlIEROUzogQSByZWNvcmQgJHtkb21haW5OYW1lfSAtPiAke2Zyb250ZW5kU3RhY2suZGlzdHJpYnV0aW9uLmRpc3RyaWJ1dGlvbkRvbWFpbk5hbWV9LCBDTkFNRSByZWNvcmQgd3d3LiR7ZG9tYWluTmFtZX0gLT4gJHtmcm9udGVuZFN0YWNrLmRpc3RyaWJ1dGlvbi5kaXN0cmlidXRpb25Eb21haW5OYW1lfWAsXG4gIGRlc2NyaXB0aW9uOiAnRE5TIGNvbmZpZ3VyYXRpb24gaW5zdHJ1Y3Rpb25zIGZvciBDbG91ZGZsYXJlJyxcbiAgZXhwb3J0TmFtZTogJ0Zkbml4Q2xvdWRmbGFyZUluc3RydWN0aW9ucycsXG59KTtcblxuLy8gQXBwbGljYXRpb24tbGV2ZWwgdGFnc1xuY2RrLlRhZ3Mub2YoYXBwKS5hZGQoJ1Byb2plY3QnLCAnZmRuaXgnKTtcbmNkay5UYWdzLm9mKGFwcCkuYWRkKCdNYW5hZ2VkQnknLCAnQ0RLJyk7XG5jZGsuVGFncy5vZihhcHApLmFkZCgnUmVwb3NpdG9yeScsICdmZG5peCcpO1xuXG4vLyBTeW50aGVzaXplIHRoZSBhcHBcbmFwcC5zeW50aCgpOyJdfQ==