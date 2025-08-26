import os
import json
import logging
import asyncio
import random
import time
import httpx
from collections import deque
from typing import List, Dict, Any, Deque, Tuple

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class GeminiClient:
    def __init__(self, api_key: str = None, model_id: str = None):
        self.api_key = api_key or os.environ.get('GOOGLE_GEMINI_API_KEY')
        self.model_id = model_id or os.environ.get('GEMINI_MODEL_ID', 'gemini-embedding-001')
        self.output_dimensions = int(os.environ.get('GEMINI_OUTPUT_DIMENSIONS', '256'))
        self.task_type = os.environ.get('GEMINI_TASK_TYPE', 'SEMANTIC_SIMILARITY')
        
        if not self.api_key:
            raise ValueError("GOOGLE_GEMINI_API_KEY environment variable is required")
        
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_id}:embedContent"
        
        # Retry/backoff settings
        self.max_retries = 3
        self.base_delay = 1.0  # seconds
        
        # Concurrency and rate limiting (configurable via env)
        self.max_concurrent_requests = int(os.environ.get('GEMINI_MAX_CONCURRENT_REQUESTS', '10'))
        self.requests_per_minute = int(os.environ.get('GEMINI_REQUESTS_PER_MINUTE', '3000'))
        self.tokens_per_minute = int(os.environ.get('GEMINI_TOKENS_PER_MINUTE', '1000000'))
        self.inter_batch_delay = float(os.environ.get('GEMINI_INTER_BATCH_DELAY', '0.02'))
        
        # Async primitives/state
        self._sem = asyncio.Semaphore(self.max_concurrent_requests)
        self._rate_lock = asyncio.Lock()
        self._req_times: Deque[float] = deque()              # request timestamps in last 60s
        self._tok_times: Deque[Tuple[float, int]] = deque()  # (timestamp, tokens)
        
        # Circuit breaker for sustained throttling
        self._throttle_streak = 0
        self._throttle_break_threshold = 5
        self._throttle_cooldown = 10.0
        
        # HTTP client
        self._client = None
        
        logger.info(
            f"Initialized Gemini client for {self.model_id} | concurrency={self.max_concurrent_requests}, rpm={self.requests_per_minute}, tpm={self.tokens_per_minute}, dim={self.output_dimensions}"
        )

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    async def generate_embedding(self, text: str) -> List[float]:
        """Generate a single embedding from text"""
        embeddings = await self.generate_embeddings_batch([text])
        return embeddings[0] if embeddings else []

    async def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a batch of texts with concurrency and rate limiting"""
        if not texts:
            return []

        total = len(texts)
        logger.info(f"Generating embeddings for {total} texts using {self.model_id}")

        results: List[List[float]] = [None] * total  # type: ignore
        completed = 0
        progress_marks = {int(total * p / 5) for p in range(1, 5)}  # 20/40/60/80%
        completed_lock = asyncio.Lock()

        async def worker(idx: int, text: str) -> None:
            nonlocal completed
            try:
                async with self._sem:
                    await self._respect_rate_limits(text)
                    vec = await self._generate_single_embedding_with_retry(text)
                results[idx] = vec
            except Exception as error:
                logger.error(f"Failed to generate embedding for index {idx}: {type(error).__name__}: {str(error)}")
                results[idx] = []
            finally:
                async with completed_lock:
                    completed += 1
                    if completed in progress_marks:
                        pct = int((completed / total) * 100)
                        logger.info(f"Embedding progress: {completed}/{total} ({pct}%)")

        tasks = [asyncio.create_task(worker(i, t)) for i, t in enumerate(texts)]
        await asyncio.gather(*tasks)

        # Light smoothing between big batches
        if self.inter_batch_delay > 0:
            await asyncio.sleep(self.inter_batch_delay)

        successful_count = sum(1 for e in results if e)
        logger.info(f"Successfully generated {successful_count}/{total} embeddings")
        return results

    async def _generate_single_embedding_with_retry(self, text: str) -> List[float]:
        """Generate embedding for a single text with retry, jitter, and simple circuit breaker"""
        for attempt in range(self.max_retries):
            try:
                vec = await self._generate_single_embedding(text)
                self._throttle_streak = 0
                return vec
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 429:  # Rate limit
                    self._throttle_streak += 1
                    if self._throttle_streak >= self._throttle_break_threshold:
                        logger.warning(f"Sustained throttling ({self._throttle_streak}). Cooling down {self._throttle_cooldown:.1f}s")
                        await asyncio.sleep(self._throttle_cooldown)
                        self._throttle_streak = 0
                if attempt == self.max_retries - 1:
                    raise
                delay = self.base_delay * (2 ** attempt)
                jitter = random.uniform(0, delay * 0.25)
                await asyncio.sleep(delay + jitter)
            except Exception:
                if attempt == self.max_retries - 1:
                    raise
                delay = self.base_delay * (2 ** attempt)
                jitter = random.uniform(0, delay * 0.25)
                await asyncio.sleep(delay + jitter)
        return []

    async def _respect_rate_limits(self, text: str) -> None:
        """Sliding-window limiter for requests/min and tokens/min."""
        est_tokens = self._estimate_tokens(text)
        now = time.monotonic()
        async with self._rate_lock:
            cutoff = now - 60.0
            while self._req_times and self._req_times[0] < cutoff:
                self._req_times.popleft()
            while self._tok_times and self._tok_times[0][0] < cutoff:
                self._tok_times.popleft()

            def token_sum() -> int:
                return sum(t for _, t in self._tok_times)

            while len(self._req_times) >= self.requests_per_minute or (token_sum() + est_tokens) > self.tokens_per_minute:
                next_req_expire = (self._req_times[0] + 60.0) if self._req_times else now
                next_tok_expire = (self._tok_times[0][0] + 60.0) if self._tok_times else now
                sleep_for = max(0.01, min(next_req_expire, next_tok_expire) - now)
                await asyncio.sleep(sleep_for)
                now = time.monotonic()
                cutoff = now - 60.0
                while self._req_times and self._req_times[0] < cutoff:
                    self._req_times.popleft()
                while self._tok_times and self._tok_times[0][0] < cutoff:
                    self._tok_times.popleft()

            self._req_times.append(now)
            self._tok_times.append((now, est_tokens))

    def _estimate_tokens(self, text: str) -> int:
        # Rough heuristic: ~4 chars per token
        return max(1, int(len(text) / 4))

    async def _generate_single_embedding(self, text: str) -> List[float]:
        """Generate embedding for a single text using Gemini API"""
        if not self._client:
            raise RuntimeError("HTTP client not initialized. Use 'async with' context.")
        
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json"
        }
        
        # Prepare request body for Gemini embedding API
        body = {
            "model": f"models/{self.model_id}",
            "content": {
                "parts": [{
                    "text": text
                }]
            },
            "taskType": self.task_type,
            "outputDimensionality": self.output_dimensions
        }
        
        try:
            response = await self._client.post(
                self.base_url,
                headers=headers,
                json=body
            )
            response.raise_for_status()
            
            response_data = response.json()
            
            # Extract embedding from Gemini response
            if "embedding" in response_data and "values" in response_data["embedding"]:
                embedding_values = response_data["embedding"]["values"]
                if isinstance(embedding_values, list) and len(embedding_values) > 0:
                    logger.debug(f"Successfully extracted embedding with dimension {len(embedding_values)}")
                    return embedding_values
                else:
                    logger.error(f"Invalid embedding values: {type(embedding_values)}, length: {len(embedding_values) if isinstance(embedding_values, list) else 'N/A'}")
                    return []
            else:
                logger.error(f"No embedding found in response. Response keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}")
                logger.error(f"Full response: {response_data}")
                return []
                
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            error_text = error.response.text
            
            if status_code == 429:
                logger.warning("Gemini request was rate limited")
                raise error
            elif status_code == 400:
                logger.error(f"Bad request: {error_text}")
                raise error
            elif status_code == 401:
                logger.error(f"Authentication failed: {error_text}")
                raise error
            elif status_code == 403:
                logger.error(f"Access forbidden: {error_text}")
                raise error
            else:
                logger.error(f"Gemini API error ({status_code}): {error_text}")
                raise error
                
        except httpx.RequestError as error:
            logger.error(f"Request error: {str(error)}")
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
        """Validate that the Gemini API is accessible"""
        async def _validate():
            try:
                async with self:
                    test_embedding = await self.generate_embedding("test")
                    return len(test_embedding) > 0
            except Exception as error:
                logger.error(f"Model validation failed: {str(error)}")
                return False
        
        try:
            return asyncio.run(_validate())
        except Exception:
            return False