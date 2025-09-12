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

    Expects the SQLite database file to be available in S3 and publishes it as a new
    layer version using the unversioned Layer ARN (name) passed in.
    """

    def __init__(self, region: Optional[str] = None) -> None:
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not self.region:
            logger.warning("AWS region not provided; falling back to default client config")

    def publish_from_s3(self, *, bucket: str, key: str, layer_arn: str) -> str:
        """Publish a new layer version and return the LayerVersionArn.

        Args:
            bucket: S3 bucket containing the SQLite database file
            key: S3 key for the SQLite database file
            layer_arn: Unversioned layer ARN or name (e.g., arn:aws:lambda:...:layer:fdnix-database-layer)
        """
        if not boto3:
            raise RuntimeError("boto3 is not available but required for layer publishing")

        if not bucket or not key or not layer_arn:
            raise ValueError("bucket, key, and layer_arn are required to publish a layer")

        logger.info("Publishing new layer version from s3://%s/%s to %s", bucket, key, layer_arn)
        
        # Create a ZIP file containing just the SQLite database
        import tempfile
        import zipfile
        from pathlib import Path
        
        s3_client = boto3.client("s3", region_name=self.region)
        lambda_client = boto3.client("lambda", region_name=self.region)

        try:
            # Download the SQLite database file to a temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Download the SQLite database file
                logger.info("Downloading SQLite database from S3...")
                local_db_path = temp_path / "fdnix.db"
                s3_client.download_file(bucket, key, str(local_db_path))
                logger.debug(f"Downloaded SQLite database to {local_db_path}")
                
                # Create ZIP file with the SQLite database in the correct location
                zip_path = temp_path / "sqlite-layer.zip"
                logger.info("Creating ZIP file for Lambda layer...")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    # Add the SQLite database to the ZIP in the correct location for Lambda
                    # The database should be extracted to /opt/fdnix/fdnix.db in the Lambda layer
                    arc_name = "fdnix.db"
                    zip_file.write(local_db_path, arc_name)
                    logger.debug(f"Added {local_db_path} as {arc_name} to ZIP")
                
                # Upload ZIP to S3 with timestamp to avoid overlap
                import time
                timestamp = int(time.time())
                zip_key = f"{key.rsplit('.', 1)[0]}-{timestamp}.zip"
                logger.info(f"Uploading ZIP file to s3://{bucket}/{zip_key}")
                s3_client.upload_file(str(zip_path), bucket, zip_key)
                
                # Publish layer using the ZIP file
                resp = lambda_client.publish_layer_version(
                    LayerName=layer_arn,
                    Description="Minified SQLite database with FTS search for fdnix search API",
                    Content={"S3Bucket": bucket, "S3Key": zip_key},
                    CompatibleRuntimes=["provided.al2023"],
                    CompatibleArchitectures=["x86_64"],
                )
                
                arn = resp.get("LayerVersionArn") or ""
                version = resp.get("Version")
                logger.info("Published layer version: %s (version %s)", arn, version)
                logger.info("ZIP file preserved at s3://%s/%s", bucket, zip_key)
                
                # Update Lambda functions using this layer
                self._update_lambda_functions_using_layer(lambda_client, layer_arn, arn)
                
                return arn
                        
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to publish layer version: %s", e)
            raise

    def _update_lambda_functions_using_layer(self, lambda_client, layer_arn: str, new_layer_version_arn: str) -> None:
        """Find and update Lambda functions that are using this layer."""
        try:
            logger.info("Searching for Lambda functions using layer: %s", layer_arn)
            
            # Extract layer name from ARN for matching
            layer_name = layer_arn.split(':')[-1] if ':' in layer_arn else layer_arn
            base_layer_arn = ':'.join(layer_arn.split(':')[:-1]) if ':' in layer_arn and layer_arn.count(':') >= 6 else layer_arn
            
            # List all Lambda functions
            paginator = lambda_client.get_paginator('list_functions')
            functions_updated = 0
            
            for page in paginator.paginate():
                for function in page.get('Functions', []):
                    function_name = function['FunctionName']
                    
                    try:
                        # Get function configuration to check layers
                        config = lambda_client.get_function_configuration(FunctionName=function_name)
                        layers = config.get('Layers', [])
                        
                        # Check if this function uses our layer
                        updated_layers = []
                        layer_found = False
                        
                        for layer in layers:
                            layer_version_arn = layer['Arn']
                            
                            # Check if this layer matches our layer (by name/base ARN)
                            if (layer_name in layer_version_arn or 
                                base_layer_arn in layer_version_arn or
                                self._layer_arns_match(layer_version_arn, layer_arn)):
                                
                                logger.info("Found function %s using layer %s", function_name, layer_version_arn)
                                updated_layers.append(new_layer_version_arn)
                                layer_found = True
                            else:
                                # Keep other layers unchanged
                                updated_layers.append(layer_version_arn)
                        
                        # Update the function if it uses our layer
                        if layer_found:
                            logger.info("Updating function %s to use new layer version %s", function_name, new_layer_version_arn)
                            
                            lambda_client.update_function_configuration(
                                FunctionName=function_name,
                                Layers=updated_layers
                            )
                            
                            functions_updated += 1
                            logger.info("Successfully updated function: %s", function_name)
                    
                    except Exception as e:
                        logger.warning("Failed to check/update function %s: %s", function_name, e)
                        continue
            
            if functions_updated > 0:
                logger.info("Successfully updated %d Lambda function(s) to use the new layer version", functions_updated)
            else:
                logger.info("No Lambda functions found using this layer")
                
        except Exception as e:
            logger.error("Failed to update Lambda functions using layer: %s", e)
            # Don't raise - this is a nice-to-have feature, not critical

    def _layer_arns_match(self, layer_version_arn: str, target_layer_arn: str) -> bool:
        """Check if two layer ARNs refer to the same layer (ignoring version)."""
        try:
            # Extract base ARN without version
            if ':' in layer_version_arn:
                parts = layer_version_arn.split(':')
                if len(parts) >= 7:  # arn:aws:lambda:region:account:layer:name:version
                    base_arn = ':'.join(parts[:-1])  # Remove version part
                    target_base = ':'.join(target_layer_arn.split(':')[:-1]) if ':' in target_layer_arn else target_layer_arn
                    return base_arn == target_base or base_arn.endswith(target_base) or target_base.endswith(base_arn)
            
            return False
        except Exception:
            return False

