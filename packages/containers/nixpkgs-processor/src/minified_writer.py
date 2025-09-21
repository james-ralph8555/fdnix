#!/usr/bin/env python3

import json
import logging
import os
import random
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import zstandard as zstd

logger = logging.getLogger("fdnix.minified-writer")


class MinifiedWriter:
    def __init__(
        self,
        output_path: str,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        region: Optional[str] = None,
        clear_before_upload: bool = True,
        dict_size: int = 65536,
        sample_count: int = 10000,
        compression_level: int = 3,
    ) -> None:
        self.output_path = Path(output_path)
        # Always write a sibling dictionary file with `.dict` suffix
        self.dict_output_path = self.output_path.with_suffix('.dict')
        self.s3_bucket = s3_bucket
        self.s3_key = s3_key
        self.region = region
        self.clear_before_upload = clear_before_upload
        self.dict_size = dict_size
        self.sample_count = sample_count
        self.compression_level = compression_level

    def write_artifact(self, packages: List[Dict[str, Any]]) -> None:
        """Write minified artifact with zstd compression and shared dictionary."""
        self._ensure_parent_dir()
        
        logger.info("Creating minified SQLite database at %s", self.output_path)
        logger.info("Using zstd compression: dict_size=%d, sample_count=%d, level=%d", 
                   self.dict_size, self.sample_count, self.compression_level)

        # Phase 1: Train compression dictionary
        logger.info("Phase 1: Training compression dictionary...")
        dictionary = self._train_dictionary(packages)
        
        # Save dictionary to file
        with open(self.dict_output_path, 'wb') as f:
            f.write(dictionary.as_bytes())
        logger.info(
            "Compression dictionary saved to %s (size: %d bytes)",
            self.dict_output_path,
            len(dictionary.as_bytes()),
        )

        # Phase 2: Build compressed SQLite database
        logger.info("Phase 2: Building compressed SQLite database...")
        self._build_compressed_database(packages, dictionary)

        logger.info("Minified SQLite artifact written: %s", self.output_path)
        logger.info("Compression dictionary written: %s", self.dict_output_path)

        if self.s3_bucket and self.s3_key:
            self._upload_to_s3()

    def _train_dictionary(self, packages: List[Dict[str, Any]]) -> zstd.ZstdCompressionDict:
        """Train zstd compression dictionary from sample data."""
        logger.info("Sampling %d packages for dictionary training...", self.sample_count)
        
        # Sample packages if we have more than the sample count
        sample_packages = packages
        if len(packages) > self.sample_count:
            sample_packages = random.sample(packages, self.sample_count)
        
        # Prepare sample bytes
        samples = []
        for i, pkg in enumerate(sample_packages):
            # Create the final JSON object we intend to store
            json_obj = self._create_package_json(pkg)
            json_bytes = json.dumps(json_obj, separators=(',', ':')).encode('utf-8')
            samples.append(json_bytes)
            
            if (i + 1) % 1000 == 0:
                logger.info("Processed %d/%d samples for dictionary training", i + 1, len(sample_packages))
        
        logger.info("Training dictionary from %d samples...", len(samples))
        
        # Train the dictionary using zstandard. The function expects the
        # samples as a sequence of bytes-like objects.
        dictionary = zstd.train_dictionary(self.dict_size, samples)
        
        logger.info("Dictionary trained successfully (size: %d bytes)", len(dictionary))
        return dictionary

    def _build_compressed_database(self, packages: List[Dict[str, Any]], dictionary: zstd.ZstdCompressionDict) -> None:
        """Build SQLite database with compressed data using the trained dictionary."""
        # Initialize database
        conn = sqlite3.connect(str(self.output_path))
        cursor = conn.cursor()
        
        # Create schema
        self._create_schema(cursor)
        
        # Prepare compressor with dictionary
        compressor = zstd.ZstdCompressor(level=self.compression_level, dict_data=dictionary)
        
        # Prepare decompressor with dictionary (for verification)
        decompressor = zstd.ZstdDecompressor(dict_data=dictionary)
        
        logger.info("Compressing and inserting package data...")
        
        # Insert compressed packages
        for i, pkg in enumerate(packages):
            package_id = self._package_id(pkg)
            
            # Create and compress package JSON
            json_obj = self._create_package_json(pkg)
            json_bytes = json.dumps(json_obj, separators=(',', ':')).encode('utf-8')
            compressed_data = compressor.compress(json_bytes)
            
            # Verify compression works
            try:
                decompressed = decompressor.decompress(compressed_data)
                assert decompressed == json_bytes, "Decompression verification failed"
            except Exception as e:
                logger.error("Compression verification failed for package %s: %s", package_id, e)
                raise
            
            # Insert key-value pair
            cursor.execute(
                "INSERT OR REPLACE INTO packages_kv (id, data) VALUES (?, ?)",
                (package_id, compressed_data)
            )
            
            # Insert FTS data
            fts_data = self._extract_fts_data(pkg)
            cursor.execute(
                "INSERT OR REPLACE INTO packages_fts (id, name, description) VALUES (?, ?, ?)",
                (package_id, fts_data['name'], fts_data['description'])
            )
            
            if (i + 1) % 1000 == 0:
                logger.info("Processed %d/%d packages (compression ratio: %.2f%%)", 
                           i + 1, len(packages), 
                           (len(compressed_data) / len(json_bytes)) * 100)
        
        # Commit and optimize
        conn.commit()
        logger.info("Running VACUUM to optimize database size...")
        cursor.execute("VACUUM")
        conn.commit()
        conn.close()
        
        logger.info("Compressed database created successfully")

    def _create_schema(self, cursor: sqlite3.Cursor) -> None:
        """Create the database schema for compressed storage."""
        # Key-value table for compressed data
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS packages_kv (
                id TEXT PRIMARY KEY,
                data BLOB NOT NULL
            )
        """)
        
        # FTS5 table for searching with external content
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS packages_fts USING fts5(
                id,
                name,
                description,
                content='packages_kv',
                content_rowid='id'
            )
        """)
        
        # Create indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_packages_kv_id ON packages_kv(id)")
        
        logger.info("Database schema created")

    def _create_package_json(self, pkg: Dict[str, Any]) -> Dict[str, Any]:
        """Create the complete JSON object for a package."""
        return {
            "package_id": self._package_id(pkg),
            "package_name": pkg.get("packageName") or "",
            "version": pkg.get("version") or "",
            "attribute_path": pkg.get("attributePath") or "",
            "description": pkg.get("description") or "",
            "long_description": pkg.get("longDescription") or "",
            "homepage": pkg.get("homepage") or "",
            "license": pkg.get("license"),
            "platforms": pkg.get("platforms"),
            "maintainers": pkg.get("maintainers"),
            "category": pkg.get("category") or "",
            "broken": bool(pkg.get("broken", False)),
            "unfree": bool(pkg.get("unfree", False)),
            "available": bool(pkg.get("available", True)),
            "insecure": bool(pkg.get("insecure", False)),
            "unsupported": bool(pkg.get("unsupported", False)),
            "main_program": pkg.get("mainProgram") or "",
            "position": pkg.get("position") or "",
            "outputs_to_install": pkg.get("outputsToInstall"),
            "last_updated": pkg.get("lastUpdated") or "",
            "content_hash": int(pkg.get("content_hash") or 0)
        }

    def _extract_fts_data(self, pkg: Dict[str, Any]) -> Dict[str, str]:
        """Extract data for FTS indexing."""
        return {
            'name': pkg.get("packageName") or "",
            'description': pkg.get("description") or ""
        }

    def _package_id(self, p: Dict[str, Any]) -> str:
        """Generate package ID without system suffix for deduplication."""
        # Use attribute path but remove system part for uniqueness
        attr_path = (p.get("attributePath") or "").strip()
        if attr_path:
            # Remove system suffix if present
            parts = attr_path.split(".")
            if len(parts) >= 2 and any(sys in parts[-1] for sys in ["linux", "darwin", "windows"]):
                return ".".join(parts[:-1])
            return attr_path
        
        # Fallback to name@version
        name = (p.get("packageName") or "").strip()
        ver = (p.get("version") or "").strip()
        return f"{name}@{ver}" if name or ver else "unknown"

    def _ensure_parent_dir(self) -> None:
        """Ensure parent directory exists."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _upload_to_s3(self) -> None:
        """Upload artifacts to S3."""
        try:
            import boto3
        except ImportError:
            logger.error("boto3 not available for S3 upload")
            return
            
        if not (self.region and self.s3_bucket and self.s3_key):
            logger.info("S3 upload not configured; skipping.")
            return
        
        s3 = boto3.client("s3", region_name=self.region)
        
        # Upload database
        logger.info("Uploading minified database to s3://%s/%s", self.s3_bucket, self.s3_key)
        s3.upload_file(str(self.output_path), self.s3_bucket, self.s3_key)
        
        # Upload dictionary
        # Ensure dictionary key uses `.dict` suffix regardless of original
        from pathlib import Path as _Path
        dict_key = str(_Path(self.s3_key).with_suffix('.dict'))
        logger.info("Uploading compression dictionary to s3://%s/%s", self.s3_bucket, dict_key)
        s3.upload_file(str(self.dict_output_path), self.s3_bucket, dict_key)
        
        logger.info("S3 upload complete")

    def create_minified_db_from_main(self, main_db_path: str) -> None:
        """Create minified database from existing main database."""
        logger.info("Creating minified database from main DB at %s", main_db_path)
        
        # Read data from main database
        main_conn = sqlite3.connect(main_db_path)
        main_cursor = main_conn.cursor()
        
        # Extract package data
        main_cursor.execute("""
            SELECT package_id, package_name, version, attribute_path, description, 
                   long_description, homepage, license, platforms, maintainers, 
                   category, broken, unfree, available, insecure, unsupported, 
                   main_program, position, outputs_to_install, last_updated, content_hash
            FROM packages
        """)
        
        columns = [desc[0] for desc in main_cursor.description]
        packages = []
        
        for row in main_cursor.fetchall():
            pkg = dict(zip(columns, row))
            # Convert JSON strings back to objects
            for field in ['license', 'platforms', 'maintainers', 'outputs_to_install']:
                if pkg[field]:
                    try:
                        pkg[field] = json.loads(pkg[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            packages.append(pkg)
        
        main_conn.close()
        
        # Write minified version
        self.write_artifact(packages)
