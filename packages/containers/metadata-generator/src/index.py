#!/usr/bin/env python3

import os
import sys
import logging
from typing import List, Dict, Any

from nixpkgs_extractor import NixpkgsExtractor
from dynamodb_writer import DynamoDBWriter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("fdnix.metadata-generator")


def validate_env() -> None:
    required = ["DYNAMODB_TABLE", "AWS_REGION"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def main() -> int:
    logger.info("Starting fdnix metadata generation process...")
    try:
        validate_env()

        extractor = NixpkgsExtractor()
        writer = DynamoDBWriter(
            table_name=os.environ["DYNAMODB_TABLE"],
            region=os.environ["AWS_REGION"],
        )

        logger.info("Extracting nixpkgs metadata...")
        packages: List[Dict[str, Any]] = extractor.extract_all_packages()
        logger.info("Extracted %d packages from nixpkgs", len(packages))

        logger.info("Writing metadata to DynamoDB...")
        writer.batch_write_packages(packages)
        logger.info("Metadata generation completed successfully!")
        return 0

    except Exception as exc:
        logger.exception("Error during metadata generation: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

