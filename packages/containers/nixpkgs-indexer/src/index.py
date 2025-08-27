#!/usr/bin/env python3

import os
import sys
import logging
import asyncio
from typing import List, Dict, Any

from nixpkgs_extractor import NixpkgsExtractor
from duckdb_writer import DuckDBWriter
from minified_db_writer import MinifiedDuckDBWriter
from embedding_generator import EmbeddingGenerator
from layer_publisher import LayerPublisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Keep bedrock_client at INFO by default; no forced DEBUG noise
logger = logging.getLogger("fdnix.nixpkgs-indexer")


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "yes", "on"}


def validate_env() -> None:
    """Validate environment variables"""
    # Optional S3 upload: requires bucket and region, plus at least one key
    has_bucket = bool(os.environ.get("ARTIFACTS_BUCKET"))
    has_data_key = bool(os.environ.get("DUCKDB_DATA_KEY"))
    has_minified_key = bool(os.environ.get("DUCKDB_MINIFIED_KEY"))
    has_region = bool(os.environ.get("AWS_REGION"))
    
    if has_bucket or has_data_key or has_minified_key or has_region:
        required_basic = [k for k in ("ARTIFACTS_BUCKET", "AWS_REGION") if not os.environ.get(k)]
        if required_basic:
            raise RuntimeError(
                "S3 upload requested but missing envs: " + ", ".join(required_basic)
            )
        
        # Set default keys if not provided but S3 upload is configured
        if not has_data_key:
            os.environ["DUCKDB_DATA_KEY"] = "fdnix-data.duckdb"
        if not has_minified_key:
            os.environ["DUCKDB_MINIFIED_KEY"] = "fdnix.duckdb"

    # Optional publish layer: requires LAYER_ARN + S3 triplet with minified key
    if _truthy(os.environ.get("PUBLISH_LAYER")):
        required_keys = ["LAYER_ARN", "ARTIFACTS_BUCKET", "AWS_REGION", "DUCKDB_MINIFIED_KEY"]
        missing = [k for k in required_keys if not os.environ.get(k)]
            
        if missing:
            raise RuntimeError(
                "Layer publish requested but missing envs: " + ", ".join(missing)
            )


async def main() -> int:
    logger.info("Starting fdnix nixpkgs-indexer...")
    try:
        validate_env()
        
        processing_mode = os.environ.get("PROCESSING_MODE", "both").lower()
        # Map aliases
        if processing_mode in ("all", "full"):
            processing_mode = "both"
        
        # Setup paths for both databases
        main_db_path = os.environ.get("OUTPUT_PATH", "/out/fdnix-data.duckdb")
        minified_db_path = os.environ.get("OUTPUT_MINIFIED_PATH", "/out/fdnix.duckdb")
        
        # Phase 1: Metadata Generation (if requested)
        if processing_mode in ("metadata", "both"):
            logger.info("=== METADATA GENERATION PHASE ===")
            
            extractor = NixpkgsExtractor()
            
            # Create main database with all metadata (upload to data key)
            main_writer = DuckDBWriter(
                output_path=main_db_path,
                s3_bucket=os.environ.get("ARTIFACTS_BUCKET"),
                s3_key=os.environ.get("DUCKDB_DATA_KEY"),
                region=os.environ.get("AWS_REGION"),
            )

            logger.info("Extracting nixpkgs metadata...")
            packages: List[Dict[str, Any]] = extractor.extract_all_packages()
            logger.info("Extracted %d packages from nixpkgs", len(packages))

            logger.info("Writing metadata to main DuckDB artifact...")
            main_writer.write_artifact(packages)
            logger.info("Main database generation completed successfully!")
        
        # Phase 2: Embedding Generation (if needed and enabled)
        enable_embeddings = _truthy(os.environ.get("ENABLE_EMBEDDINGS", "true"))  # Default to enabled
        if processing_mode in ("embedding", "both") and enable_embeddings:
            logger.info("=== EMBEDDING GENERATION PHASE ===")
            
            # Set required environment for embedding generator
            if not os.environ.get("BEDROCK_MODEL_ID"):
                os.environ["BEDROCK_MODEL_ID"] = "amazon.titan-embed-text-v2:0"
            if not os.environ.get("BEDROCK_OUTPUT_DIMENSIONS"):
                os.environ["BEDROCK_OUTPUT_DIMENSIONS"] = "256"
            
            # Always use the main database for embeddings
            if not os.environ.get("DUCKDB_PATH"):
                os.environ["DUCKDB_PATH"] = main_db_path
            
            # Check for force rebuild flag
            force_rebuild = _truthy(os.environ.get("FORCE_REBUILD_EMBEDDINGS"))
            if force_rebuild:
                logger.info("Force rebuild enabled - will regenerate all embeddings")
            
            # Ensure S3 key is set for main DB during embedding stage
            if os.environ.get("ARTIFACTS_BUCKET") and not os.environ.get("DUCKDB_DATA_KEY"):
                os.environ["DUCKDB_DATA_KEY"] = "fdnix-data.duckdb"

            generator = EmbeddingGenerator()
            await generator.run(force_rebuild=force_rebuild)
            logger.info("Embedding generation completed successfully!")
        elif processing_mode in ("embedding", "both"):
            logger.info("=== EMBEDDING GENERATION SKIPPED (EMBEDDINGS DISABLED) ===")
            logger.info("ENABLE_EMBEDDINGS is set to false, skipping embedding generation")

        # Phase 3: Minified Database Generation (if requested)
        if processing_mode in ("minified", "both"):
            logger.info("=== MINIFIED DATABASE GENERATION PHASE ===")
            minified_writer = MinifiedDuckDBWriter(
                output_path=minified_db_path,
                s3_bucket=os.environ.get("ARTIFACTS_BUCKET"),
                s3_key=os.environ.get("DUCKDB_MINIFIED_KEY"),
                region=os.environ.get("AWS_REGION"),
            )
            logger.info("Creating minified database from main database (with embeddings if present)...")
            minified_writer.create_minified_db_from_main(main_db_path)
            logger.info("Minified database generation completed successfully!")
        
        # Phase 3: Publish DuckDB layer (if requested)
        if _truthy(os.environ.get("PUBLISH_LAYER")):
            logger.info("=== LAYER PUBLISH PHASE ===")
            layer_arn = os.environ.get("LAYER_ARN", "").strip()
            bucket = os.environ.get("ARTIFACTS_BUCKET", "").strip()
            
            # Use minified key for layer publishing
            key = os.environ.get("DUCKDB_MINIFIED_KEY", "")
            if not key:
                raise RuntimeError("DUCKDB_MINIFIED_KEY required for layer publishing")

            publisher = LayerPublisher(region=os.environ.get("AWS_REGION"))
            publisher.publish_from_s3(bucket=bucket, key=key, layer_arn=layer_arn)
            logger.info("Layer published using minified database from key: %s", key)

        logger.info("Indexing completed successfully!")
        return 0

    except Exception as exc:
        logger.exception("Error during nixpkgs indexing: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
