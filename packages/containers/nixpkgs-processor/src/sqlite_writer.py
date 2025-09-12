#!/usr/bin/env python3

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import sqlite3
import pandas as pd
from pydantic import BaseModel

try:
    import boto3  # type: ignore
except Exception:  # pragma: no cover - boto3 may be absent in local-only runs
    boto3 = None  # type: ignore


logger = logging.getLogger("fdnix.sqlite-writer")


class SQLiteWriter:
    def __init__(
        self,
        output_path: str,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        region: Optional[str] = None,
        clear_before_upload: bool = True,
    ) -> None:
        self.output_path = Path(output_path)
        self.s3_bucket = s3_bucket
        self.s3_key = s3_key
        self.region = region
        self.clear_before_upload = clear_before_upload
        self._db_connection = None

    def write_artifact(self, packages: List[Dict[str, Any]]) -> None:
        self._ensure_parent_dir()
        logger.info("Creating SQLite database at %s", self.output_path)

        # Connect to SQLite database
        self._db_connection = sqlite3.connect(str(self.output_path))
        cursor = self._db_connection.cursor()
        
        # Create tables if they don't exist
        self._create_tables(cursor)
        
        # Convert packages to SQLite format and insert
        sqlite_packages = self._convert_packages_to_sqlite_format(packages)
        
        # Insert packages data
        if sqlite_packages:
            cursor.executemany(
                """
                INSERT OR REPLACE INTO packages (
                    package_id, package_name, version, attribute_path, description, 
                    long_description, search_text, homepage, license, platforms, 
                    maintainers, category, broken, unfree, available, insecure, 
                    unsupported, main_program, position, outputs_to_install, 
                    last_updated, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                sqlite_packages
            )
            logger.info("Inserted %d packages into SQLite database", len(sqlite_packages))
        else:
            logger.info("No packages to insert into SQLite database")

        # Create FTS virtual table
        self._create_fts_table(cursor)
        
        # Create indexes for performance
        self._create_indexes(cursor)
        
        # Commit changes
        self._db_connection.commit()

        logger.info("SQLite artifact written: %s", self.output_path)

        if self.s3_bucket and self.s3_key:
            self._upload_to_s3()
        
        # Close connection
        if self._db_connection:
            self._db_connection.close()
            self._db_connection = None

    def _ensure_parent_dir(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _create_tables(self, cursor: sqlite3.Cursor) -> None:
        """Create the main packages table"""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS packages (
                package_id TEXT PRIMARY KEY,
                package_name TEXT NOT NULL,
                version TEXT NOT NULL,
                attribute_path TEXT,
                description TEXT,
                long_description TEXT,
                search_text TEXT,  -- This field is for FTS indexing only, minimal content
                homepage TEXT,
                license TEXT,
                platforms TEXT,
                maintainers TEXT,
                category TEXT,
                broken BOOLEAN DEFAULT 0,
                unfree BOOLEAN DEFAULT 0,
                available BOOLEAN DEFAULT 1,
                insecure BOOLEAN DEFAULT 0,
                unsupported BOOLEAN DEFAULT 0,
                main_program TEXT,
                position TEXT,
                outputs_to_install TEXT,
                last_updated TEXT,
                content_hash INTEGER
            )
        """)

    def _create_fts_table(self, cursor: sqlite3.Cursor) -> None:
        """Create FTS virtual table for full-text search"""
        try:
            # Create FTS virtual table with contentless mode
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS packages_fts USING fts5(
                    package_id, 
                    package_name, 
                    attribute_path, 
                    description, 
                    long_description, 
                    main_program,
                    content='',
                    content_rowid='package_id'
                )
            """)
            
            # Populate FTS table with minimal search content
            cursor.execute("""
                INSERT INTO packages_fts(package_id, package_name, attribute_path, description, long_description, main_program)
                SELECT package_id, package_name, attribute_path, description, long_description, main_program
                FROM packages
            """)
            
            logger.info("FTS virtual table created and populated")
        except Exception as e:
            logger.error("Failed to create FTS table: %s", e)

    def _create_indexes(self, cursor: sqlite3.Cursor) -> None:
        """Create indexes for performance optimization"""
        
        # Index on package name for fast lookups
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_package_name ON packages(package_name)")
        except Exception as e:
            logger.warning("Failed to create package_name index: %s", e)
        
        # Index on category for filtering
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_category ON packages(category)")
        except Exception as e:
            logger.warning("Failed to create category index: %s", e)
        
        # Index on status flags
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON packages(broken, unfree, available)")
        except Exception as e:
            logger.warning("Failed to create status index: %s", e)

    def _convert_packages_to_sqlite_format(self, packages: List[Dict[str, Any]]) -> List[tuple]:
        """Convert package dictionaries to SQLite format (tuples for executemany)."""
        sqlite_packages = []
        
        for p in packages:
            pkg_id = self._package_id(p)
            
            # Create minimal search text for FTS (just enough for search relevance)
            search_parts = [
                p.get("packageName") or "",
                p.get("description") or "",
                p.get("longDescription") or "",
                p.get("attributePath") or "",
                p.get("mainProgram") or "",
            ]
            search_text = " ".join(filter(None, search_parts))
            
            # Convert to SQLite tuple format
            sqlite_pkg = (
                pkg_id,
                p.get("packageName") or "",
                p.get("version") or "",
                p.get("attributePath") or "",
                p.get("description") or "",
                p.get("longDescription") or "",
                search_text,
                p.get("homepage") or "",
                json.dumps(p.get("license")) if p.get("license") is not None else "",
                json.dumps(p.get("platforms")) if p.get("platforms") is not None else "",
                json.dumps(p.get("maintainers")) if p.get("maintainers") is not None else "",
                p.get("category") or "",
                bool(p.get("broken", False)),
                bool(p.get("unfree", False)),
                bool(p.get("available", True)),
                bool(p.get("insecure", False)),
                bool(p.get("unsupported", False)),
                p.get("mainProgram") or "",
                p.get("position") or "",
                json.dumps(p.get("outputsToInstall")) if p.get("outputsToInstall") is not None else "",
                p.get("lastUpdated") or "",
                int(p.get("content_hash") or 0)
            )
            
            sqlite_packages.append(sqlite_pkg)

        return sqlite_packages
    
    def create_minified_db_from_main(self, main_db_path: str) -> None:
        """Create a minified database by copying essential data from main database.
        
        For SQLite, this involves copying the main table with a subset of columns
        and maintaining proper indexing.
        """
        self._ensure_parent_dir()
        logger.info("Creating minified SQLite database at %s from main DB at %s", self.output_path, main_db_path)

        # Connect to main database
        main_connection = sqlite3.connect(main_db_path)
        
        # Connect to minified database
        self._db_connection = sqlite3.connect(str(self.output_path))
        cursor = self._db_connection.cursor()
        
        # Create tables in minified database
        self._create_tables(cursor)
        
        # Copy only essential columns from main database
        essential_columns = [
            "package_id", "package_name", "version", "attribute_path", "description", 
            "search_text", "homepage", "license", "maintainers", "broken", "unfree", "available", 
            "insecure", "unsupported", "main_program", "content_hash"
        ]
        
        # Build query with only essential columns
        columns_str = ", ".join(essential_columns)
        query = f"SELECT {columns_str} FROM packages"
        
        # Copy data
        main_cursor = main_connection.cursor()
        main_cursor.execute(query)
        
        # Insert into minified database
        if essential_columns:
            placeholders = ", ".join(["?" for _ in essential_columns])
            cursor.executemany(
                f"INSERT OR REPLACE INTO packages ({columns_str}) VALUES ({placeholders})",
                main_cursor.fetchall()
            )
        
        main_connection.close()
        
        # Create indexes on minified table
        self._create_indexes(cursor)
        self._create_fts_table(cursor)
        
        # Commit and close
        self._db_connection.commit()
        self._db_connection.close()
        self._db_connection = None

        logger.info("Minified SQLite artifact written: %s", self.output_path)

        if self.s3_bucket and self.s3_key:
            self._upload_to_s3()

    def _package_id(self, p: Dict[str, Any]) -> str:
        # Prefer attributePath, fallback to name@version
        attr = (p.get("attributePath") or "").strip()
        if attr:
            return attr
        name = (p.get("packageName") or "").strip()
        ver = (p.get("version") or "").strip()
        return f"{name}@{ver}" if name or ver else "unknown"

    def _delete_s3_objects(self, bucket: str, prefix: str) -> None:
        """Delete all objects with given prefix from S3 bucket."""
        if boto3 is None:
            logger.error("boto3 not available for S3 deletion")
            return
            
        try:
            s3 = boto3.client("s3", region_name=self.region)
            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            
            if 'Contents' in response:
                objects = [{'Key': obj['Key']} for obj in response['Contents']]
                if objects:
                    logger.info("Deleting %d objects from s3://%s/%s", len(objects), bucket, prefix)
                    s3.delete_objects(
                        Bucket=bucket,
                        Delete={'Objects': objects}
                    )
                    logger.info("Successfully deleted %d objects", len(objects))
                else:
                    logger.info("No objects found to delete at s3://%s/%s", bucket, prefix)
            else:
                logger.info("No objects found to delete at s3://%s/%s", bucket, prefix)
        except Exception as e:
            logger.warning("Failed to delete S3 objects at %s/%s: %s", bucket, prefix, e)

    def _upload_to_s3(self) -> None:
        if not (self.region and self.s3_bucket and self.s3_key):
            logger.info("S3 upload not configured; skipping.")
            return
            
        if boto3 is None:
            logger.error("boto3 not available for S3 upload")
            return
            
        logger.info(
            "Uploading SQLite database to s3://%s/%s (region=%s)",
            self.s3_bucket,
            self.s3_key,
            self.region,
        )
        
        # Clear existing objects if requested
        if self.clear_before_upload:
            logger.info("Clearing existing objects before upload...")
            self._delete_s3_objects(self.s3_bucket, self.s3_key)
        
        # Upload the SQLite database file
        s3 = boto3.client("s3", region_name=self.region)
        s3.upload_file(str(self.output_path), self.s3_bucket, self.s3_key)
        
        logger.info("Upload complete.")