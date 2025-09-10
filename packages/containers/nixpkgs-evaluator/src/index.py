#!/usr/bin/env python3

import os
import sys
import logging
import time
from typing import Optional

from nixpkgs_extractor import NixpkgsExtractor
from s3_jsonl_writer import S3JsonlWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("fdnix.nixpkgs-evaluator")


def validate_env() -> None:
    """Validate environment variables for Stage 1 (evaluator)."""
    required_vars = ["ARTIFACTS_BUCKET", "AWS_REGION"]
    missing = [var for var in required_vars if not os.environ.get(var)]
    
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    
    # Generate default output key if not provided
    if not os.environ.get("JSONL_OUTPUT_KEY"):
        timestamp = int(time.time())
        os.environ["JSONL_OUTPUT_KEY"] = f"evaluations/{timestamp}/nixpkgs-raw.jsonl"
        logger.info("Generated output key: %s", os.environ["JSONL_OUTPUT_KEY"])


def main() -> int:
    """Main entry point for Stage 1: nixpkgs evaluation."""
    logger.info("Starting fdnix nixpkgs-evaluator (Stage 1)...")
    
    try:
        validate_env()
        
        bucket = os.environ["ARTIFACTS_BUCKET"]
        region = os.environ["AWS_REGION"]
        output_key = os.environ["JSONL_OUTPUT_KEY"]
        
        logger.info("Configuration:")
        logger.info("  S3 Bucket: %s", bucket)
        logger.info("  S3 Region: %s", region)
        logger.info("  Output Key: %s", output_key)
        
        # Phase 1: Extract raw packages using nix-eval-jobs
        logger.info("=== NIXPKGS EXTRACTION PHASE ===")
        extractor = NixpkgsExtractor()
        raw_packages = extractor.extract_all_packages()
        
        if not raw_packages:
            logger.warning("No packages extracted! This may indicate an issue.")
            return 1
            
        logger.info("Successfully extracted %d packages", len(raw_packages))
        
        # Phase 2: Upload raw JSONL to S3
        logger.info("=== S3 UPLOAD PHASE ===")
        writer = S3JsonlWriter(bucket=bucket, key=output_key, region=region)
        uploaded_key = writer.write_raw_jsonl(raw_packages)
        
        # Output the key for the next stage (can be picked up by Step Functions)
        logger.info("=== STAGE 1 COMPLETED SUCCESSFULLY ===")
        logger.info("Raw JSONL uploaded to: s3://%s/%s", bucket, uploaded_key)
        logger.info("Next stage should read from: %s", uploaded_key)
        
        # Write output for Step Functions integration
        output_info = {
            "status": "success",
            "bucket": bucket,
            "jsonl_key": uploaded_key,
            "package_count": len(raw_packages),
            "timestamp": time.time()
        }
        
        # Output JSON for Step Functions to consume
        import json
        print("EVALUATOR_OUTPUT:", json.dumps(output_info))
        
        return 0
        
    except Exception as exc:
        logger.exception("Error during nixpkgs evaluation: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())