import logging
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


class DynamoDBWriter:
    def __init__(self, table_name: str, region: str) -> None:
        self.table_name = table_name
        self.region = region
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)
        self.batch_size = 25  # DynamoDB batch write limit

    def batch_write_packages(self, packages: List[Dict[str, Any]]) -> None:
        logger.info("Starting batch write of %d packages...", len(packages))

        processed = 0
        errors = 0

        for i in range(0, len(packages), self.batch_size):
            batch = packages[i : i + self.batch_size]
            try:
                self._write_batch(batch)
                processed += len(batch)
                if processed % 100 == 0:
                    logger.info("Written %d/%d packages...", processed, len(packages))
            except Exception as e:
                logger.error("Failed to write batch %d-%d: %s", i, i + len(batch), str(e))
                # attempt individual writes
                for pkg in batch:
                    try:
                        self._write_single(pkg)
                        processed += 1
                    except Exception as ie:
                        errors += 1
                        logger.warning(
                            "Failed to write individual package %s: %s",
                            pkg.get("packageName", "<unknown>"),
                            str(ie),
                        )

        logger.info("Batch write completed. Success: %d, Errors: %d", processed, errors)
        if errors > 0:
            logger.warning("%d packages failed to write", errors)

    def _write_batch(self, items: List[Dict[str, Any]]) -> None:
        # boto3 batch_writer handles retries for unprocessed items
        with self.table.batch_writer(overwrite_by_pkeys=["packageName", "version"]) as batch:
            for item in items:
                batch.put_item(Item=self._serialize(item))

    def _write_single(self, item: Dict[str, Any]) -> None:
        self.table.put_item(Item=self._serialize(item))

    def _serialize(self, pkg: Dict[str, Any]) -> Dict[str, Any]:
        # Ensure presence and proper types; DynamoDB via boto3 will map dicts/lists appropriately
        return {
            "packageName": pkg.get("packageName"),
            "version": pkg.get("version"),
            "attributePath": pkg.get("attributePath", ""),
            "description": pkg.get("description", ""),
            "longDescription": pkg.get("longDescription", ""),
            "homepage": pkg.get("homepage", ""),
            "license": pkg.get("license"),
            "platforms": pkg.get("platforms", []),
            "maintainers": pkg.get("maintainers", []),
            "broken": bool(pkg.get("broken", False)),
            "unfree": bool(pkg.get("unfree", False)),
            "available": bool(pkg.get("available", True)),
            "insecure": bool(pkg.get("insecure", False)),
            "unsupported": bool(pkg.get("unsupported", False)),
            "mainProgram": pkg.get("mainProgram", ""),
            "position": pkg.get("position", ""),
            "outputsToInstall": pkg.get("outputsToInstall", []),
            "lastUpdated": pkg.get("lastUpdated"),
            "hasEmbedding": bool(pkg.get("hasEmbedding", False)),
        }

