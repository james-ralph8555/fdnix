import os
import sys
import logging

from .nixpkgs_extractor import NixpkgsExtractor
from .dynamodb_writer import DynamoDBWriter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("fdnix.metadata-generator")


def main() -> None:
    logger.info("Starting fdnix metadata generation process...")

    required_env = ["DYNAMODB_TABLE", "AWS_REGION"]
    missing = [k for k in required_env if not os.getenv(k)]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    table_name = os.environ["DYNAMODB_TABLE"]
    region = os.environ["AWS_REGION"]

    try:
        extractor = NixpkgsExtractor()
        writer = DynamoDBWriter(table_name=table_name, region=region)

        logger.info("Extracting nixpkgs metadata...")
        packages = extractor.extract_all_packages()
        logger.info("Extracted %d packages from nixpkgs", len(packages))

        logger.info("Writing metadata to DynamoDB...")
        writer.batch_write_packages(packages)

        logger.info("Metadata generation completed successfully!")
    except Exception as e:
        logger.exception("Error during metadata generation: %s", str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()

