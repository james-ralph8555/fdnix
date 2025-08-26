#!/usr/bin/env python3

import os
import sys
import logging
import asyncio
from typing import List, Dict, Any

from nixpkgs_extractor import NixpkgsExtractor
from duckdb_writer import DuckDBWriter
from embedding_generator import EmbeddingGenerator
from layer_publisher import LayerPublisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Enable debug logging specifically for bedrock_client
logging.getLogger("bedrock_client").setLevel(logging.DEBUG)
logger = logging.getLogger("fdnix.nixpkgs-indexer")


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "yes", "on"}


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

    # Optional publish layer: requires LAYER_ARN + S3 triplet
    if _truthy(os.environ.get("PUBLISH_LAYER")):
        missing = [k for k in ("LAYER_ARN", "ARTIFACTS_BUCKET", "DUCKDB_KEY", "AWS_REGION") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                "Layer publish requested but missing envs: " + ", ".join(missing)
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
            
            # Check for force rebuild flag
            force_rebuild = _truthy(os.environ.get("FORCE_REBUILD_EMBEDDINGS"))
            if force_rebuild:
                logger.info("Force rebuild enabled - will regenerate all embeddings")
            
            generator = EmbeddingGenerator()
            await generator.run(force_rebuild=force_rebuild)
            logger.info("Embedding generation completed successfully!")
        
        # Phase 3: Publish DuckDB layer (if requested)
        if _truthy(os.environ.get("PUBLISH_LAYER")):
            logger.info("=== LAYER PUBLISH PHASE ===")
            layer_arn = os.environ.get("LAYER_ARN", "").strip()
            bucket = os.environ.get("ARTIFACTS_BUCKET", "").strip()
            key = os.environ.get("DUCKDB_KEY", "").strip()

            publisher = LayerPublisher(region=os.environ.get("AWS_REGION"))
            publisher.publish_from_s3(bucket=bucket, key=key, layer_arn=layer_arn)

        logger.info("Indexing completed successfully!")
        return 0

    except Exception as exc:
        logger.exception("Error during nixpkgs indexing: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
