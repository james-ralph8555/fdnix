#!/usr/bin/env python3

import os
import sys
import logging
import asyncio
from typing import List, Dict, Any

from s3_jsonl_reader import S3JsonlReader
from data_processor import DataProcessor
from lancedb_writer import LanceDBWriter
from s3_stats_writer import S3StatsWriter
from embedding_generator import EmbeddingGenerator
from layer_publisher import LayerPublisher
from node_s3_writer import NodeS3Writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("fdnix.nixpkgs-processor")


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "yes", "on"}


def validate_env() -> None:
    """Validate environment variables for Stage 2 (processor)."""
    # Required for reading Stage 1 output
    required_basic = ["ARTIFACTS_BUCKET", "PROCESSED_FILES_BUCKET", "AWS_REGION", "JSONL_INPUT_KEY"]
    missing_basic = [k for k in required_basic if not os.environ.get(k)]
    
    if missing_basic:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing_basic)}")
    
    # Set default keys for outputs if not provided
    has_data_key = bool(os.environ.get("LANCEDB_DATA_KEY"))
    has_minified_key = bool(os.environ.get("LANCEDB_MINIFIED_KEY"))
    
    if not has_data_key or not has_minified_key:
        import time
        timestamp = int(time.time())
        if not has_data_key:
            os.environ["LANCEDB_DATA_KEY"] = f"snapshots/fdnix-data-{timestamp}.lancedb"
        if not has_minified_key:
            os.environ["LANCEDB_MINIFIED_KEY"] = f"snapshots/fdnix-{timestamp}.lancedb"
    
    # Set default stats key if not set
    if not os.environ.get("STATS_S3_KEY"):
        import time
        timestamp = int(time.time())
        os.environ["STATS_S3_KEY"] = f"stats/fdnix-stats-{timestamp}.json"
    
    # Set default node S3 prefix if not set
    if not os.environ.get("NODE_S3_PREFIX"):
        os.environ["NODE_S3_PREFIX"] = "nodes/"

    # Optional publish layer: requires LAYER_ARN + S3 triplet with minified key
    if _truthy(os.environ.get("PUBLISH_LAYER")):
        required_keys = ["LAYER_ARN", "ARTIFACTS_BUCKET", "AWS_REGION", "LANCEDB_MINIFIED_KEY"]
        missing = [k for k in required_keys if not os.environ.get(k)]
            
        if missing:
            raise RuntimeError(
                "Layer publish requested but missing envs: " + ", ".join(missing)
            )


