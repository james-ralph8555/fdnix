#!/usr/bin/env python3

import os
import sys
import logging
import json
from typing import List, Dict, Any, Tuple
import duckdb
import boto3
from bedrock_client import BedrockClient

logger = logging.getLogger(__name__)

class EmbeddingGenerator:
    def __init__(self):
        self.validate_environment()
        self.bedrock_client = BedrockClient(
            model_id=os.environ['BEDROCK_MODEL_ID'],
            region=os.environ['AWS_REGION']
        )
        
        self.batch_size = 50  # Process embeddings in batches
        self.max_text_length = 2000  # Limit text length for embedding
        # DuckDB + artifact settings
        self.duckdb_path = os.environ.get('DUCKDB_PATH', '/out/fdnix.duckdb').strip()
        self.artifacts_bucket = os.environ.get('ARTIFACTS_BUCKET', '').strip()
        self.duckdb_key = os.environ.get('DUCKDB_KEY', '').strip()
        self.embedding_dim: int | None = None
        # VSS index parameters (override via env)
        self.vss_hnsw_m = int(os.environ.get('VSS_HNSW_M', '16'))
        self.vss_ef_construction = int(os.environ.get('VSS_EF_CONSTRUCTION', '200'))
        self.vss_ef_search = int(os.environ.get('VSS_EF_SEARCH', '40'))

    def validate_environment(self):
        """Validate required environment variables"""
        required_vars = [
            'AWS_REGION',
            'BEDROCK_MODEL_ID'
        ]
        
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    # -----------------------
    # DuckDB helpers
    # -----------------------
    def connect_duckdb(self) -> duckdb.DuckDBPyConnection:
        if not os.path.exists(self.duckdb_path):
            raise FileNotFoundError(f"DuckDB file not found at {self.duckdb_path}")
        logger.info(f"Opening DuckDB at {self.duckdb_path}")
        con = duckdb.connect(self.duckdb_path)
        # Ensure VSS extension is available
        try:
            con.execute("INSTALL vss; LOAD vss;")
        except Exception as e:
            error_msg = f"Could not install/load DuckDB vss extension: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        # Enable experimental persistence for HNSW indexes for on-disk DBs
        try:
            con.execute("SET hnsw_enable_experimental_persistence = true;")
        except Exception:
            # Some versions might guard this flag differently; ignore if unsupported
            pass
        # Best-effort: set runtime VSS parameters (ignored if unsupported)
        try:
            con.execute(f"SET vss.ef_search={int(self.vss_ef_search)};")
        except Exception:
            pass
        # Create embeddings table if not present
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                package_id TEXT PRIMARY KEY,
                vector FLOAT[]
            );
            """
        )
        # Simple key/value meta table for index metadata (e.g., embedding_dim)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS fdnix_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        return con

    def fetch_packages_to_embed(self, con: duckdb.DuckDBPyConnection, limit: int | None = None) -> List[Dict[str, Any]]:
        # Select candidates, preferring usable packages
        sql = (
            "SELECT packageName, version, attributePath, description, longDescription, homepage, mainProgram, "
            "license, maintainers, platforms, COALESCE(hasEmbedding, FALSE) AS hasEmbedding, "
            "COALESCE(available, TRUE) AS available, COALESCE(broken, FALSE) AS broken, "
            "COALESCE(insecure, FALSE) AS insecure, COALESCE(unsupported, FALSE) AS unsupported "
            "FROM packages "
            "WHERE COALESCE(hasEmbedding, FALSE) = FALSE "
            "AND COALESCE(available, TRUE) = TRUE "
            "AND COALESCE(broken, FALSE) = FALSE "
            "AND COALESCE(insecure, FALSE) = FALSE "
            "AND COALESCE(unsupported, FALSE) = FALSE"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        try:
            res = con.execute(sql)
            rows = res.fetchall()
        except duckdb.CatalogException as e:
            raise RuntimeError("Expected 'packages' table not found in DuckDB. Run metadata phase first.") from e

        columns = [d[0] for d in res.description]
        out: List[Dict[str, Any]] = []
        for r in rows:
            rec = dict(zip(columns, r))
            # Coerce JSON-like columns into Python types when stored as JSON or VARCHAR
            for key in ("license", "maintainers", "platforms"):
                val = rec.get(key)
                if isinstance(val, str):
                    try:
                        rec[key] = json.loads(val)
                    except Exception:
                        # keep as-is if not valid JSON
                        pass
            out.append(rec)
        return out

    def insert_embeddings(self, con: duckdb.DuckDBPyConnection, rows: List[Tuple[str, list]]) -> None:
        if not rows:
            return
        con.executemany("INSERT OR REPLACE INTO embeddings (package_id, vector) VALUES (?, ?)", rows)

    def mark_packages_embedded(self, con: duckdb.DuckDBPyConnection, keys: List[Tuple[str, str]]) -> None:
        if not keys:
            return
        con.executemany(
            "UPDATE packages SET hasEmbedding = TRUE WHERE packageName = ? AND version = ?",
            keys,
        )

    def ensure_vss_index(self, con: duckdb.DuckDBPyConnection) -> None:
        # Determine dimension from existing rows if not already known
        dim = self.embedding_dim
        if dim is None:
            try:
                dim_row = con.execute("SELECT list_length(vector) FROM embeddings WHERE vector IS NOT NULL LIMIT 1").fetchone()
                if dim_row and dim_row[0]:
                    dim = int(dim_row[0])
            except Exception:
                dim = None
        if not dim:
            logger.warning("Skipping VSS index creation: no embeddings present to infer dimension")
            return
        # Compare with last known dim; if different, rebuild index
        try:
            prev_dim_row = con.execute("SELECT value FROM fdnix_meta WHERE key='embedding_dim'").fetchone()
            prev_dim = int(prev_dim_row[0]) if prev_dim_row and prev_dim_row[0] is not None else None
        except Exception:
            prev_dim = None

        # Try creating/updating index with HNSW params
        logger.info(f"Ensuring VSS index (dim={dim}, M={self.vss_hnsw_m}, ef_construction={self.vss_ef_construction})")
        try:
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS embeddings_vss_idx
                ON embeddings(vector)
                USING vss (dim={}, hnsw_m={}, hnsw_ef_construction={});
                """.format(dim, self.vss_hnsw_m, self.vss_ef_construction)
            )
        except Exception as e:
            logger.warning(f"VSS index ensure failed: {e}. Attempting drop/recreate...")
            try:
                con.execute("DROP INDEX IF EXISTS embeddings_vss_idx;")
                con.execute(
                    """
                    CREATE INDEX embeddings_vss_idx
                    ON embeddings(vector)
                    USING vss (dim={}, hnsw_m={}, hnsw_ef_construction={});
                    """.format(dim, self.vss_hnsw_m, self.vss_ef_construction)
                )
            except Exception as inner:
                error_msg = f"Failed to rebuild VSS index: {inner}"
                logger.error(error_msg)
                raise RuntimeError(error_msg) from inner

        # Persist current dim into meta table
        try:
            con.execute("INSERT OR REPLACE INTO fdnix_meta (key, value) VALUES ('embedding_dim', ?)", [str(dim)])
        except Exception:
            pass

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

    async def process_packages_batch(self, packages: List[Dict[str, Any]]) -> int:
        """Process a batch of packages to generate embeddings"""
        logger.info(f"Processing batch of {len(packages)} packages...")
        
        # Create embedding texts
        texts = []
        package_keys: List[Tuple[str, str]] = []
        
        for package in packages:
            text = self.create_embedding_text(package)
            texts.append(text)
            package_keys.append((package['packageName'], package['version']))
        
        try:
            # Generate embeddings using Bedrock
            logger.info(f"Generating embeddings for {len(texts)} texts...")
            embeddings = await self.bedrock_client.generate_embeddings_batch(texts)
            
            if len(embeddings) != len(texts):
                logger.error(f"Mismatch in embeddings count: expected {len(texts)}, got {len(embeddings)}")
                return 0
            
            # Remember embedding dimension for VSS index creation later
            if embeddings and not self.embedding_dim:
                self.embedding_dim = len(embeddings[0]) if embeddings[0] else None

            # Prepare rows for insertion
            vector_rows: List[Tuple[str, list]] = []
            for (pkg_name, ver), vec in zip(package_keys, embeddings):
                pkg_id = f"{pkg_name}#{ver}"
                vector_rows.append((pkg_id, vec))

            # Insert into DuckDB and mark packages
            con = self._duckdb_con
            con.execute("BEGIN TRANSACTION;")
            try:
                self.insert_embeddings(con, vector_rows)
                self.mark_packages_embedded(con, package_keys)
                con.execute("COMMIT;")
            except Exception:
                con.execute("ROLLBACK;")
                raise
            
            logger.info(f"Successfully processed batch of {len(packages)} packages")
            return len(packages)
            
        except Exception as error:
            logger.error(f"Error processing batch: {str(error)}")
            return 0

    async def run(self):
        """Main execution function"""
        logger.info("Starting fdnix embedding generation process...")
        
        try:
            # Connect to DuckDB
            self._duckdb_con = self.connect_duckdb()
            # Load candidates
            candidates = self.fetch_packages_to_embed(self._duckdb_con)
            total_packages = len(candidates)
            if total_packages == 0:
                logger.info("No packages need embeddings (hasEmbedding=true for all). Nothing to do.")
                # Still consider uploading if requested
                self._maybe_upload_artifact()
                return
            
            # Process packages in batches
            processed_count = 0
            failed_count = 0
            
            for i in range(0, total_packages, self.batch_size):
                batch = candidates[i:i + self.batch_size]
                logger.info(f"Processing batch {i//self.batch_size + 1}/{(total_packages + self.batch_size - 1)//self.batch_size}")
                
                batch_processed = await self.process_packages_batch(batch)
                processed_count += batch_processed
                failed_count += len(batch) - batch_processed
                
                logger.info(f"Progress: {processed_count}/{total_packages} processed, {failed_count} failed")
            # Build or update VSS index in DuckDB
            self.ensure_vss_index(self._duckdb_con)

            # Upload final artifact to S3 if configured
            self._maybe_upload_artifact()

            logger.info(f"Embedding generation completed! Processed: {processed_count}, Failed: {failed_count}")
            
        except Exception as error:
            logger.error(f"Fatal error during embedding generation: {str(error)}")
            raise error

    def _maybe_upload_artifact(self) -> None:
        if not self.artifacts_bucket or not self.duckdb_key:
            logger.info("Artifact upload not configured (ARTIFACTS_BUCKET/DUCKDB_KEY missing). Skipping upload.")
            return
        s3 = boto3.client('s3', region_name=os.environ['AWS_REGION'])
        logger.info(f"Uploading {self.duckdb_path} to s3://{self.artifacts_bucket}/{self.duckdb_key}")
        s3.upload_file(self.duckdb_path, self.artifacts_bucket, self.duckdb_key)
