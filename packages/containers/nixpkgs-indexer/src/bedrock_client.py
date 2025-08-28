#!/usr/bin/env python3

# Priority order for embedding client selection:
# 1. Try batch API first (faster but requires special permissions)
# 2. Fall back to individual API with rate limiting (slower but works with standard permissions)

import os
import json
import logging
import time
import uuid
import asyncio
from typing import List, Dict, Any, Tuple
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class BedrockBatchClient:
    def __init__(self, region: str = None, model_id: str = None):
        self.region = region or os.environ.get('AWS_REGION', 'us-east-1')
        self.model_id = model_id or os.environ.get('BEDROCK_MODEL_ID', 'amazon.titan-embed-text-v2:0')
        self.output_dimensions = int(os.environ.get('BEDROCK_OUTPUT_DIMENSIONS', '256'))
        
        # Batch job configuration
        self.batch_size = int(os.environ.get('BEDROCK_BATCH_SIZE', '10000'))
        self.input_bucket = os.environ.get('BEDROCK_INPUT_BUCKET') or os.environ.get('ARTIFACTS_BUCKET')
        self.output_bucket = os.environ.get('BEDROCK_OUTPUT_BUCKET') or os.environ.get('ARTIFACTS_BUCKET')
        self.role_arn = os.environ.get('BEDROCK_ROLE_ARN')
        
        if not self.input_bucket:
            raise ValueError("BEDROCK_INPUT_BUCKET or ARTIFACTS_BUCKET environment variable is required")
        if not self.output_bucket:
            raise ValueError("BEDROCK_OUTPUT_BUCKET or ARTIFACTS_BUCKET environment variable is required")
        if not self.role_arn:
            raise ValueError("BEDROCK_ROLE_ARN environment variable is required for batch inference")
        
        # AWS clients
        self.bedrock = boto3.client('bedrock', region_name=self.region)
        self.s3 = boto3.client('s3', region_name=self.region)
        
        # Job polling configuration
        self.poll_interval = float(os.environ.get('BEDROCK_POLL_INTERVAL', '60'))  # seconds
        self.max_wait_time = int(os.environ.get('BEDROCK_MAX_WAIT_TIME', '7200'))  # 2 hours
        
        logger.info(
            f"Initialized Bedrock batch client for {self.model_id} | "
            f"batch_size={self.batch_size}, dimensions={self.output_dimensions}, "
            f"region={self.region}"
        )

    def create_batch_input_jsonl(self, texts_with_ids: List[Tuple[str, str]]) -> str:
        """
        Create JSONL content for batch inference input.
        
        Args:
            texts_with_ids: List of (record_id, text) tuples
            
        Returns:
            JSONL string content
        """
        lines = []
        for record_id, text in texts_with_ids:
            record = {
                "recordId": record_id,
                "modelInput": {
                    "inputText": text,
                    "dimensions": self.output_dimensions
                }
            }
            lines.append(json.dumps(record))
        
        return '\n'.join(lines)

    def upload_batch_input(self, jsonl_content: str, job_id: str) -> str:
        """Upload batch input JSONL to S3 and return the S3 URI."""
        key = f"bedrock-batch/input/{job_id}/input.jsonl"
        
        try:
            self.s3.put_object(
                Bucket=self.input_bucket,
                Key=key,
                Body=jsonl_content.encode('utf-8'),
                ContentType='application/x-ndjson'
            )
            s3_uri = f"s3://{self.input_bucket}/{key}"
            logger.info(f"Uploaded batch input to {s3_uri}")
            return s3_uri
        except Exception as e:
            logger.error(f"Failed to upload batch input: {e}")
            raise

    def submit_batch_job(self, input_s3_uri: str, job_id: str) -> str:
        """Submit a batch inference job and return the job ID."""
        output_s3_uri = f"s3://{self.output_bucket}/bedrock-batch/output/{job_id}/"
        
        job_config = {
            'jobName': f"fdnix-embedding-batch-{job_id}",
            'roleArn': self.role_arn,
            'modelId': self.model_id,
            'inputDataConfig': {
                's3InputDataConfig': {
                    's3Uri': input_s3_uri
                }
            },
            'outputDataConfig': {
                's3OutputDataConfig': {
                    's3Uri': output_s3_uri
                }
            }
        }
        
        try:
            response = self.bedrock.create_model_invocation_job(**job_config)
            job_arn = response['jobArn']
            logger.info(f"Submitted batch job: {job_arn}")
            logger.info(f"Output will be written to: {output_s3_uri}")
            return job_arn
        except Exception as e:
            logger.error(f"Failed to submit batch job: {e}")
            raise

    def wait_for_job_completion(self, job_arn: str) -> Dict[str, Any]:
        """Wait for batch job to complete and return job details."""
        start_time = time.time()
        
        while True:
            try:
                response = self.bedrock.get_model_invocation_job(jobArn=job_arn)
                status = response['status']
                
                logger.info(f"Batch job status: {status}")
                
                if status == 'Completed':
                    logger.info("Batch job completed successfully")
                    return response
                elif status == 'Failed':
                    failure_reason = response.get('message', 'Unknown failure')
                    logger.error(f"Batch job failed: {failure_reason}")
                    raise RuntimeError(f"Batch job failed: {failure_reason}")
                elif status in ['Stopped', 'Stopping']:
                    logger.error(f"Batch job was stopped: {status}")
                    raise RuntimeError(f"Batch job was stopped: {status}")
                
                # Check if we've exceeded maximum wait time
                elapsed = time.time() - start_time
                if elapsed > self.max_wait_time:
                    logger.error(f"Batch job timed out after {elapsed:.1f} seconds")
                    raise TimeoutError(f"Batch job timed out after {elapsed:.1f} seconds")
                
                # Wait before next poll
                time.sleep(self.poll_interval)
                
            except Exception as e:
                if isinstance(e, (RuntimeError, TimeoutError)):
                    raise
                logger.error(f"Error checking job status: {e}")
                time.sleep(self.poll_interval)

    def download_batch_results(self, job_details: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Download and parse batch job results."""
        output_config = job_details['outputDataConfig']['s3OutputDataConfig']
        output_s3_uri = output_config['s3Uri']
        
        # Parse S3 URI to get bucket and prefix
        if not output_s3_uri.startswith('s3://'):
            raise ValueError(f"Invalid S3 URI: {output_s3_uri}")
        
        uri_parts = output_s3_uri[5:].split('/', 1)  # Remove 's3://' prefix
        bucket = uri_parts[0]
        prefix = uri_parts[1] if len(uri_parts) > 1 else ''
        
        try:
            # List objects with the prefix to find result files
            response = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            
            if 'Contents' not in response:
                raise RuntimeError(f"No result files found at {output_s3_uri}")
            
            results = []
            for obj in response['Contents']:
                key = obj['Key']
                if key.endswith('.jsonl') or key.endswith('.out'):
                    logger.info(f"Downloading result file: s3://{bucket}/{key}")
                    
                    # Download and parse JSONL file
                    response = self.s3.get_object(Bucket=bucket, Key=key)
                    content = response['Body'].read().decode('utf-8')
                    
                    # Parse each line as JSON
                    for line in content.strip().split('\n'):
                        if line:
                            try:
                                result = json.loads(line)
                                results.append(result)
                            except json.JSONDecodeError as e:
                                logger.warning(f"Failed to parse result line: {e}")
                                continue
            
            logger.info(f"Downloaded {len(results)} embedding results")
            return results
            
        except Exception as e:
            logger.error(f"Failed to download batch results: {e}")
            raise

    def process_batch_results(self, results: List[Dict[str, Any]]) -> List[Tuple[str, List[float]]]:
        """
        Process batch results to extract embeddings.
        
        Returns:
            List of (record_id, embedding_vector) tuples
        """
        processed = []
        failed_count = 0
        
        for result in results:
            try:
                record_id = result['recordId']
                
                if 'modelOutput' in result:
                    # Successful result
                    model_output = result['modelOutput']
                    if 'embedding' in model_output:
                        embedding = model_output['embedding']
                        processed.append((record_id, embedding))
                    else:
                        logger.warning(f"No embedding in result for record {record_id}")
                        failed_count += 1
                else:
                    # Failed result
                    error_msg = result.get('error', {}).get('message', 'Unknown error')
                    logger.warning(f"Failed result for record {record_id}: {error_msg}")
                    failed_count += 1
                    
            except Exception as e:
                logger.warning(f"Error processing result: {e}")
                failed_count += 1
        
        logger.info(f"Processed {len(processed)} successful embeddings, {failed_count} failures")
        return processed

    async def generate_embeddings_batch(self, texts_with_ids: List[Tuple[str, str]]) -> List[Tuple[str, List[float]]]:
        """
        Generate embeddings for a batch of texts using Bedrock batch inference.
        
        Args:
            texts_with_ids: List of (record_id, text) tuples
            
        Returns:
            List of (record_id, embedding_vector) tuples
        """
        if not texts_with_ids:
            return []
        
        total_texts = len(texts_with_ids)
        logger.info(f"Starting batch embedding generation for {total_texts} texts")
        
        # Generate unique job ID
        job_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        
        try:
            # Create JSONL input
            logger.info("Creating batch input JSONL...")
            jsonl_content = self.create_batch_input_jsonl(texts_with_ids)
            
            # Upload to S3
            logger.info("Uploading batch input to S3...")
            input_s3_uri = self.upload_batch_input(jsonl_content, job_id)
            
            # Submit batch job
            logger.info("Submitting batch inference job...")
            job_arn = self.submit_batch_job(input_s3_uri, job_id)
            
            # Wait for completion
            logger.info("Waiting for batch job to complete...")
            job_details = self.wait_for_job_completion(job_arn)
            
            # Download results
            logger.info("Downloading batch results...")
            results = self.download_batch_results(job_details)
            
            # Process results
            logger.info("Processing batch results...")
            embeddings = self.process_batch_results(results)
            
            logger.info(f"Batch embedding generation completed: {len(embeddings)}/{total_texts} successful")
            return embeddings
            
        except Exception as e:
            logger.error(f"Batch embedding generation failed: {e}")
            raise

    def validate_model_access(self) -> bool:
        """Validate that we can access the Bedrock model."""
        try:
            # List foundation models to check if our model is available
            response = self.bedrock.list_foundation_models()
            available_models = {model['modelId'] for model in response['modelSummaries']}
            
            if self.model_id in available_models:
                logger.info(f"Model {self.model_id} is available")
                return True
            else:
                logger.error(f"Model {self.model_id} is not available. Available models: {available_models}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to validate model access: {e}")
            return False

    def cleanup_batch_files(self, job_id: str):
        """Clean up batch input/output files from S3."""
        try:
            # Clean up input files
            input_key = f"bedrock-batch/input/{job_id}/"
            self._delete_s3_objects(self.input_bucket, input_key)
            
            # Clean up output files
            output_key = f"bedrock-batch/output/{job_id}/"
            self._delete_s3_objects(self.output_bucket, output_key)
            
            logger.info(f"Cleaned up batch files for job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to clean up batch files: {e}")

    def _delete_s3_objects(self, bucket: str, prefix: str):
        """Delete all objects with given prefix from S3 bucket."""
        try:
            response = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            if 'Contents' in response:
                objects = [{'Key': obj['Key']} for obj in response['Contents']]
                if objects:
                    self.s3.delete_objects(
                        Bucket=bucket,
                        Delete={'Objects': objects}
                    )
        except Exception as e:
            logger.warning(f"Failed to delete S3 objects at {bucket}/{prefix}: {e}")


class BedrockIndividualClient:
    """Individual embedding client with rate limiting for accounts without batch API access."""
    
    def __init__(self, region: str = None, model_id: str = None):
        self.region = region or os.environ.get('AWS_REGION', 'us-east-1')
        self.model_id = model_id or os.environ.get('BEDROCK_MODEL_ID', 'amazon.titan-embed-text-v2:0')
        self.output_dimensions = int(os.environ.get('BEDROCK_OUTPUT_DIMENSIONS', '256'))
        
        # Rate limiting configuration (200 RPM, 30K tokens per minute)
        self.max_rpm = int(os.environ.get('BEDROCK_MAX_RPM', '200'))
        self.max_tokens_per_minute = int(os.environ.get('BEDROCK_MAX_TOKENS_PER_MINUTE', '30000'))
        
        # Internal rate tracking
        self.requests_in_minute = []
        self.tokens_in_minute = []
        self.last_request_time = 0
        
        # AWS client
        self.bedrock_runtime = boto3.client('bedrock-runtime', region_name=self.region)
        
        logger.info(
            f"Initialized Bedrock individual client for {self.model_id} | "
            f"dimensions={self.output_dimensions}, region={self.region}, "
            f"max_rpm={self.max_rpm}, max_tokens_per_minute={self.max_tokens_per_minute}"
        )
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for rate limiting (rough approximation)."""
        return len(text.split()) + len(text) // 4  # Word count + character count / 4
    
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
        
        logger.info(f"Starting individual embedding generation for {len(texts_with_ids)} texts with rate limiting")
        
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
        
        logger.info(f"Individual embedding generation completed: {len(results)}/{len(texts_with_ids)} successful, {failed_count} failed")
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