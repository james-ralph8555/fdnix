#!/usr/bin/env python3

import os
import sys
import logging
import json
import asyncio
from typing import List, Dict, Any, Tuple
import lancedb
import pandas as pd
from botocore.exceptions import ClientError
from bedrock_client import BedrockBatchClient

logger = logging.getLogger(__name__)

class EmbeddingGenerator:
    def __init__(self):
        self.validate_environment()
        self.bedrock_client = BedrockBatchClient(
            region=os.environ.get('AWS_REGION'),
            model_id=os.environ.get('BEDROCK_MODEL_ID')
        )
        
        # Use Bedrock batch size for processing (up to 50,000)
        self.batch_size = int(os.environ.get('BEDROCK_BATCH_SIZE', '50000'))
        self.max_text_length = 8192  # Titan Text v2 supports up to 8,192 tokens
        # LanceDB + artifact settings (operate on the main database)
        self.lancedb_path = os.environ.get('LANCEDB_PATH', '/out/fdnix-data.lancedb').strip()
        self.artifacts_bucket = os.environ.get('ARTIFACTS_BUCKET', '').strip()
        # Embedding phase reads/writes the MAIN DB artifact in S3
        self.lancedb_key = os.environ.get('LANCEDB_DATA_KEY', '').strip()
        self.embedding_dim: int | None = None
        # Vector index parameters (override via env)
        self.vector_index_partitions = int(os.environ.get('VECTOR_INDEX_PARTITIONS', '256'))
        self.vector_index_sub_vectors = int(os.environ.get('VECTOR_INDEX_SUB_VECTORS', '8'))

    def validate_environment(self):
        """Validate required environment variables"""
        required_vars = [
            'BEDROCK_ROLE_ARN'
        ]
        
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    # -----------------------
    # LanceDB helpers
    # -----------------------
    def connect_lancedb(self) -> lancedb.DBConnection:
        if not os.path.exists(self.lancedb_path):
            raise FileNotFoundError(f"LanceDB not found at {self.lancedb_path}")
        logger.info(f"Opening LanceDB at {self.lancedb_path}")
        db = lancedb.connect(self.lancedb_path)
        return db

    def fetch_packages_to_embed(self, db: lancedb.DBConnection, limit: int | None = None, force_rebuild: bool = False) -> List[Dict[str, Any]]:
        try:
            table = db.open_table("packages")
        except (FileNotFoundError, ValueError) as e:
            raise RuntimeError("Expected 'packages' table not found in LanceDB. Run metadata phase first.") from e

        # Get all packages data
        if force_rebuild:
            # Force rebuild: process all packages regardless of has_embedding status
            # Use head with count_rows to get all data (workaround for to_pandas limit)
            row_count = table.count_rows()
            if limit:
                row_count = min(row_count, limit)
            df = table.head(row_count).to_pandas()
        else:
            # Incremental: process packages that need embeddings
            # Use vector search with where clause (need a dummy vector for search API)
            dummy_vector = [0.0] * 256  # Match the vector dimension
            query = table.search(dummy_vector).where("has_embedding = false")
            if limit:
                query = query.limit(limit)
            df = query.to_pandas()
        
        # Convert to list of dictionaries
        out: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            rec = row.to_dict()
            # Coerce JSON-like columns into Python types
            for key in ("license", "maintainers", "platforms"):
                val = rec.get(key)
                if isinstance(val, str) and val:
                    try:
                        rec[key] = json.loads(val)
                    except Exception:
                        # keep as-is if not valid JSON
                        pass
            out.append(rec)
        return out

    def insert_embeddings(self, table: lancedb.table.Table, rows: List[Tuple[str, list]]) -> None:
        if not rows:
            return
        # Convert to records for LanceDB
        records = []
        for package_id, vector in rows:
            records.append({
                "package_id": package_id,
                "vector": vector
            })
        # Update existing records with embeddings
        for record in records:
            table.update(where=f"package_id = '{record['package_id']}'", values={"vector": record["vector"], "has_embedding": True})

    def mark_packages_embedded(self, table: lancedb.table.Table, keys: List[str]) -> None:
        if not keys:
            return
        for key in keys:
            table.update(where=f"package_id = '{key}'", values={"has_embedding": True})

    def ensure_vector_index(self, table: lancedb.table.Table) -> None:
        # Create vector index using LanceDB's native indexing
        try:
            logger.info(f"Creating vector index (partitions={self.vector_index_partitions}, sub_vectors={self.vector_index_sub_vectors})")
            
            # Create IVF-PQ index on vector column
            table.create_index(
                column="vector",
                index_type="IVF_PQ",
                num_partitions=self.vector_index_partitions,
                num_sub_vectors=self.vector_index_sub_vectors,
                distance_type="cosine"
            )
            
            logger.info("Vector index created successfully")
        except Exception as e:
            logger.error("Failed to create vector index: %s", e)
            # Don't raise - vector index is optional

    def create_embedding_text(self, package: Dict[str, Any]) -> str:
        """Create a text representation of the package for embedding"""
        parts = []
        
        # Package name and version
        parts.append(f"Package: {package.get('packageName', '')}")
        if package.get('version'):
            parts.append(f"Version: {package['version']}")
        
        # Main program for better searchability
        if package.get('mainProgram'):
            parts.append(f"Main Program: {package['mainProgram']}")
        
        # Description - concatenate both description and longDescription for richer content
        description = package.get('description', '').strip()
        long_description = package.get('longDescription', '').strip()
        
        description_parts = []
        if description:
            description_parts.append(description)
        if long_description and long_description != description:
            description_parts.append(long_description)
        
        if description_parts:
            description_text = '. '.join(description_parts)
            parts.append(f"Description: {description_text}")
        
        # Homepage URL
        if package.get('homepage'):
            parts.append(f"Homepage: {package['homepage']}")
        
        # License information - handle enhanced license structure
        license_info = self.format_license_for_embedding(package.get('license'))
        if license_info:
            parts.append(f"License: {license_info}")
        
        # Maintainers - handle enhanced maintainer structure
        maintainer_info = self.format_maintainers_for_embedding(package.get('maintainers', []))
        if maintainer_info:
            parts.append(f"Maintainers: {maintainer_info}")
        
        # Platforms
        if package.get('platforms') and isinstance(package['platforms'], list):
            platforms = [str(p) for p in package['platforms'][:5]]  # Limit to first 5
            if platforms:
                parts.append(f"Platforms: {', '.join(platforms)}")
        
        # Attribute path for technical context
        if package.get('attributePath'):
            parts.append(f"Attribute: {package['attributePath']}")
        
        text = '. '.join(parts)
        
        # Truncate if too long
        if len(text) > self.max_text_length:
            text = text[:self.max_text_length - 3] + '...'
        
        return text

    def format_license_for_embedding(self, license_data) -> str:
        """Format license data for embedding text"""
        if not license_data:
            return ""
        
        if isinstance(license_data, str):
            return license_data
        
        if isinstance(license_data, dict):
            if license_data.get('type') == 'string':
                return license_data.get('value', '')
            elif license_data.get('type') == 'object':
                # Prefer spdxId, then shortName, then fullName
                return license_data.get('spdxId') or license_data.get('shortName') or license_data.get('fullName', '')
            elif license_data.get('type') == 'array':
                licenses = license_data.get('licenses', [])
                license_names = []
                for lic in licenses[:3]:  # Limit to first 3 licenses
                    name = lic.get('spdxId') or lic.get('shortName') or lic.get('fullName', '')
                    if name:
                        license_names.append(name)
                return ', '.join(license_names)
        
        return str(license_data)[:100]  # Fallback with length limit

    def format_maintainers_for_embedding(self, maintainers) -> str:
        """Format maintainer data for embedding text"""
        if not maintainers or not isinstance(maintainers, list):
            return ""
        
        maintainer_names = []
        for maintainer in maintainers[:3]:  # Limit to first 3 maintainers
            if isinstance(maintainer, dict):
                name = maintainer.get('name', '') or maintainer.get('email', '') or maintainer.get('github', '')
                if name:
                    maintainer_names.append(name)
            elif isinstance(maintainer, str):
                maintainer_names.append(maintainer)
        
        return ', '.join(maintainer_names)

    async def process_packages_batch(self, packages: List[Dict[str, Any]]) -> Tuple[int, int]:
        """Process a batch of packages to generate embeddings.
        
        Returns: (processed_count, reused_count) 
        """
        logger.info(f"Processing Bedrock batch of {len(packages)} packages...")
        
        # Separate packages that can reuse embeddings vs need new ones
        packages_need_embedding = []
        packages_can_reuse = []
        package_keys: List[Tuple[str, str]] = []
        
        for package in packages:
            package_keys.append((package['packageName'], package['version']))
            content_hash = package.get('content_hash')
            
            if content_hash:
                # Check if we have an existing embedding for this content hash
                try:
                    # For LanceDB, we'll check if package already has embedding based on content hash
                    # This is simplified since embeddings are stored directly in the packages table
                    # Use vector search with where clause to find existing embedding
                    dummy_vector = [0.0] * 256
                    existing_df = self._lancedb_table.search(dummy_vector).where(f"content_hash = {content_hash} AND has_embedding = true").limit(1).to_pandas()
                    
                    if not existing_df.empty and 'vector' in existing_df.columns:
                        existing_vector = existing_df.iloc[0]['vector']
                        if existing_vector is not None and len(existing_vector) > 0:
                            packages_can_reuse.append((package, existing_vector))
                            continue
                except Exception:
                    pass  # Fall through to generate new embedding
            
            packages_need_embedding.append(package)
        
        reused_count = len(packages_can_reuse)
        processed_count = 0
        
        # Process packages that can reuse embeddings
        if packages_can_reuse:
            logger.info(f"Reusing embeddings for {reused_count} packages with unchanged content...")
            vector_rows: List[Tuple[str, list]] = []
            reuse_keys: List[str] = []
            
            for package, vector in packages_can_reuse:
                pkg_id = package['package_id']
                vector_rows.append((pkg_id, vector))
                reuse_keys.append(pkg_id)
            
            table = self._lancedb_table
            try:
                self.insert_embeddings(table, vector_rows)
                self.mark_packages_embedded(table, reuse_keys)
                processed_count += reused_count
            except Exception:
                raise
        
        # Process packages that need new embeddings
        if packages_need_embedding:
            texts = []
            new_package_ids: List[str] = []
            content_hashes = []
            
            for package in packages_need_embedding:
                text = self.create_embedding_text(package)
                texts.append(text)
                new_package_ids.append(package['package_id'])
                content_hashes.append(package.get('content_hash'))
            
            try:
                # Generate embeddings using Bedrock batch inference
                logger.info(f"Generating embeddings for {len(texts)} new/changed texts...")
                texts_with_ids = [(pkg_id, text) for pkg_id, text in zip(new_package_ids, texts)]
                embeddings_with_ids = await self.bedrock_client.generate_embeddings_batch(texts_with_ids)
                
                # Convert to list indexed by original order
                embeddings_dict = {record_id: embedding for record_id, embedding in embeddings_with_ids}
                embeddings = [embeddings_dict.get(pkg_id, []) for pkg_id in new_package_ids]
                
                if len(embeddings) != len(texts):
                    logger.error(f"Mismatch in embeddings count: expected {len(texts)}, got {len(embeddings)}")
                    return processed_count, reused_count
                
                # Remember embedding dimension for VSS index creation later
                if embeddings and not self.embedding_dim:
                    self.embedding_dim = len(embeddings[0]) if embeddings[0] else None

                # Prepare rows for insertion
                vector_rows: List[Tuple[str, list]] = []
                hash_rows: List[Tuple[str, list]] = []
                
                for i, (pkg_id, vec, content_hash) in enumerate(zip(new_package_ids, embeddings, content_hashes)):
                    vector_rows.append((pkg_id, vec))
                    if content_hash:
                        hash_rows.append((content_hash, vec))

                # Insert into LanceDB and mark packages
                table = self._lancedb_table
                try:
                    self.insert_embeddings(table, vector_rows)
                    self.mark_packages_embedded(table, new_package_ids)
                    # Note: For LanceDB, content hash -> embedding mapping is handled directly in the packages table
                    processed_count += len(packages_need_embedding)
                except Exception:
                    raise
                
            except Exception as error:
                logger.error(f"Error processing batch: {str(error)}")
                return processed_count, reused_count
        
        logger.info(f"Successfully processed Bedrock batch: {len(packages_need_embedding)} new, {reused_count} reused")
        return processed_count, reused_count

    async def run(self, force_rebuild: bool = False):
        """Main execution function"""
        logger.info("Starting fdnix embedding generation process...")
        
        try:
            # Test Bedrock access first
            logger.info("Validating Bedrock model access...")
            if not self.bedrock_client.validate_model_access():
                raise RuntimeError("Cannot access Bedrock model. Check IAM permissions and model availability.")
            logger.info("Bedrock model access validated successfully")
            
            # Connect to LanceDB, downloading current artifact from S3 if missing
            downloaded_current = False
            try:
                self._lancedb_db = self.connect_lancedb()
                self._lancedb_table = self._lancedb_db.open_table("packages")
            except FileNotFoundError:
                if self.artifacts_bucket and self.lancedb_key:
                    logger.info(
                        f"Local main DB not found at {self.lancedb_path}. Attempting download from s3://{self.artifacts_bucket}/{self.lancedb_key}"
                    )
                    try:
                        import boto3
                        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
                        # Download entire LanceDB directory structure
                        self._download_lancedb_from_s3(s3, self.artifacts_bucket, self.lancedb_key, self.lancedb_path)
                        downloaded_current = True
                        self._lancedb_db = self.connect_lancedb()
                        self._lancedb_table = self._lancedb_db.open_table("packages")
                        logger.info("Downloaded and opened main DB from S3")
                    except Exception as e:
                        logger.error(f"Failed to download main DB from S3: {e}")
                        raise
                else:
                    raise
            
            # Try to load previous artifact for incremental updates (unless force rebuild)
            if not force_rebuild and not downloaded_current:
                prev_artifact_path = self.lancedb_path + ".previous"
                if self._maybe_download_previous_artifact():
                    self._load_previous_embeddings_and_hashes(prev_artifact_path)
                    # Clean up temporary file
                    try:
                        import os
                        os.remove(prev_artifact_path)
                    except Exception:
                        pass
            
            # Load candidates (this now uses content hash comparison for incremental updates)
            candidates = self.fetch_packages_to_embed(self._lancedb_db, force_rebuild=force_rebuild)
            total_packages = len(candidates)
            
            if total_packages == 0:
                logger.info("No packages need embeddings (all up to date). Nothing to do.")
                # Still consider uploading if requested
                self._maybe_upload_artifact()
                return
            
            # Process packages in batches
            total_processed = 0
            total_reused = 0
            failed_count = 0
            
            for i in range(0, total_packages, self.batch_size):
                batch = candidates[i:i + self.batch_size]
                batch_num = i//self.batch_size + 1
                total_batches = (total_packages + self.batch_size - 1)//self.batch_size
                logger.info(f"Processing Bedrock batch {batch_num}/{total_batches} ({len(batch)} packages)")
                
                try:
                    batch_processed, batch_reused = await self.process_packages_batch(batch)
                    total_processed += batch_processed
                    total_reused += batch_reused
                    
                    logger.info(f"Bedrock batch {batch_num} complete: {total_processed}/{total_packages} total, "
                               f"{batch_processed - batch_reused} new embeddings, "
                               f"{total_reused} reused from cache")
                except Exception as e:
                    logger.error(f"Failed to process batch {batch_num}: {e}")
                    failed_count += len(batch)
            
            # Build or update vector index in LanceDB
            self.ensure_vector_index(self._lancedb_table)

            # Upload final artifact to S3 if configured
            self._maybe_upload_artifact()

            new_embeddings = total_processed - total_reused
            logger.info(f"Embedding generation completed! "
                       f"Total: {total_processed}, New: {new_embeddings}, "
                       f"Reused: {total_reused}, Failed: {failed_count}")
            
        except Exception as error:
            logger.error(f"Fatal error during embedding generation: {str(error)}")
            raise error

    def _maybe_download_previous_artifact(self) -> bool:
        """Download previous LanceDB artifact from S3 if available for incremental updates.
        
        Returns True if previous artifact was downloaded, False otherwise.
        """
        if not self.artifacts_bucket or not self.lancedb_key:
            logger.info("Artifact download not configured (ARTIFACTS_BUCKET/LANCEDB_DATA_KEY missing). Skipping download.")
            return False
            
        # Use a temporary path for the previous artifact
        prev_artifact_path = self.lancedb_path + ".previous"
        
        try:
            import boto3
            s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
            logger.info(f"Attempting to download previous main DB artifact from s3://{self.artifacts_bucket}/{self.lancedb_key}")
            
            # For LanceDB, we need to download the entire directory structure
            # Check if the key exists (this might be a prefix)
            try:
                response = s3.list_objects_v2(Bucket=self.artifacts_bucket, Prefix=self.lancedb_key, MaxKeys=1)
                if 'Contents' not in response:
                    logger.info(f"No previous main DB artifact exists at s3://{self.artifacts_bucket}/{self.lancedb_key}. Starting fresh build.")
                    return False
            except Exception as e:
                logger.info(f"Could not check for previous main DB artifact: {e}. Starting fresh build.")
                return False
            
            # Download the LanceDB directory structure
            self._download_lancedb_from_s3(s3, self.artifacts_bucket, self.lancedb_key, prev_artifact_path)
            logger.info(f"Successfully downloaded previous main DB artifact to {prev_artifact_path}")
            return True
        except Exception as e:
            logger.info(f"Failed to download previous main DB artifact: {e}. Starting fresh build.")
            return False

    def _load_previous_embeddings_and_hashes(self, prev_artifact_path: str) -> None:
        """Load embeddings and content hashes from previous artifact into current DB."""
        try:
            logger.info("Loading embeddings and content hashes from previous main DB artifact...")
            prev_db = lancedb.connect(prev_artifact_path)
            prev_table = prev_db.open_table("packages")
            
            # Load existing embeddings from previous LanceDB
            try:
                # Use vector search to find packages with embeddings
                dummy_vector = [0.0] * 256
                prev_df = prev_table.search(dummy_vector).where("has_embedding = true AND vector IS NOT NULL").to_pandas()
                
                if not prev_df.empty:
                    logger.info(f"Found {len(prev_df)} packages with embeddings in previous artifact")
                    
                    # For each package with existing embedding, update current DB
                    for _, row in prev_df.iterrows():
                        package_id = row['package_id']
                        vector = row['vector']
                        content_hash = row.get('content_hash')
                        
                        if vector is not None and len(vector) > 0:
                            # Update current table with existing embedding
                            try:
                                self._lancedb_table.update(
                                    where=f"package_id = '{package_id}'",
                                    values={"vector": vector, "has_embedding": True}
                                )
                            except Exception as e:
                                logger.debug(f"Could not update package {package_id}: {e}")
                    
                    logger.info("Successfully loaded embeddings from previous artifact")
                else:
                    logger.info("No existing embeddings found in previous main DB artifact")
                    
            except Exception as e:
                logger.warning(f"Could not load embeddings from previous main DB artifact: {e}")
                
        except Exception as e:
            logger.warning(f"Failed to load previous embeddings from main DB artifact: {e}")

    def _maybe_upload_artifact(self) -> None:
        if not self.artifacts_bucket or not self.lancedb_key:
            logger.info("Artifact upload not configured (ARTIFACTS_BUCKET/LANCEDB_DATA_KEY missing). Skipping upload.")
            return
        import boto3
        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        logger.info(f"Uploading main DB {self.lancedb_path} to s3://{self.artifacts_bucket}/{self.lancedb_key}")
        
        # Upload the entire LanceDB directory structure
        self._upload_lancedb_to_s3(s3, self.artifacts_bucket, self.lancedb_key, self.lancedb_path)
        logger.info("Upload complete.")
    
    def _download_lancedb_from_s3(self, s3, bucket: str, key_prefix: str, local_path: str) -> None:
        """Download LanceDB directory structure from S3."""
        import os
        from pathlib import Path
        
        # List all objects with the prefix
        paginator = s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket, Prefix=key_prefix)
        
        # Create local directory
        Path(local_path).mkdir(parents=True, exist_ok=True)
        
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    s3_key = obj['Key']
                    # Calculate local file path
                    relative_path = s3_key[len(key_prefix):].lstrip('/')
                    if relative_path:  # Skip empty paths
                        local_file_path = os.path.join(local_path, relative_path)
                        
                        # Create parent directories
                        Path(local_file_path).parent.mkdir(parents=True, exist_ok=True)
                        
                        # Download file
                        s3.download_file(bucket, s3_key, local_file_path)
                        logger.debug(f"Downloaded {s3_key} to {local_file_path}")

    def _upload_lancedb_to_s3(self, s3, bucket: str, key_prefix: str, local_path: str) -> None:
        """Upload LanceDB directory structure to S3."""
        from pathlib import Path
        
        local_path_obj = Path(local_path)
        
        for file_path in local_path_obj.rglob("*"):
            if file_path.is_file():
                # Calculate relative path for S3 key
                relative_path = file_path.relative_to(local_path_obj)
                s3_key = f"{key_prefix.rstrip('/')}/{relative_path}".replace("\\", "/")
                
                # Upload file
                s3.upload_file(str(file_path), bucket, s3_key)
                logger.debug(f"Uploaded {file_path} to {s3_key}")
