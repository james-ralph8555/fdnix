#!/usr/bin/env python3

import os
import sys
import logging
import json
from typing import List, Dict, Any
from bedrock_client import BedrockClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EmbeddingGenerator:
    def __init__(self):
        self.validate_environment()
        self.bedrock_client = BedrockClient(
            model_id=os.environ['BEDROCK_MODEL_ID'],
            region=os.environ['AWS_REGION']
        )
        
        self.batch_size = 50  # Process embeddings in batches
        self.max_text_length = 2000  # Limit text length for embedding
        # Optional local file I/O (temporary until DuckDB integration lands)
        self.packages_input = os.environ.get('PACKAGES_INPUT', '').strip()
        self.embeddings_output = os.environ.get('EMBEDDINGS_OUTPUT', '/tmp/embeddings.jsonl').strip()

    def validate_environment(self):
        """Validate required environment variables"""
        required_vars = [
            'AWS_REGION',
            'BEDROCK_MODEL_ID'
        ]
        
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    def load_packages_from_file(self) -> List[Dict[str, Any]]:
        """Load packages from a JSONL file if provided via PACKAGES_INPUT."""
        path = self.packages_input
        if not path:
            logger.info("No PACKAGES_INPUT provided; nothing to process.")
            return []
        if not os.path.exists(path):
            logger.warning(f"PACKAGES_INPUT file not found: {path}")
            return []
        logger.info(f"Loading packages from {path} ...")
        pkgs: List[Dict[str, Any]] = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pkgs.append(json.loads(line))
                except Exception as e:
                    logger.warning(f"Skipping invalid JSON line: {e}")
        logger.info(f"Loaded {len(pkgs)} packages from file")
        return pkgs

    def create_embedding_text(self, package: Dict[str, Any]) -> str:
        """Create a text representation of the package for embedding"""
        parts = []
        
        # Package name and version
        parts.append(f"Package: {package.get('packageName', '')}")
        if package.get('version'):
            parts.append(f"Version: {package['version']}")
        
        # Main program for better searchability
        if package.get('mainProgram'):
            parts.append(f"Main Program: {package['mainProgram']}")
        
        # Description - prefer longDescription for richer content, fallback to description
        description_text = package.get('longDescription', '') or package.get('description', '')
        if description_text:
            parts.append(f"Description: {description_text}")
        
        # Homepage URL
        if package.get('homepage'):
            parts.append(f"Homepage: {package['homepage']}")
        
        # License information - handle enhanced license structure
        license_info = self.format_license_for_embedding(package.get('license'))
        if license_info:
            parts.append(f"License: {license_info}")
        
        # Maintainers - handle enhanced maintainer structure
        maintainer_info = self.format_maintainers_for_embedding(package.get('maintainers', []))
        if maintainer_info:
            parts.append(f"Maintainers: {maintainer_info}")
        
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

    def format_license_for_embedding(self, license_data) -> str:
        """Format license data for embedding text"""
        if not license_data:
            return ""
        
        if isinstance(license_data, str):
            return license_data
        
        if isinstance(license_data, dict):
            if license_data.get('type') == 'string':
                return license_data.get('value', '')
            elif license_data.get('type') == 'object':
                # Prefer spdxId, then shortName, then fullName
                return license_data.get('spdxId') or license_data.get('shortName') or license_data.get('fullName', '')
            elif license_data.get('type') == 'array':
                licenses = license_data.get('licenses', [])
                license_names = []
                for lic in licenses[:3]:  # Limit to first 3 licenses
                    name = lic.get('spdxId') or lic.get('shortName') or lic.get('fullName', '')
                    if name:
                        license_names.append(name)
                return ', '.join(license_names)
        
        return str(license_data)[:100]  # Fallback with length limit

    def format_maintainers_for_embedding(self, maintainers) -> str:
        """Format maintainer data for embedding text"""
        if not maintainers or not isinstance(maintainers, list):
            return ""
        
        maintainer_names = []
        for maintainer in maintainers[:3]:  # Limit to first 3 maintainers
            if isinstance(maintainer, dict):
                name = maintainer.get('name', '') or maintainer.get('email', '') or maintainer.get('github', '')
                if name:
                    maintainer_names.append(name)
            elif isinstance(maintainer, str):
                maintainer_names.append(maintainer)
        
        return ', '.join(maintainer_names)

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
            
            # Prepare vector payloads (temporary file-based output)
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
            # Write to local JSONL as a temporary artifact
            os.makedirs(os.path.dirname(self.embeddings_output), exist_ok=True)
            with open(self.embeddings_output, 'a', encoding='utf-8') as out:
                for item in vector_data:
                    out.write(json.dumps(item) + "\n")
            logger.info(f"Wrote {len(vector_data)} embeddings to {self.embeddings_output}")
            
            logger.info(f"Successfully processed batch of {len(packages)} packages")
            return len(packages)
            
        except Exception as error:
            logger.error(f"Error processing batch: {str(error)}")
            return 0

    async def run(self):
        """Main execution function"""
        logger.info("Starting fdnix embedding generation process...")
        
        try:
            # Temporary input path flow until DuckDB integration is added
            packages = self.load_packages_from_file()
            total_packages = len(packages)
            if total_packages == 0:
                logger.info("No input packages provided. Exiting.")
                return
            
            # Process packages in batches
            processed_count = 0
            failed_count = 0
            
            for i in range(0, total_packages, self.batch_size):
                batch = packages[i:i + self.batch_size]
                logger.info(f"Processing batch {i//self.batch_size + 1}/{(total_packages + self.batch_size - 1)//self.batch_size}")
                
                batch_processed = await self.process_packages_batch(batch)
                processed_count += batch_processed
                failed_count += len(batch) - batch_processed
                
                logger.info(f"Progress: {processed_count}/{total_packages} processed, {failed_count} failed")
            
            # No S3 index handling; this will move to DuckDB
            
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
