import json
import logging
from typing import Any, Dict, List, Tuple

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    import brotli
except ImportError:
    boto3 = None
    brotli = None

logger = logging.getLogger("fdnix.s3-jsonl-reader")


class S3JsonlReader:
    """S3 reader for raw JSONL data from Stage 1 evaluator."""
    
    def __init__(self, bucket: str, key: str, region: str = "us-east-1"):
        if not boto3:
            raise RuntimeError("boto3 is required for S3 operations")
        if not brotli:
            raise RuntimeError("brotli is required for compressed file support")
        
        self.bucket = bucket
        self.key = key
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)
        
    def read_raw_jsonl(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Read raw package data from brotli-compressed S3 JSONL file.
        
        Returns:
            Tuple of (raw_packages, metadata)
        """
        try:
            # Ensure the file has .br extension for brotli compression
            if not self.key.endswith('.br'):
                raise RuntimeError(f"Only brotli-compressed files (.br) are supported. Got: {self.key}")
            
            logger.info("Downloading brotli-compressed JSONL from s3://%s/%s", self.bucket, self.key)
            
            # Download the compressed file
            response = self.s3_client.get_object(Bucket=self.bucket, Key=self.key)
            compressed_data = response['Body'].read()
            
            # Decompress with brotli
            content = brotli.decompress(compressed_data).decode('utf-8')
            
            logger.info("Downloaded and decompressed %.2f MB of JSONL data", len(content) / 1024 / 1024)
            
            # Parse JSONL content
            lines = content.strip().split('\n')
            if not lines:
                raise RuntimeError("Empty JSONL file")
            
            # First line should be metadata
            metadata = {}
            raw_packages = []
            
            for line_num, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # Check if this is metadata (first line)
                    if line_num == 1 and "_metadata" in data:
                        metadata = data["_metadata"]
                        logger.info("Found metadata: %s", metadata)
                    else:
                        raw_packages.append(data)
                        
                except json.JSONDecodeError as e:
                    logger.warning("Failed to parse JSON at line %d: %s", line_num, str(e)[:100])
                    continue
            
            logger.info("Successfully parsed %d packages from JSONL", len(raw_packages))
            
            if metadata:
                logger.info("Metadata: extracted %s packages from %s at %s", 
                          metadata.get("total_packages"), 
                          metadata.get("nixpkgs_branch"),
                          metadata.get("extraction_timestamp"))
            
            return raw_packages, metadata
            
        except (ClientError, NoCredentialsError) as e:
            logger.error("Failed to download JSONL from S3: %s", str(e))
            raise RuntimeError(f"S3 download failed: {e}") from e
        except Exception as e:
            logger.error("Unexpected error during S3 download: %s", str(e))
            raise RuntimeError(f"Unexpected S3 download error: {e}") from e