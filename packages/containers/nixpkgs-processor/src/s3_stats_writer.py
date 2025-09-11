import json
import logging
import brotli
from typing import Any, Dict, Optional

try:
    import boto3  # type: ignore
except Exception:  # pragma: no cover
    boto3 = None  # type: ignore

logger = logging.getLogger("fdnix.s3-stats-writer")


class S3StatsWriter:
    """Write compressed dependency statistics JSON to S3."""
    
    def __init__(
        self,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        region: Optional[str] = None,
        compression_level: int = 5  # Moderate compression level for fast performance
    ) -> None:
        self.s3_bucket = s3_bucket
        self.s3_key = s3_key
        self.region = region
        self.compression_level = compression_level

    def write_stats_json(self, graph_stats: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None:
        """Write comprehensive dependency statistics as compressed JSON to S3."""
        if not (self.s3_bucket and self.s3_key and self.region):
            logger.info("Stats S3 upload not configured; skipping stats output.")
            return
            
        if boto3 is None:
            logger.error("boto3 not available for stats S3 upload")
            return
        
        # Create comprehensive stats output
        output_data = {
            "metadata": metadata or {},
            "stats": graph_stats
        }
        
        # Convert to compact JSON (no pretty printing for efficiency)
        json_data = json.dumps(output_data, separators=(',', ':'), sort_keys=True)
        
        # Compress with brotli at moderate level for fast compression
        compressed_data = brotli.compress(
            json_data.encode('utf-8'),
            quality=self.compression_level
        )
        
        logger.info("Uploading compressed stats data to s3://%s/%s (compression: %d -> %d bytes)", 
                   self.s3_bucket, self.s3_key, len(json_data.encode('utf-8')), len(compressed_data))
        
        # Upload to S3 with appropriate content encoding
        s3 = boto3.client("s3", region_name=self.region)
        s3.put_object(
            Bucket=self.s3_bucket,
            Key=self.s3_key,
            Body=compressed_data,
            ContentType='application/json',
            ContentEncoding='br'  # Indicate brotli compression
        )
        
        logger.info("Stats JSON upload complete.")