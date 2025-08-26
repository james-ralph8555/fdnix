#!/usr/bin/env python3

import os
import sys
import logging
import json
from typing import List, Dict, Any
from bedrock_client import BedrockClient
from s3_vector_client import S3VectorClient
from dynamodb_scanner import DynamoDBScanner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EmbeddingGenerator:
    def __init__(self):
        self.validate_environment()
        
        self.dynamodb_scanner = DynamoDBScanner(
            table_name=os.environ['DYNAMODB_TABLE'],
            region=os.environ['AWS_REGION']
        )
        
        self.bedrock_client = BedrockClient(
            model_id=os.environ['BEDROCK_MODEL_ID'],
            region=os.environ['AWS_REGION']
        )
        
        self.s3_client = S3VectorClient(
            bucket_name=os.environ['S3_BUCKET'],
            region=os.environ['AWS_REGION']
        )
        
        self.batch_size = 50  # Process embeddings in batches
        self.max_text_length = 2000  # Limit text length for embedding

    def validate_environment(self):
        """Validate required environment variables"""
        required_vars = [
            'DYNAMODB_TABLE',
            'S3_BUCKET', 
            'AWS_REGION',
            'BEDROCK_MODEL_ID'
        ]
        
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    def create_embedding_text(self, package: Dict[str, Any]) -> str:
        """Create a text representation of the package for embedding"""
        parts = []
        
        # Package name and version
        parts.append(f"Package: {package.get('packageName', '')}")
        if package.get('version'):
            parts.append(f"Version: {package['version']}")
        
        # Description - most important for semantic search
        if package.get('description'):
            parts.append(f"Description: {package['description']}")
        
        # Homepage URL
        if package.get('homepage'):
            parts.append(f"Homepage: {package['homepage']}")
        
        # License information
        if package.get('license'):
            parts.append(f"License: {package['license']}")
        
        # Maintainers
        if package.get('maintainers') and isinstance(package['maintainers'], list):
            maintainers = [str(m) for m in package['maintainers'][:3]]  # Limit to first 3
            if maintainers:
                parts.append(f"Maintainers: {', '.join(maintainers)}")
        
        # Platforms
        if package.get('platforms') and isinstance(package['platforms'], list):
            platforms = [str(p) for p in package['platforms'][:5]]  # Limit to first 5
            if platforms:
                parts.append(f"Platforms: {', '.join(platforms)}")
        
        # Attribute path for technical context
        if package.get('attributePath'):
            parts.append(f"Attribute: {package['attributePath']}")
        
        text = '. '.join(parts)
        
        # Truncate if too long
        if len(text) > self.max_text_length:
            text = text[:self.max_text_length - 3] + '...'
        
        return text

    async def process_packages_batch(self, packages: List[Dict[str, Any]]) -> int:
        """Process a batch of packages to generate embeddings"""
        logger.info(f"Processing batch of {len(packages)} packages...")
        
        # Create embedding texts
        texts = []
        package_keys = []
        
        for package in packages:
            text = self.create_embedding_text(package)
            texts.append(text)
            package_keys.append({
                'packageName': package['packageName'],
                'version': package['version']
            })
        
        try:
            # Generate embeddings using Bedrock
            logger.info(f"Generating embeddings for {len(texts)} texts...")
            embeddings = await self.bedrock_client.generate_embeddings_batch(texts)
            
            if len(embeddings) != len(texts):
                logger.error(f"Mismatch in embeddings count: expected {len(texts)}, got {len(embeddings)}")
                return 0
            
            # Store vectors in S3
            logger.info("Storing vectors in S3...")
            vector_data = []
            for i, (package_key, embedding, text) in enumerate(zip(package_keys, embeddings, texts)):
                vector_data.append({
                    'id': f"{package_key['packageName']}#{package_key['version']}",
                    'vector': embedding,
                    'metadata': {
                        'packageName': package_key['packageName'],
                        'version': package_key['version'],
                        'text': text[:500]  # Store first 500 chars for debugging
                    }
                })
            
            await self.s3_client.store_vectors_batch(vector_data)
            
            # Update DynamoDB to mark packages as having embeddings
            logger.info("Updating DynamoDB records...")
            await self.dynamodb_scanner.mark_packages_with_embeddings(package_keys)
            
            logger.info(f"Successfully processed batch of {len(packages)} packages")
            return len(packages)
            
        except Exception as error:
            logger.error(f"Error processing batch: {str(error)}")
            return 0

    async def run(self):
        """Main execution function"""
        logger.info("Starting fdnix embedding generation process...")
        
        try:
            # Scan for packages without embeddings
            logger.info("Scanning for packages without embeddings...")
            packages_without_embeddings = await self.dynamodb_scanner.scan_packages_without_embeddings()
            
            total_packages = len(packages_without_embeddings)
            logger.info(f"Found {total_packages} packages that need embeddings")
            
            if total_packages == 0:
                logger.info("No packages need embeddings. Exiting.")
                return
            
            # Process packages in batches
            processed_count = 0
            failed_count = 0
            
            for i in range(0, total_packages, self.batch_size):
                batch = packages_without_embeddings[i:i + self.batch_size]
                logger.info(f"Processing batch {i//self.batch_size + 1}/{(total_packages + self.batch_size - 1)//self.batch_size}")
                
                batch_processed = await self.process_packages_batch(batch)
                processed_count += batch_processed
                failed_count += len(batch) - batch_processed
                
                logger.info(f"Progress: {processed_count}/{total_packages} processed, {failed_count} failed")
            
            # Create or update the vector index
            logger.info("Creating/updating vector index...")
            await self.s3_client.create_or_update_index()
            
            logger.info(f"Embedding generation completed! Processed: {processed_count}, Failed: {failed_count}")
            
        except Exception as error:
            logger.error(f"Fatal error during embedding generation: {str(error)}")
            sys.exit(1)

async def main():
    generator = EmbeddingGenerator()
    await generator.run()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())