async def main() -> int:
    """Main entry point for Stage 2: nixpkgs data processing."""
    logger.info("Starting fdnix nixpkgs-processor (Stage 2)...")
    
    try:
        validate_env()
        
        artifacts_bucket = os.environ["ARTIFACTS_BUCKET"]
        processed_files_bucket = os.environ["PROCESSED_FILES_BUCKET"]
        region = os.environ["AWS_REGION"]
        jsonl_input_key = os.environ["JSONL_INPUT_KEY"]
        
        logger.info("Configuration:")
        logger.info("  Artifacts S3 Bucket: %s", artifacts_bucket)
        logger.info("  Processed Files S3 Bucket: %s", processed_files_bucket)
        logger.info("  S3 Region: %s", region)
        logger.info("  JSONL Input Key: %s", jsonl_input_key)
        
        processing_mode = os.environ.get("PROCESSING_MODE", "both").lower()
        # Map aliases
        if processing_mode in ("all", "full"):
            processing_mode = "both"
        
        # Setup paths for both databases
        main_db_path = os.environ.get("OUTPUT_PATH", "/out/fdnix-data.lancedb")
        minified_db_path = os.environ.get("OUTPUT_MINIFIED_PATH", "/out/fdnix.lancedb")
        
        # Phase 1: Read raw JSONL from Stage 1 (from artifacts bucket)
        logger.info("=== JSONL READING PHASE ===")
        reader = S3JsonlReader(bucket=artifacts_bucket, key=jsonl_input_key, region=region)
        raw_packages, metadata = reader.read_raw_jsonl()
        
        if not raw_packages:
            logger.warning("No packages found in JSONL! This may indicate an issue.")
            return 1
            
        logger.info("Successfully read %d packages from Stage 1", len(raw_packages))
        
        # Phase 2: Data Processing (if requested)
        packages = None
        graph_data = None
        
        if processing_mode in ("metadata", "both"):
            logger.info("=== DATA PROCESSING PHASE ===")
            
            processor = DataProcessor()
            
            # Check if we need dependency graph processing for node S3 files or stats
            enable_node_s3 = _truthy(os.environ.get("ENABLE_NODE_S3", "true"))  # Default enabled
            enable_stats = _truthy(os.environ.get("ENABLE_STATS", "true"))  # Default enabled
            
            if enable_node_s3 or enable_stats:
                # Use enhanced processing with dependency graph
                packages, graph_data = processor.process_with_dependency_graph(raw_packages)
            else:
                # Use standard processing (original behavior)
                packages = processor.process_raw_packages(raw_packages)
            
            # Create main database with metadata only (upload to artifacts bucket)
            main_writer = LanceDBWriter(
                output_path=main_db_path,
                s3_bucket=artifacts_bucket,
                s3_key=os.environ.get("LANCEDB_DATA_KEY"),
                region=region,
            )

            logger.info("Writing metadata to main LanceDB artifact...")
            main_writer.write_artifact(packages)
            
            # Write comprehensive stats data to S3 (to processed files bucket)
            if graph_data and os.environ.get("STATS_S3_KEY"):
                logger.info("Writing comprehensive stats data to processed files bucket...")
                stats_s3_writer = S3StatsWriter(
                    s3_bucket=processed_files_bucket,
                    s3_key=os.environ.get("STATS_S3_KEY"), 
                    region=region
                )
                stats_metadata = {
                    "extraction_timestamp": metadata.get("extraction_timestamp", "unknown"),
                    "nixpkgs_branch": metadata.get("nixpkgs_branch", "unknown"),
                    "total_packages": len(packages)
                }
                graph_stats = graph_data.get("graph_stats", {})
                stats_s3_writer.write_stats_json(graph_stats, stats_metadata)
                logger.info("Comprehensive stats data uploaded to S3!")
            
            logger.info("Main database generation completed successfully!")
        
        # Phase 3: Embedding Generation (if needed and enabled)
        enable_embeddings = _truthy(os.environ.get("ENABLE_EMBEDDINGS", "true"))  # Default to enabled
        if processing_mode in ("embedding", "both") and enable_embeddings:
            logger.info("=== EMBEDDING GENERATION PHASE ===")
            
            # Set required environment for embedding generator
            if not os.environ.get("BEDROCK_MODEL_ID"):
                os.environ["BEDROCK_MODEL_ID"] = "amazon.titan-embed-text-v2:0"
            if not os.environ.get("BEDROCK_OUTPUT_DIMENSIONS"):
                os.environ["BEDROCK_OUTPUT_DIMENSIONS"] = "256"
            
            # Always use the main database for embeddings
            if not os.environ.get("LANCEDB_PATH"):
                os.environ["LANCEDB_PATH"] = main_db_path
            
            # Check for force rebuild flag
            force_rebuild = _truthy(os.environ.get("FORCE_REBUILD_EMBEDDINGS"))
            if force_rebuild:
                logger.info("Force rebuild enabled - will regenerate all embeddings")
            
            # Ensure S3 key is set for main DB during embedding stage
            if not os.environ.get("LANCEDB_DATA_KEY"):
                os.environ["LANCEDB_DATA_KEY"] = "fdnix-data.lancedb"

            generator = EmbeddingGenerator()
            await generator.run(force_rebuild=force_rebuild)
            logger.info("Embedding generation completed successfully!")
        elif processing_mode in ("embedding", "both"):
            logger.info("=== EMBEDDING GENERATION SKIPPED (EMBEDDINGS DISABLED) ===")
            logger.info("ENABLE_EMBEDDINGS is set to false, skipping embedding generation")

        # Phase 4: Minified Database Generation (if requested)
        if processing_mode in ("minified", "both"):
            logger.info("=== MINIFIED DATABASE GENERATION PHASE ===")
            minified_writer = LanceDBWriter(
                output_path=minified_db_path,
                s3_bucket=artifacts_bucket,
                s3_key=os.environ.get("LANCEDB_MINIFIED_KEY"),
                region=region,
            )
            logger.info("Creating minified database from main database (with embeddings if present)...")
            minified_writer.create_minified_db_from_main(main_db_path)
            logger.info("Minified database generation completed successfully!")
        
        # Phase 5: Individual Node S3 Writing (if requested and graph data available)
        enable_node_s3 = _truthy(os.environ.get("ENABLE_NODE_S3", "true"))
        if enable_node_s3 and packages and graph_data:
            logger.info("=== INDIVIDUAL NODE S3 WRITING PHASE ===")
            
            node_s3_prefix = os.environ.get("NODE_S3_PREFIX", "nodes/")
            node_writer = NodeS3Writer(
                s3_bucket=processed_files_bucket,
                s3_prefix=node_s3_prefix,
                region=region,
                clear_existing=_truthy(os.environ.get("CLEAR_EXISTING_NODES", "true")),
                max_workers=int(os.environ.get("NODE_S3_MAX_WORKERS", "10"))
            )
            
            # Prepare metadata for node files
            node_metadata = {
                "extraction_timestamp": metadata.get("extraction_timestamp", "unknown"),
                "nixpkgs_branch": metadata.get("nixpkgs_branch", "unknown"),
                "total_packages": len(packages)
            }
            
            # Write individual node files with dependency information
            dependency_data = graph_data.get("dependency_data", {})
            node_writer.write_nodes(packages, dependency_data, node_metadata)
            
            # Create index file for the frontend
            graph_stats = graph_data.get("graph_stats", {})
            node_writer.create_index_file(packages, graph_stats, node_metadata)
            
            # Log final statistics
            upload_stats = node_writer.get_upload_stats()
            logger.info("Node S3 writing completed: %d successful, %d errors", 
                       upload_stats.get('success', 0), upload_stats.get('errors', 0))
        elif enable_node_s3:
            logger.info("=== INDIVIDUAL NODE S3 WRITING SKIPPED ===")
            logger.info("Node S3 writing requested but no graph data available (check ENABLE_NODE_S3 and data processing)")
        
        # Phase 6: Publish LanceDB layer (if requested)
        if _truthy(os.environ.get("PUBLISH_LAYER")):
            logger.info("=== LAYER PUBLISH PHASE ===")
            layer_arn = os.environ.get("LAYER_ARN", "").strip()
            
            # Use minified key for layer publishing (from artifacts bucket)
            key = os.environ.get("LANCEDB_MINIFIED_KEY", "")
            if not key:
                raise RuntimeError("LANCEDB_MINIFIED_KEY required for layer publishing")

            publisher = LayerPublisher(region=region)
            publisher.publish_from_s3(bucket=artifacts_bucket, key=key, layer_arn=layer_arn)
            logger.info("Layer published using minified database from key: %s", key)

        logger.info("=== STAGE 2 COMPLETED SUCCESSFULLY ===")
        logger.info("Processing completed successfully!")
        return 0

    except Exception as exc:
        logger.exception("Error during nixpkgs processing: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))