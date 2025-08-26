import boto3
import json
import logging
import asyncio
from typing import List, Dict, Any
from botocore.exceptions import ClientError, BotoCoreError

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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
                logger.error(f"Failed to generate embedding for text {i}: {type(error).__name__}: {str(error)}")
                logger.error(f"Text content: {text[:100]}...")  # Log first 100 chars of problematic text
                # Add empty embedding to maintain array consistency
                embeddings.append([])
        
        successful_count = sum(1 for e in embeddings if e)
        logger.info(f"Successfully generated {successful_count}/{len(texts)} embeddings")
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
                logger.warning(f"Attempt {attempt + 1} failed, retrying in {delay}s: {type(error).__name__}: {str(error)}")
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
            
            # Debug: Log response structure to understand the KeyError
            logger.debug(f"Bedrock response structure: {list(response_body.keys()) if isinstance(response_body, dict) else type(response_body)}")
            
            # Extract embeddings from response based on Cohere's structure
            if 'embeddings' in response_body and response_body['embeddings']:
                embeddings_data = response_body['embeddings']
                
                # Handle case where embeddings is a dict with 'float' key containing the embedding arrays
                if isinstance(embeddings_data, dict) and 'float' in embeddings_data:
                    embedding_arrays = embeddings_data['float']
                    if isinstance(embedding_arrays, list) and len(embedding_arrays) > 0:
                        # Get the first embedding (for our single text input)
                        first_embedding = embedding_arrays[0]
                        if isinstance(first_embedding, list) and len(first_embedding) > 0:
                            logger.debug(f"Successfully extracted embedding with dimension {len(first_embedding)}")
                            return first_embedding
                        else:
                            logger.error(f"Invalid first embedding: {type(first_embedding)}, content: {first_embedding}")
                            return []
                    else:
                        logger.error(f"Invalid float embedding arrays: {type(embedding_arrays)}, content: {embedding_arrays}")
                        return []
                
                # Handle case where embeddings is a list of embedding objects
                elif isinstance(embeddings_data, list) and len(embeddings_data) > 0:
                    # First element should be the embedding array for our single text input
                    embedding = embeddings_data[0]
                    # Handle the case where embedding is wrapped in a dict with 'float' key
                    if isinstance(embedding, dict) and 'float' in embedding:
                        embedding_values = embedding['float']
                        # embedding_values might be a list of lists (batch response)
                        if isinstance(embedding_values, list) and len(embedding_values) > 0:
                            # If it's a nested list, take the first element
                            if isinstance(embedding_values[0], list):
                                actual_embedding = embedding_values[0]
                            else:
                                actual_embedding = embedding_values
                            
                            if isinstance(actual_embedding, list) and len(actual_embedding) > 0:
                                logger.debug(f"Successfully extracted embedding with dimension {len(actual_embedding)}")
                                return actual_embedding
                            else:
                                logger.error(f"Invalid extracted embedding: {type(actual_embedding)}, length: {len(actual_embedding) if isinstance(actual_embedding, list) else 'N/A'}")
                                return []
                        else:
                            logger.error(f"Invalid float embedding format: {type(embedding_values)}, length: {len(embedding_values) if isinstance(embedding_values, list) else 'N/A'}")
                            return []
                    elif isinstance(embedding, list) and len(embedding) > 0:
                        logger.debug(f"Successfully extracted embedding with dimension {len(embedding)}")
                        return embedding
                    else:
                        logger.error(f"Invalid embedding format - expected list or dict with 'float' key: {type(embedding)}, content: {embedding}")
                        return []
                else:
                    logger.error(f"Invalid embeddings format - expected list or dict with 'float' key: {type(embeddings_data)}, content: {embeddings_data}")
                    return []
            else:
                logger.error(f"No embeddings found in response. Response keys: {list(response_body.keys()) if isinstance(response_body, dict) else 'Not a dict'}")
                logger.error(f"Full response: {response_body}")
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
        except (KeyError, IndexError, TypeError) as error:
            logger.error(f"Response parsing error: {type(error).__name__}: {str(error)}")
            logger.error(f"This suggests the response structure is different than expected")
            logger.error(f"Error details: {repr(error)}")
            raise error
        except Exception as error:
            logger.error(f"Unexpected error generating embedding: {type(error).__name__}: {str(error)}")
            logger.error(f"Error details: {repr(error)}")
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