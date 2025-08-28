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

    Expects the LanceDB artifact to be available in S3 and publishes it as a new
    layer version using the unversioned Layer ARN (name) passed in.
    """

    def __init__(self, region: Optional[str] = None) -> None:
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not self.region:
            logger.warning("AWS region not provided; falling back to default client config")

    def publish_from_s3(self, *, bucket: str, key: str, layer_arn: str) -> str:
        """Publish a new layer version and return the LayerVersionArn.

        Args:
            bucket: S3 bucket containing the LanceDB directory structure
            key: S3 key prefix for the LanceDB directory structure
            layer_arn: Unversioned layer ARN or name (e.g., arn:aws:lambda:...:layer:fdnix-database-layer)
        """
        if not boto3:
            raise RuntimeError("boto3 is not available but required for layer publishing")

        if not bucket or not key or not layer_arn:
            raise ValueError("bucket, key, and layer_arn are required to publish a layer")

        logger.info("Publishing new layer version from s3://%s/%s to %s", bucket, key, layer_arn)
        
        # Create a ZIP file from the LanceDB directory structure
        import tempfile
        import zipfile
        from pathlib import Path
        
        s3_client = boto3.client("s3", region_name=self.region)
        lambda_client = boto3.client("lambda", region_name=self.region)

        try:
            # Download the LanceDB directory structure to a temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                lancedb_dir = temp_path / "lancedb"
                lancedb_dir.mkdir()
                
                # List and download all objects with the prefix
                logger.info("Downloading LanceDB directory structure from S3...")
                paginator = s3_client.get_paginator('list_objects_v2')
                pages = paginator.paginate(Bucket=bucket, Prefix=key)
                
                for page in pages:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            s3_key = obj['Key']
                            # Calculate local file path
                            relative_path = s3_key[len(key):].lstrip('/')
                            if relative_path:  # Skip empty paths
                                local_file_path = lancedb_dir / relative_path
                                
                                # Create parent directories
                                local_file_path.parent.mkdir(parents=True, exist_ok=True)
                                
                                # Download file
                                s3_client.download_file(bucket, s3_key, str(local_file_path))
                                logger.debug(f"Downloaded {s3_key} to {local_file_path}")
                
                # Create ZIP file with correct directory structure for Lambda layer
                zip_path = temp_path / "lancedb.zip"
                logger.info("Creating ZIP file for Lambda layer...")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for file_path in lancedb_dir.rglob("*"):
                        if file_path.is_file():
                            # Calculate path within ZIP - preserve the full directory structure
                            # This ensures files are extracted to packages.lance/ in the Lambda layer
                            relative_path = file_path.relative_to(lancedb_dir)
                            
                            # If the file is at the root level of lancedb directory, 
                            # assume it should be in packages.lance/
                            if len(relative_path.parts) == 1:
                                arc_name = Path("packages.lance") / relative_path
                            else:
                                # If already in a subdirectory structure, preserve it
                                # but ensure it starts with packages.lance if not already
                                if relative_path.parts[0] != "packages.lance":
                                    arc_name = Path("packages.lance") / relative_path
                                else:
                                    arc_name = relative_path
                                    
                            logger.debug(f"Adding {file_path} as {arc_name} to ZIP")
                            zip_file.write(file_path, arc_name)
                
                # Upload ZIP to S3 with timestamp to avoid overlap
                import time
                timestamp = int(time.time())
                zip_key = f"{key.rstrip('/')}-{timestamp}.zip"
                logger.info(f"Uploading ZIP file to s3://{bucket}/{zip_key}")
                s3_client.upload_file(str(zip_path), bucket, zip_key)
                
                # Publish layer using the ZIP file
                resp = lambda_client.publish_layer_version(
                    LayerName=layer_arn,
                    Description="Minified LanceDB database with search indexes for fdnix search API",
                    Content={"S3Bucket": bucket, "S3Key": zip_key},
                    CompatibleRuntimes=["provided.al2023"],
                    CompatibleArchitectures=["x86_64"],
                )
                
                arn = resp.get("LayerVersionArn") or ""
                version = resp.get("Version")
                logger.info("Published layer version: %s (version %s)", arn, version)
                logger.info("ZIP file preserved at s3://%s/%s", bucket, zip_key)
                
                return arn
                        
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to publish layer version: %s", e)
            raise

