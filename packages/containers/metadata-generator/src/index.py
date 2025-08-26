#!/usr/bin/env python3

import os
import sys
import logging
from typing import List, Dict, Any

from nixpkgs_extractor import NixpkgsExtractor
from duckdb_writer import DuckDBWriter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("fdnix.metadata-generator")


def validate_env() -> None:
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


def main() -> int:
    logger.info("Starting fdnix metadata generation process...")
    try:
        validate_env()

        extractor = NixpkgsExtractor()
        writer = DuckDBWriter(
            output_path=os.environ.get("OUTPUT_PATH", "/out/fdnix.duckdb"),
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
        return 0

    except Exception as exc:
        logger.exception("Error during metadata generation: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
