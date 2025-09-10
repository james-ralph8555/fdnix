import json
import logging
import os
from datetime import datetime, timezone
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
        
    def write_raw_jsonl(self, raw_packages: List[Dict[str, Any]]) -> str:
        """Write raw package data as JSONL to S3.
        
        Args:
            raw_packages: List of raw package dictionaries from nix-eval-jobs
            
        Returns:
            S3 key where the data was written
        """
        if not raw_packages:
            logger.warning("No packages provided to write")
            return self.key
        
        # Create JSONL content
        jsonl_lines = []
        for package in raw_packages:
            jsonl_lines.append(json.dumps(package, separators=(',', ':')))
        
        jsonl_content = '\n'.join(jsonl_lines)
        
        # Add metadata as first line (comment-style)
        metadata = {
            "_metadata": {
                "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
                "nixpkgs_branch": "release-25.05", 
                "total_packages": len(raw_packages),
                "extractor_version": "fdnix-evaluator-v1"
            }
        }
        
        final_content = json.dumps(metadata) + '\n' + jsonl_content
        
        try:
            logger.info("Uploading raw JSONL to s3://%s/%s (%d packages, %.2f MB)", 
                       self.bucket, self.key, len(raw_packages), len(final_content) / 1024 / 1024)
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=self.key,
                Body=final_content.encode('utf-8'),
                ContentType='application/jsonl',
                Metadata={
                    'extraction-timestamp': metadata["_metadata"]["extraction_timestamp"],
                    'package-count': str(len(raw_packages)),
                    'nixpkgs-branch': metadata["_metadata"]["nixpkgs_branch"]
                }
            )
            
            logger.info("Successfully uploaded raw JSONL to S3: s3://%s/%s", self.bucket, self.key)
            return self.key
            
        except (ClientError, NoCredentialsError) as e:
            logger.error("Failed to upload JSONL to S3: %s", str(e))
            raise RuntimeError(f"S3 upload failed: {e}") from e
        except Exception as e:
            logger.error("Unexpected error during S3 upload: %s", str(e))
            raise RuntimeError(f"Unexpected S3 upload error: {e}") from e