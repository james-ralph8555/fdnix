import logging
import os
from typing import Optional

try:
    import boto3  # type: ignore
except Exception:  # pragma: no cover - boto3 may be absent in local-only runs
    boto3 = None  # type: ignore


logger = logging.getLogger("fdnix.layer-publisher")


class LayerPublisher:
    """Publishes a new Lambda Layer version from an S3 object.

    Expects the DuckDB artifact to be available in S3 and publishes it as a new
    layer version using the unversioned Layer ARN (name) passed in.
    """

    def __init__(self, region: Optional[str] = None) -> None:
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not self.region:
            logger.warning("AWS region not provided; falling back to default client config")

    def publish_from_s3(self, *, bucket: str, key: str, layer_arn: str) -> str:
        """Publish a new layer version and return the LayerVersionArn.

        Args:
            bucket: S3 bucket containing the DuckDB object
            key: S3 key for the DuckDB object
            layer_arn: Unversioned layer ARN or name (e.g., arn:aws:lambda:...:layer:fdnix-database-layer)
        """
        if not boto3:
            raise RuntimeError("boto3 is not available but required for layer publishing")

        if not bucket or not key or not layer_arn:
            raise ValueError("bucket, key, and layer_arn are required to publish a layer")

        logger.info("Publishing new layer version from s3://%s/%s to %s", bucket, key, layer_arn)
        lambda_client = boto3.client("lambda", region_name=self.region)

        try:
            resp = lambda_client.publish_layer_version(
                LayerName=layer_arn,
                Description="DuckDB database file for fdnix search API",
                Content={"S3Bucket": bucket, "S3Key": key},
                CompatibleRuntimes=["provided.al2023"],
                CompatibleArchitectures=["arm64"],
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to publish layer version: %s", e)
            raise

        arn = resp.get("LayerVersionArn") or ""
        version = resp.get("Version")
        logger.info("Published layer version: %s (version %s)", arn, version)
        return arn

