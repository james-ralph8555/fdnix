import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb

try:
    import boto3  # type: ignore
except Exception:  # pragma: no cover - boto3 may be absent in local-only runs
    boto3 = None  # type: ignore


logger = logging.getLogger("fdnix.minified-duckdb-writer")


class MinifiedDuckDBWriter:
    """Creates a minified DuckDB file optimized for Lambda layer deployment.
    
    This writer creates a stripped-down database containing only the essential
    data needed for search operations, excluding metadata columns and tables
    used only for data processing and debugging.
    """

    def __init__(
        self,
        output_path: str,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        self.output_path = Path(output_path)
        self.s3_bucket = s3_bucket
        self.s3_key = s3_key
        self.region = region

    def create_minified_db_from_main(self, main_db_path: str) -> None:
        """Create minified database by copying essential data from main database."""
        self._ensure_parent_dir()
        logger.info("Creating minified DuckDB at %s from main DB at %s", self.output_path, main_db_path)

        with duckdb.connect(str(self.output_path)) as target_conn:
            # Attach the main database as a source
            target_conn.execute(f"ATTACH '{main_db_path}' AS main_db (READ_ONLY);")
            
            # Create the minified schema
            self._init_minified_schema(target_conn)
            
            # Copy essential package data
            self._copy_essential_packages(target_conn)
            
            # Copy FTS source data
            self._copy_fts_data(target_conn)
            
            # Copy embeddings and related tables (if they exist)
            self._copy_embeddings_data(target_conn)
            
            # Build FTS index on minified data
            self._build_fts(target_conn)
            
            target_conn.execute("CHECKPOINT")

        logger.info("Minified DuckDB artifact written: %s", self.output_path)

        if self.s3_bucket and self.s3_key:
            self._upload_to_s3()

    def _ensure_parent_dir(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_minified_schema(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Create the minified schema with only essential columns."""
        # Essential packages table - only columns needed for search and display
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS packages (
              package_id TEXT PRIMARY KEY,
              packageName TEXT,
              version TEXT,
              attributePath TEXT,
              description TEXT,
              homepage TEXT,
              license TEXT,           -- Simplified license string
              maintainers TEXT,       -- Simplified maintainer string  
              broken BOOLEAN,
              unfree BOOLEAN,
              available BOOLEAN,
              insecure BOOLEAN,
              unsupported BOOLEAN,
              mainProgram TEXT
            );
            """
        )

        # FTS source table (same structure as main)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS packages_fts_source (
              package_id TEXT,
              text TEXT
            );
            """
        )

        # Embeddings table (copied from main if exists)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                package_id TEXT PRIMARY KEY,
                vector FLOAT[]
            );
            """
        )

        # Meta table for storing index metadata
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fdnix_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )

    def _copy_essential_packages(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Copy package data with only essential columns and simplified license/maintainer info."""
        logger.info("Copying essential package data...")
        
        conn.execute(
            """
            INSERT INTO packages (
                package_id, packageName, version, attributePath, description, homepage,
                license, maintainers, broken, unfree, available, insecure, unsupported, mainProgram
            )
            SELECT 
                package_id,
                packageName,
                version,
                attributePath,
                description,
                homepage,
                CASE 
                    WHEN license IS NULL THEN NULL
                    ELSE COALESCE(
                        json_extract_string(license, '$.spdxId'),
                        json_extract_string(license, '$.shortName'),
                        json_extract_string(license, '$.fullName'),
                        json_extract_string(license, '$.value'),
                        CASE 
                            WHEN json_extract_string(license, '$.type') = 'array' THEN
                                array_to_string(
                                    list_transform(
                                        json_extract(license, '$.licenses'),
                                        x -> COALESCE(
                                            json_extract_string(x, '$.spdxId'),
                                            json_extract_string(x, '$.shortName'),
                                            json_extract_string(x, '$.fullName')
                                        )
                                    ),
                                    ', '
                                )
                            ELSE substr(license, 1, 100)
                        END
                    )
                END as license,
                CASE 
                    WHEN maintainers IS NULL THEN NULL
                    ELSE array_to_string(
                        list_transform(
                            json_extract(maintainers, '$'),
                            x -> COALESCE(
                                json_extract_string(x, '$.name'),
                                json_extract_string(x, '$.email'),
                                json_extract_string(x, '$.github'),
                                CAST(x AS VARCHAR)
                            )
                        ),
                        ', '
                    )
                END as maintainers,
                COALESCE(broken, FALSE) as broken,
                COALESCE(unfree, FALSE) as unfree,
                COALESCE(available, TRUE) as available,
                COALESCE(insecure, FALSE) as insecure,
                COALESCE(unsupported, FALSE) as unsupported,
                mainProgram
            FROM main_db.packages;
            """
        )
        
        # Get count for logging
        result = conn.execute("SELECT COUNT(*) FROM packages").fetchone()
        count = result[0] if result else 0
        logger.info("Copied %d packages to minified database", count)

    def _copy_fts_data(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Copy FTS source data from main database."""
        logger.info("Copying FTS source data...")
        
        try:
            conn.execute(
                """
                INSERT INTO packages_fts_source (package_id, text)
                SELECT package_id, text FROM main_db.packages_fts_source;
                """
            )
            
            result = conn.execute("SELECT COUNT(*) FROM packages_fts_source").fetchone()
            count = result[0] if result else 0
            logger.info("Copied %d FTS source entries", count)
        except Exception as e:
            logger.warning("Failed to copy FTS data: %s", e)

    def _copy_embeddings_data(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Copy embeddings and metadata from main database if they exist."""
        try:
            # Copy embeddings
            conn.execute(
                """
                INSERT INTO embeddings (package_id, vector)
                SELECT package_id, vector FROM main_db.embeddings
                WHERE vector IS NOT NULL;
                """
            )
            
            result = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
            count = result[0] if result else 0
            logger.info("Copied %d embeddings to minified database", count)
        except Exception as e:
            logger.info("No embeddings found in main database: %s", e)

        try:
            # Copy metadata
            conn.execute(
                """
                INSERT INTO fdnix_meta (key, value)
                SELECT key, value FROM main_db.fdnix_meta;
                """
            )
            logger.info("Copied metadata to minified database")
        except Exception as e:
            logger.info("No metadata found in main database: %s", e)

    def _build_fts(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Create FTS index on the minified database."""
        stopwords = os.environ.get("FTS_STOPWORDS", "english").strip() or "english"
        stemmer = os.environ.get("FTS_STEMMER", "english").strip()

        try:
            conn.execute("INSTALL fts;")
            conn.execute("LOAD fts;")
        except Exception as e:
            error_msg = f"Could not install/load DuckDB fts extension: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        try:
            if stemmer:
                pragma = (
                    f"PRAGMA create_fts_index('packages_fts_source', 'package_id', 'text', "
                    f"stopwords='{stopwords}', stemmer='{stemmer}', strip_accents=true);"
                )
            else:
                pragma = (
                    f"PRAGMA create_fts_index('packages_fts_source', 'package_id', 'text', "
                    f"stopwords='{stopwords}', strip_accents=true);"
                )
            conn.execute(pragma)
            logger.info("FTS index created on minified database (stopwords=%s, stemmer=%s)", 
                       stopwords, stemmer or "<none>")
        except Exception as e:
            error_msg = f"Failed to create FTS index on minified database: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _upload_to_s3(self) -> None:
        if not (boto3 and self.region and self.s3_bucket and self.s3_key):
            logger.info("S3 upload not configured; skipping.")
            return
        logger.info(
            "Uploading minified artifact to s3://%s/%s (region=%s)",
            self.s3_bucket,
            self.s3_key,
            self.region,
        )
        s3 = boto3.client("s3", region_name=self.region)
        s3.upload_file(str(self.output_path), self.s3_bucket, self.s3_key)
        logger.info("Minified database upload complete.")