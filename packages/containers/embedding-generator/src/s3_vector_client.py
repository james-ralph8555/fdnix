import boto3
import json
import logging
import asyncio
import numpy as np
from typing import List, Dict, Any, Optional
from botocore.exceptions import ClientError
import io
import gzip

logger = logging.getLogger(__name__)

class S3VectorClient:
    def __init__(self, bucket_name: str, region: str):
        self.bucket_name = bucket_name
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)
        self.index_key = 'vector-index/index.json.gz'
        self.vectors_prefix = 'vectors/'
        self.batch_size = 100  # Number of vectors to store per S3 object
        
        logger.info(f"Initialized S3 vector client for bucket {bucket_name} in region {region}")

    async def store_vectors_batch(self, vector_data: List[Dict[str, Any]]) -> bool:
        """Store a batch of vectors in S3"""
        logger.info(f"Storing {len(vector_data)} vectors in S3...")
        
        try:
            # Group vectors into batches for efficient storage
            batches = [vector_data[i:i + self.batch_size] for i in range(0, len(vector_data), self.batch_size)]
            
            stored_count = 0
            for batch_idx, batch in enumerate(batches):
                batch_key = f"{self.vectors_prefix}batch_{batch_idx}_{len(batch)}.json.gz"
                success = await self._store_vector_batch_file(batch_key, batch)
                if success:
                    stored_count += len(batch)
            
            logger.info(f"Successfully stored {stored_count}/{len(vector_data)} vectors")
            return stored_count == len(vector_data)
            
        except Exception as error:
            logger.error(f"Error storing vector batch: {str(error)}")
            return False

    async def _store_vector_batch_file(self, key: str, vectors: List[Dict[str, Any]]) -> bool:
        """Store a single batch file of vectors"""
        try:
            # Prepare the data
            batch_data = {
                'vectors': vectors,
                'count': len(vectors),
                'dimension': len(vectors[0]['vector']) if vectors else 0
            }
            
            # Compress the JSON data
            json_data = json.dumps(batch_data, separators=(',', ':'))
            compressed_data = gzip.compress(json_data.encode('utf-8'))
            
            # Store in S3
            await asyncio.to_thread(
                self.s3_client.put_object,
                Bucket=self.bucket_name,
                Key=key,
                Body=compressed_data,
                ContentType='application/json',
                ContentEncoding='gzip',
                Metadata={
                    'vector-count': str(len(vectors)),
                    'dimension': str(len(vectors[0]['vector']) if vectors else 0)
                }
            )
            
            return True
            
        except Exception as error:
            logger.error(f"Error storing batch file {key}: {str(error)}")
            return False

    async def create_or_update_index(self) -> bool:
        """Create or update the vector index file"""
        logger.info("Creating/updating vector index...")
        
        try:
            # List all vector batch files
            vector_files = await self._list_vector_files()
            
            # Create index metadata
            index_data = {
                'version': '1.0',
                'created_at': np.datetime64('now').isoformat(),
                'total_vectors': 0,
                'vector_dimension': 0,
                'batch_files': [],
                'metadata': {
                    'description': 'fdnix package embeddings index',
                    'model': 'cohere.embed-english-v3'
                }
            }
            
            # Process each batch file to gather statistics
            total_vectors = 0
            vector_dimension = 0
            
            for file_key in vector_files:
                try:
                    # Get file metadata
                    response = await asyncio.to_thread(
                        self.s3_client.head_object,
                        Bucket=self.bucket_name,
                        Key=file_key
                    )
                    
                    file_metadata = response.get('Metadata', {})
                    vector_count = int(file_metadata.get('vector-count', 0))
                    dimension = int(file_metadata.get('dimension', 0))
                    
                    total_vectors += vector_count
                    if vector_dimension == 0 and dimension > 0:
                        vector_dimension = dimension
                    
                    index_data['batch_files'].append({
                        'key': file_key,
                        'vector_count': vector_count,
                        'dimension': dimension,
                        'size': response['ContentLength']
                    })
                    
                except Exception as error:
                    logger.warning(f"Error processing batch file {file_key}: {str(error)}")
                    continue
            
            index_data['total_vectors'] = total_vectors
            index_data['vector_dimension'] = vector_dimension
            
            # Store the index file
            json_data = json.dumps(index_data, indent=2)
            compressed_data = gzip.compress(json_data.encode('utf-8'))
            
            await asyncio.to_thread(
                self.s3_client.put_object,
                Bucket=self.bucket_name,
                Key=self.index_key,
                Body=compressed_data,
                ContentType='application/json',
                ContentEncoding='gzip',
                Metadata={
                    'total-vectors': str(total_vectors),
                    'dimension': str(vector_dimension),
                    'batch-files': str(len(index_data['batch_files']))
                }
            )
            
            logger.info(f"Index created: {total_vectors} vectors, {vector_dimension} dimensions, {len(index_data['batch_files'])} batch files")
            return True
            
        except Exception as error:
            logger.error(f"Error creating index: {str(error)}")
            return False

    async def _list_vector_files(self) -> List[str]:
        """List all vector batch files in S3"""
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            vector_files = []
            async for page in self._paginate_async(paginator, Bucket=self.bucket_name, Prefix=self.vectors_prefix):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        if key.endswith('.json.gz') and 'batch_' in key:
                            vector_files.append(key)
            
            logger.info(f"Found {len(vector_files)} vector batch files")
            return vector_files
            
        except Exception as error:
            logger.error(f"Error listing vector files: {str(error)}")
            return []

    async def _paginate_async(self, paginator, **kwargs):
        """Convert synchronous pagination to async"""
        loop = asyncio.get_event_loop()
        page_iterator = paginator.paginate(**kwargs)
        
        for page in page_iterator:
            yield page

    async def get_index_info(self) -> Optional[Dict[str, Any]]:
        """Get information about the current index"""
        try:
            response = await asyncio.to_thread(
                self.s3_client.get_object,
                Bucket=self.bucket_name,
                Key=self.index_key
            )
            
            # Decompress and parse
            compressed_data = response['Body'].read()
            json_data = gzip.decompress(compressed_data).decode('utf-8')
            return json.loads(json_data)
            
        except ClientError as error:
            if error.response['Error']['Code'] == 'NoSuchKey':
                logger.info("No existing index found")
                return None
            else:
                logger.error(f"Error getting index info: {str(error)}")
                return None
        except Exception as error:
            logger.error(f"Error parsing index: {str(error)}")
            return None

    async def load_vectors(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Load vectors from S3 (for testing/debugging)"""
        logger.info("Loading vectors from S3...")
        
        try:
            vector_files = await self._list_vector_files()
            all_vectors = []
            
            for file_key in vector_files:
                if limit and len(all_vectors) >= limit:
                    break
                    
                try:
                    response = await asyncio.to_thread(
                        self.s3_client.get_object,
                        Bucket=self.bucket_name,
                        Key=file_key
                    )
                    
                    # Decompress and parse
                    compressed_data = response['Body'].read()
                    json_data = gzip.decompress(compressed_data).decode('utf-8')
                    batch_data = json.loads(json_data)
                    
                    vectors = batch_data.get('vectors', [])
                    all_vectors.extend(vectors)
                    
                    logger.info(f"Loaded {len(vectors)} vectors from {file_key}")
                    
                except Exception as error:
                    logger.warning(f"Error loading batch file {file_key}: {str(error)}")
                    continue
            
            if limit:
                all_vectors = all_vectors[:limit]
            
            logger.info(f"Loaded total of {len(all_vectors)} vectors")
            return all_vectors
            
        except Exception as error:
            logger.error(f"Error loading vectors: {str(error)}")
            return []