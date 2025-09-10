import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    boto3 = None

logger = logging.getLogger("fdnix.s3-jsonl-writer")


class S3JsonlWriter:
    """Simple S3 writer for raw JSONL output from nix-eval-jobs."""
    
    def __init__(self, bucket: str, key: str, region: str = "us-east-1"):
        if not boto3:
            raise RuntimeError("boto3 is required for S3 operations")
        
        self.bucket = bucket
        self.key = key
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)
        
    def write_jsonl_file(self, jsonl_file_path: str) -> str:
        """Upload JSONL file directly to S3.
        
        Args:
            jsonl_file_path: Path to the JSONL file to upload
            
        Returns:
            S3 key where the data was written
        """
        jsonl_path = Path(jsonl_file_path)
        if not jsonl_path.exists():
            raise FileNotFoundError(f"JSONL file not found: {jsonl_file_path}")
        
        # Get file size for logging
        file_size = jsonl_path.stat().st_size
        
        # Count lines to estimate package count (excluding metadata line if present)
        package_count = 0
        try:
            with jsonl_path.open('r', encoding='utf-8') as f:
                for line in f:
                    if line.strip() and not line.strip().startswith('{"_metadata"'):
                        package_count += 1
        except Exception as e:
            logger.warning("Could not count packages in JSONL file: %s", str(e))
            package_count = 0
        
        # Add metadata as first line
        metadata = {
            "_metadata": {
                "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
                "nixpkgs_branch": "release-25.05", 
                "total_packages": package_count,
                "extractor_version": "fdnix-evaluator-v1",
                "original_file": str(jsonl_path.name)
            }
        }
        
        try:
            logger.info("Uploading JSONL file to s3://%s/%s (~%d packages, %.2f MB)", 
                       self.bucket, self.key, package_count, file_size / 1024 / 1024)
            
            # Read original file and prepend metadata
            with jsonl_path.open('r', encoding='utf-8') as f:
                original_content = f.read()
            
            final_content = json.dumps(metadata) + '\n' + original_content
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=self.key,
                Body=final_content.encode('utf-8'),
                ContentType='application/jsonl',
                Metadata={
                    'extraction-timestamp': metadata["_metadata"]["extraction_timestamp"],
                    'package-count': str(package_count),
                    'nixpkgs-branch': metadata["_metadata"]["nixpkgs_branch"],
                    'original-file': metadata["_metadata"]["original_file"]
                }
            )
            
            logger.info("Successfully uploaded JSONL file to S3: s3://%s/%s", self.bucket, self.key)
            return self.key
            
        except (ClientError, NoCredentialsError) as e:
            logger.error("Failed to upload JSONL to S3: %s", str(e))
            raise RuntimeError(f"S3 upload failed: {e}") from e
        except Exception as e:
            logger.error("Unexpected error during S3 upload: %s", str(e))
            raise RuntimeError(f"Unexpected S3 upload error: {e}") from e
