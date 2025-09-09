#!/usr/bin/env python3

# Standard Bedrock embedding client using individual API calls with rate limiting
# Respects account service quotas: 600 req/min and 300k tokens/min

import os
import json
import logging
import time
import asyncio
from typing import List, Tuple
import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BedrockClient:
    """Bedrock embedding client with rate limiting that respects account service quotas."""
    
    def __init__(self, region: str = None, model_id: str = None):
        self.region = region or os.environ.get('AWS_REGION', 'us-east-1')
        self.model_id = model_id or os.environ.get('BEDROCK_MODEL_ID', 'amazon.titan-embed-text-v2:0')
        self.output_dimensions = int(os.environ.get('BEDROCK_OUTPUT_DIMENSIONS', '256'))
        
        # Rate limiting configuration (600 RPM, 300K tokens per minute - account service quotas)
        self.max_rpm = int(os.environ.get('BEDROCK_MAX_RPM', '600'))
        self.max_tokens_per_minute = int(os.environ.get('BEDROCK_MAX_TOKENS_PER_MINUTE', '300000'))
        
        # Internal rate tracking
        self.requests_in_minute = []
        self.tokens_in_minute = []
        self.last_request_time = 0
        
        # AWS client
        self.bedrock_runtime = boto3.client('bedrock-runtime', region_name=self.region)
        
        logger.info(
            f"Initialized Bedrock client for {self.model_id} | "
            f"dimensions={self.output_dimensions}, region={self.region}, "
            f"max_rpm={self.max_rpm}, max_tokens_per_minute={self.max_tokens_per_minute}"
        )
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for rate limiting.
        
        Titan Text Embeddings V2 uses roughly 4.7 characters per token on average.
        We use a conservative estimate to avoid exceeding quotas.
        """
        # Conservative estimate: character count / 4 (slightly more conservative than 4.7)
        char_based_tokens = len(text) // 4
        # Also consider word count as a minimum
        word_count = len(text.split())
        # Use the higher of the two estimates for safety
        return max(char_based_tokens, word_count)
    
    def _clean_old_requests(self):
        """Remove requests/tokens older than 1 minute."""
        current_time = time.time()
        cutoff_time = current_time - 60  # 1 minute ago
        
        self.requests_in_minute = [t for t in self.requests_in_minute if t > cutoff_time]
        self.tokens_in_minute = [(t, count) for t, count in self.tokens_in_minute if t > cutoff_time]
    
    async def _wait_for_rate_limit(self, estimated_tokens: int):
        """Wait if necessary to respect rate limits."""
        current_time = time.time()
        
        # Clean old requests
        self._clean_old_requests()
        
        # Check RPM limit
        if len(self.requests_in_minute) >= self.max_rpm:
            oldest_request = min(self.requests_in_minute)
            wait_time = 60 - (current_time - oldest_request) + 0.1  # Add small buffer
            if wait_time > 0:
                logger.info(f"Rate limit: waiting {wait_time:.1f}s for RPM limit")
                await asyncio.sleep(wait_time)
                self._clean_old_requests()
        
        # Check token limit
        current_tokens = sum(count for _, count in self.tokens_in_minute)
        if current_tokens + estimated_tokens > self.max_tokens_per_minute:
            if self.tokens_in_minute:
                oldest_token_time = min(t for t, _ in self.tokens_in_minute)
                wait_time = 60 - (current_time - oldest_token_time) + 0.1  # Add small buffer
                if wait_time > 0:
                    logger.info(f"Rate limit: waiting {wait_time:.1f}s for token limit")
                    await asyncio.sleep(wait_time)
                    self._clean_old_requests()
        
        # Minimum delay between requests
        time_since_last = current_time - self.last_request_time
        min_delay = 60.0 / self.max_rpm  # Spread requests evenly
        if time_since_last < min_delay:
            await asyncio.sleep(min_delay - time_since_last)
    
    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for a single text with rate limiting."""
        if not text:
            raise ValueError("Empty text provided")
        
        estimated_tokens = self._estimate_tokens(text)
        await self._wait_for_rate_limit(estimated_tokens)
        
        current_time = time.time()
        
        # Prepare request
        request_body = {
            "inputText": text,
            "dimensions": self.output_dimensions
        }
        
        try:
            response = self.bedrock_runtime.invoke_model(
                modelId=self.model_id,
                accept='application/json',
                contentType='application/json',
                body=json.dumps(request_body)
            )
            
            # Parse response
            response_body = json.loads(response['body'].read())
            embedding = response_body['embedding']
            
            # Track rate limiting
            self.requests_in_minute.append(current_time)
            self.tokens_in_minute.append((current_time, estimated_tokens))
            self.last_request_time = current_time
            
            return embedding
            
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise
    
    async def generate_embeddings_batch(self, texts_with_ids: List[Tuple[str, str]]) -> List[Tuple[str, List[float]]]:
        """Generate embeddings for multiple texts with rate limiting."""
        if not texts_with_ids:
            return []
        
        logger.info(f"Starting embedding generation for {len(texts_with_ids)} texts with rate limiting")
        
        results = []
        failed_count = 0
        
        for i, (record_id, text) in enumerate(texts_with_ids):
            try:
                if i % 10 == 0:  # Log progress every 10 requests
                    logger.info(f"Processing embedding {i+1}/{len(texts_with_ids)}")
                
                embedding = await self.generate_embedding(text)
                results.append((record_id, embedding))
                
            except Exception as e:
                logger.warning(f"Failed to generate embedding for {record_id}: {e}")
                failed_count += 1
                continue
        
        logger.info(f"Embedding generation completed: {len(results)}/{len(texts_with_ids)} successful, {failed_count} failed")
        return results
    
    def validate_model_access(self) -> bool:
        """Validate that we can access the Bedrock model."""
        try:
            # Try a simple embedding request
            request_body = {
                "inputText": "test",
                "dimensions": self.output_dimensions
            }
            
            response = self.bedrock_runtime.invoke_model(
                modelId=self.model_id,
                accept='application/json',
                contentType='application/json',
                body=json.dumps(request_body)
            )
            
            response_body = json.loads(response['body'].read())
            if 'embedding' in response_body and response_body['embedding']:
                logger.info(f"Model {self.model_id} is available")
                return True
            else:
                logger.error(f"Model {self.model_id} did not return valid embedding")
                return False
                
        except Exception as e:
            logger.error(f"Failed to validate model access: {e}")
            return False