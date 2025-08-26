#!/usr/bin/env python3

import os
import sys
import logging
import asyncio
from typing import List, Dict, Any

from nixpkgs_extractor import NixpkgsExtractor
from duckdb_writer import DuckDBWriter
from embedding_generator import EmbeddingGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("fdnix.nixpkgs-indexer")


def validate_env() -> None:
    """Validate environment variables"""
    # Optional S3 upload: requires bucket, key, and region
    has_bucket = bool(os.environ.get("ARTIFACTS_BUCKET"))
    has_key = bool(os.environ.get("DUCKDB_KEY"))
    has_region = bool(os.environ.get("AWS_REGION"))
    if has_bucket or has_key or has_region:
        required = [
            k
            for k in ("ARTIFACTS_BUCKET", "DUCKDB_KEY", "AWS_REGION")
            if not os.environ.get(k)
        ]
        if required:
            raise RuntimeError(
                "S3 upload requested but missing envs: " + ", ".join(required)
            )


async def main() -> int:
    logger.info("Starting fdnix nixpkgs-indexer...")
    try:
        validate_env()
        
        processing_mode = os.environ.get("PROCESSING_MODE", "both").lower()
        output_path = os.environ.get("OUTPUT_PATH", "/out/fdnix.duckdb")
        
        # Phase 1: Metadata Generation (if needed)
        if processing_mode in ("metadata", "both"):
            logger.info("=== METADATA GENERATION PHASE ===")
            
            extractor = NixpkgsExtractor()
            writer = DuckDBWriter(
                output_path=output_path,
                s3_bucket=os.environ.get("ARTIFACTS_BUCKET"),
                s3_key=os.environ.get("DUCKDB_KEY"),
                region=os.environ.get("AWS_REGION"),
            )

            logger.info("Extracting nixpkgs metadata...")
            packages: List[Dict[str, Any]] = extractor.extract_all_packages()
            logger.info("Extracted %d packages from nixpkgs", len(packages))

            logger.info("Writing metadata to DuckDB artifact...")
            writer.write_artifact(packages)
            logger.info("Metadata generation completed successfully!")
        
        # Phase 2: Embedding Generation (if needed)
        if processing_mode in ("embedding", "both"):
            logger.info("=== EMBEDDING GENERATION PHASE ===")
            
            # Set required environment for embedding generator
            if not os.environ.get("BEDROCK_MODEL_ID"):
                os.environ["BEDROCK_MODEL_ID"] = "cohere.embed-english-v3"
            if not os.environ.get("DUCKDB_PATH"):
                os.environ["DUCKDB_PATH"] = output_path
            
            generator = EmbeddingGenerator()
            await generator.run()
            logger.info("Embedding generation completed successfully!")
        
        logger.info("Indexing completed successfully!")
        return 0

    except Exception as exc:
        logger.exception("Error during nixpkgs indexing: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
