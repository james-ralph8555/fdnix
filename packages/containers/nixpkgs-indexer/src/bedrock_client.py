import boto3
import json
import logging
import asyncio
from typing import List, Dict, Any
from botocore.exceptions import ClientError, BotoCoreError

logger = logging.getLogger(__name__)

class BedrockClient:
    def __init__(self, model_id: str, region: str):
        self.model_id = model_id
        self.region = region
        self.client = boto3.client('bedrock-runtime', region_name=region)
        self.max_retries = 3
        self.base_delay = 1.0  # seconds
        
        logger.info(f"Initialized Bedrock client for model {model_id} in region {region}")

    async def generate_embedding(self, text: str) -> List[float]:
        """Generate a single embedding from text"""
        embeddings = await self.generate_embeddings_batch([text])
        return embeddings[0] if embeddings else []

    async def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a batch of texts"""
        if not texts:
            return []
        
        logger.info(f"Generating embeddings for {len(texts)} texts using {self.model_id}")
        
        # For Cohere models, we need to process each text individually
        # as the batch API might not be available or might have different semantics
        embeddings = []
        
        for i, text in enumerate(texts):
            if i > 0 and i % 10 == 0:
                logger.info(f"Generated {i}/{len(texts)} embeddings...")
            
            try:
                embedding = await self._generate_single_embedding_with_retry(text)
                embeddings.append(embedding)
            except Exception as error:
                logger.error(f"Failed to generate embedding for text {i}: {str(error)}")
                # Add empty embedding to maintain array consistency
                embeddings.append([])
        
        logger.info(f"Successfully generated {sum(1 for e in embeddings if e)} embeddings out of {len(texts)}")
        return embeddings

    async def _generate_single_embedding_with_retry(self, text: str) -> List[float]:
        """Generate embedding for a single text with retry logic"""
        for attempt in range(self.max_retries):
            try:
                return await self._generate_single_embedding(text)
            except Exception as error:
                if attempt == self.max_retries - 1:
                    raise error
                
                delay = self.base_delay * (2 ** attempt)
                logger.warning(f"Attempt {attempt + 1} failed, retrying in {delay}s: {str(error)}")
                await asyncio.sleep(delay)
        
        return []  # Should not reach here

    async def _generate_single_embedding(self, text: str) -> List[float]:
        """Generate embedding for a single text"""
        # Prepare the request body for Cohere embedding model
        body = {
            "texts": [text],
            "input_type": "search_document",
            "truncate": "END",
            "embedding_types": ["float"]
        }
        
        try:
            # Make the synchronous call and wrap it to be async-compatible
            response = await asyncio.to_thread(
                self.client.invoke_model,
                modelId=self.model_id,
                contentType='application/json',
                accept='application/json',
                body=json.dumps(body)
            )
            
            # Parse the response
            response_body = json.loads(response['body'].read())
            
            # Extract embeddings from response
            if 'embeddings' in response_body and response_body['embeddings']:
                return response_body['embeddings'][0]
            else:
                logger.error(f"No embeddings found in response: {response_body}")
                return []
                
        except ClientError as error:
            error_code = error.response['Error']['Code']
            error_message = error.response['Error']['Message']
            
            if error_code == 'ThrottlingException':
                logger.warning("Bedrock request was throttled")
                raise error
            elif error_code == 'ValidationException':
                logger.error(f"Validation error: {error_message}")
                raise error
            elif error_code == 'AccessDeniedException':
                logger.error(f"Access denied to Bedrock model: {error_message}")
                raise error
            else:
                logger.error(f"Bedrock client error ({error_code}): {error_message}")
                raise error
                
        except BotoCoreError as error:
            logger.error(f"BotoCore error: {str(error)}")
            raise error
        except Exception as error:
            logger.error(f"Unexpected error generating embedding: {str(error)}")
            raise error

    def validate_model_access(self) -> bool:
        """Validate that the model is accessible"""
        try:
            # Try to generate a simple embedding
            test_text = "test"
            body = {
                "texts": [test_text],
                "input_type": "search_document",
                "truncate": "END",
                "embedding_types": ["float"]
            }
            
            response = self.client.invoke_model(
                modelId=self.model_id,
                contentType='application/json',
                accept='application/json',
                body=json.dumps(body)
            )
            
            response_body = json.loads(response['body'].read())
            return 'embeddings' in response_body
            
        except Exception as error:
            logger.error(f"Model validation failed: {str(error)}")
            return False