#!/usr/bin/env node

const { NixpkgsExtractor } = require('./nixpkgs-extractor');
const { DynamoDBWriter } = require('./dynamodb-writer');

async function main() {
  console.log('Starting fdnix metadata generation process...');
  
  try {
    // Validate environment variables
    const requiredEnvVars = ['DYNAMODB_TABLE', 'AWS_REGION'];
    const missingVars = requiredEnvVars.filter(envVar => !process.env[envVar]);
    
    if (missingVars.length > 0) {
      throw new Error(`Missing required environment variables: ${missingVars.join(', ')}`);
    }

    const extractor = new NixpkgsExtractor();
    const writer = new DynamoDBWriter({
      tableName: process.env.DYNAMODB_TABLE,
      region: process.env.AWS_REGION
    });

    console.log('Extracting nixpkgs metadata...');
    const packages = await extractor.extractAllPackages();
    
    console.log(`Extracted ${packages.length} packages from nixpkgs`);
    
    console.log('Writing metadata to DynamoDB...');
    await writer.batchWritePackages(packages);
    
    console.log('Metadata generation completed successfully!');
    
  } catch (error) {
    console.error('Error during metadata generation:', error);
    process.exit(1);
  }
}

main();