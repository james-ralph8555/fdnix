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
import zstandard as zstd

try:
    import boto3  # type: ignore
except Exception:  # pragma: no cover - boto3 may be absent in local-only runs
    boto3 = None  # type: ignore

try:
    from minified_writer import MinifiedWriter
except ImportError:
    MinifiedWriter = None


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
        logger.info("Creating normalized SQLite database at %s", self.output_path)

        # Connect to SQLite database
        self._db_connection = sqlite3.connect(str(self.output_path))
        cursor = self._db_connection.cursor()
        
        # Create normalized tables
        self._create_tables(cursor)
        
        # Convert packages to normalized SQLite format and insert all data
        self._convert_packages_to_sqlite_format(packages)

        # Create FTS virtual table
        self._create_fts_table(cursor)
        
        # Create indexes for performance
        self._create_indexes(cursor)
        
        # Commit changes
        self._db_connection.commit()

        logger.info("Normalized SQLite artifact written: %s", self.output_path)

        if self.s3_bucket and self.s3_key:
            self._upload_to_s3()
        
        # Close connection
        if self._db_connection:
            self._db_connection.close()
            self._db_connection = None

    def _ensure_parent_dir(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _create_tables(self, cursor: sqlite3.Cursor) -> None:
        """Create normalized database tables"""
        # Create lookup tables
        self._create_lookup_tables(cursor)
        
        # Create main packages table
        self._create_packages_table(cursor)
        
        # Create junction tables
        self._create_junction_tables(cursor)
        
        # Create package variations table
        self._create_variations_table(cursor)

    def _create_lookup_tables(self, cursor: sqlite3.Cursor) -> None:
        """Create lookup tables for licenses, architectures, and maintainers"""
        # Licenses table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                license_id INTEGER PRIMARY KEY,
                short_name TEXT UNIQUE NOT NULL,
                full_name TEXT,
                spdx_id TEXT,
                url TEXT,
                is_free BOOLEAN,
                is_redistributable BOOLEAN,
                is_deprecated BOOLEAN
            )
        """)
        
        # Architectures table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS architectures (
                arch_id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            )
        """)
        
        # Maintainers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS maintainers (
                maintainer_id INTEGER PRIMARY KEY,
                name TEXT,
                email TEXT,
                github TEXT,
                github_id INTEGER,
                UNIQUE(name, email, github)
            )
        """)

    def _create_packages_table(self, cursor: sqlite3.Cursor) -> None:
        """Create main packages table (one row per unique package)"""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS packages (
                package_id TEXT PRIMARY KEY,
                package_name TEXT NOT NULL,
                version TEXT NOT NULL,
                attribute_path TEXT,
                description TEXT,
                long_description TEXT,
                search_text TEXT,
                homepage TEXT,
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

    def _create_junction_tables(self, cursor: sqlite3.Cursor) -> None:
        """Create many-to-many junction tables"""
        # Package licenses junction table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS package_licenses (
                package_id TEXT NOT NULL,
                license_id INTEGER NOT NULL,
                FOREIGN KEY(package_id) REFERENCES packages(package_id),
                FOREIGN KEY(license_id) REFERENCES licenses(license_id),
                PRIMARY KEY(package_id, license_id)
            )
        """)
        
        # Package architectures junction table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS package_architectures (
                package_id TEXT NOT NULL,
                arch_id INTEGER NOT NULL,
                FOREIGN KEY(package_id) REFERENCES packages(package_id),
                FOREIGN KEY(arch_id) REFERENCES architectures(arch_id),
                PRIMARY KEY(package_id, arch_id)
            )
        """)
        
        # Package maintainers junction table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS package_maintainers (
                package_id TEXT NOT NULL,
                maintainer_id INTEGER NOT NULL,
                FOREIGN KEY(package_id) REFERENCES packages(package_id),
                FOREIGN KEY(maintainer_id) REFERENCES maintainers(maintainer_id),
                PRIMARY KEY(package_id, maintainer_id)
            )
        """)

    def _create_variations_table(self, cursor: sqlite3.Cursor) -> None:
        """Create package variations table (package + system combinations)"""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS package_variations (
                variation_id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL,
                system TEXT NOT NULL,
                drv_path TEXT,
                outputs TEXT,
                FOREIGN KEY(package_id) REFERENCES packages(package_id),
                UNIQUE(package_id, system)
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
        
        # Indexes for normalized tables
        self._create_normalized_indexes(cursor)

    def _create_normalized_indexes(self, cursor: sqlite3.Cursor) -> None:
        """Create indexes for normalized tables"""
        
        # Index on license short name
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_license_short_name ON licenses(short_name)")
        except Exception as e:
            logger.warning("Failed to create license_short_name index: %s", e)
        
        # Index on architecture name
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_architecture_name ON architectures(name)")
        except Exception as e:
            logger.warning("Failed to create architecture_name index: %s", e)
        
        # Index on maintainer name/email/github
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_maintainer_name ON maintainers(name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_maintainer_email ON maintainers(email)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_maintainer_github ON maintainers(github)")
        except Exception as e:
            logger.warning("Failed to create maintainer indexes: %s", e)
        
        # Indexes for junction tables
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_package_licenses_package_id ON package_licenses(package_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_package_licenses_license_id ON package_licenses(license_id)")
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_package_architectures_package_id ON package_architectures(package_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_package_architectures_arch_id ON package_architectures(arch_id)")
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_package_maintainers_package_id ON package_maintainers(package_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_package_maintainers_maintainer_id ON package_maintainers(maintainer_id)")
        except Exception as e:
            logger.warning("Failed to create junction table indexes: %s", e)
        
        # Index for variations table
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_variations_package_id ON package_variations(package_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_variations_system ON package_variations(system)")
        except Exception as e:
            logger.warning("Failed to create variations table indexes: %s", e)

    def _convert_packages_to_sqlite_format(self, packages: List[Dict[str, Any]]) -> None:
        """Convert package dictionaries to normalized SQLite format and insert all data."""
        if not packages:
            return
            
        cursor = self._db_connection.cursor()
        
        # Extract unique values for normalization (from all packages before deduplication)
        licenses_data = self._extract_licenses(packages)
        architectures_data = self._extract_architectures(packages)
        maintainers_data = self._extract_maintainers(packages)
        
        # Insert lookup table data
        self._insert_lookup_data(cursor, licenses_data, architectures_data, maintainers_data)
        
        # Deduplicate packages by merging variants
        deduplicated_packages = self._deduplicate_packages(packages)
        
        # Process and insert packages and relationships
        self._insert_packages_and_relationships(cursor, deduplicated_packages)
        
        logger.info("Normalized %d packages (deduplicated from %d) with lookup tables", 
                   len(deduplicated_packages), len(packages))

    def _deduplicate_packages(self, packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate packages by merging variants with different architectures."""
        if not packages:
            return []
            
        # Group packages by their base package ID
        package_groups = {}
        
        for p in packages:
            pkg_id = self._package_id(p)
            if pkg_id not in package_groups:
                package_groups[pkg_id] = []
            package_groups[pkg_id].append(p)
        
        deduplicated_packages = []
        
        for pkg_id, variants in package_groups.items():
            if len(variants) == 1:
                # No deduplication needed
                deduplicated_packages.append(variants[0])
                continue
            
            # Merge variants
            merged_pkg = self._merge_package_variants(variants)
            deduplicated_packages.append(merged_pkg)
        
        return deduplicated_packages
    
    def _merge_package_variants(self, variants: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge multiple package variants into one unified package."""
        if not variants:
            return {}
        if len(variants) == 1:
            return variants[0]
        
        # Use first variant as base
        merged = variants[0].copy()
        
        # Merge architectures (union of all)
        all_architectures = set()
        for variant in variants:
            platforms = variant.get("platforms", [])
            if isinstance(platforms, list):
                for platform in platforms:
                    if isinstance(platform, str):
                        all_architectures.add(platform)
        merged["platforms"] = list(all_architectures) if all_architectures else None
        
        # Merge maintainers (union of all, unique by key)
        all_maintainers = {}
        for variant in variants:
            maintainers = variant.get("maintainers", [])
            if isinstance(maintainers, list):
                for maintainer in maintainers:
                    if isinstance(maintainer, dict):
                        key = (
                            maintainer.get("name", ""),
                            maintainer.get("email", ""),
                            maintainer.get("github", "")
                        )
                        if any(key):
                            all_maintainers[key] = maintainer
        merged["maintainers"] = list(all_maintainers.values()) if all_maintainers else None
        
        # License should be the same across variants, use first non-null
        for variant in variants:
            if variant.get("license"):
                merged["license"] = variant["license"]
                break
        
        # Merge other appropriate fields
        # Use first non-null value for most fields
        fields_to_merge = ["description", "longDescription", "homepage", "category", "mainProgram"]
        for field in fields_to_merge:
            if not merged.get(field):
                for variant in variants:
                    if variant.get(field):
                        merged[field] = variant[field]
                        break
        
        # Boolean fields: logical OR (if any variant is broken, package is broken)
        bool_fields = ["broken", "unfree", "insecure", "unsupported"]
        for field in bool_fields:
            for variant in variants:
                if variant.get(field, False):
                    merged[field] = True
                    break
        
        # Available field: logical AND (if all variants are available, package is available)
        merged["available"] = all(variant.get("available", True) for variant in variants)
        
        return merged

    def _extract_licenses(self, packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract unique license information from all packages."""
        licenses = {}
        
        for p in packages:
            license_info = p.get("license")
            if not license_info:
                continue
                
            if isinstance(license_info, dict):
                if license_info.get("type") == "array":
                    for lic in license_info.get("licenses", []):
                        if lic and lic.get("shortName"):
                            licenses[lic["shortName"]] = lic
                elif license_info.get("shortName"):
                    licenses[license_info["shortName"]] = license_info
            elif isinstance(license_info, str):
                licenses[license_info] = {"shortName": license_info, "fullName": "", "spdxId": "", "url": ""}
        
        return list(licenses.values())

    def _extract_architectures(self, packages: List[Dict[str, Any]]) -> List[str]:
        """Extract unique architecture names from all packages."""
        architectures = set()
        
        for p in packages:
            platforms = p.get("platforms", [])
            if isinstance(platforms, list):
                for platform in platforms:
                    if isinstance(platform, str):
                        architectures.add(platform)
        
        return sorted(list(architectures))

    def _extract_maintainers(self, packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract unique maintainer information from all packages."""
        maintainers = {}
        maintainer_id = 1
        
        for p in packages:
            package_maintainers = p.get("maintainers", [])
            if not isinstance(package_maintainers, list):
                continue
                
            for maintainer in package_maintainers:
                if not isinstance(maintainer, dict):
                    continue
                    
                # Create unique key for maintainer
                key = (
                    maintainer.get("name", ""),
                    maintainer.get("email", ""), 
                    maintainer.get("github", "")
                )
                
                if key not in maintainers and any(key):
                    maintainers[key] = {
                        "maintainer_id": maintainer_id,
                        "name": maintainer.get("name", ""),
                        "email": maintainer.get("email", ""),
                        "github": maintainer.get("github", ""),
                        "github_id": maintainer.get("githubId")
                    }
                    maintainer_id += 1
        
        return list(maintainers.values())

    def _insert_lookup_data(self, cursor: sqlite3.Cursor, licenses: List[Dict[str, Any]], 
                           architectures: List[str], maintainers: List[Dict[str, Any]]) -> None:
        """Insert data into lookup tables."""
        # Insert licenses
        if licenses:
            license_tuples = []
            for lic in licenses:
                license_tuples.append((
                    lic.get("shortName", ""),
                    lic.get("fullName", ""),
                    lic.get("spdxId", ""),
                    lic.get("url", ""),
                    lic.get("free"),
                    lic.get("redistributable"),
                    lic.get("deprecated")
                ))
            
            cursor.executemany("""
                INSERT OR IGNORE INTO licenses (short_name, full_name, spdx_id, url, is_free, is_redistributable, is_deprecated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, license_tuples)
            logger.info("Inserted %d unique licenses", len(license_tuples))
        
        # Insert architectures
        if architectures:
            arch_tuples = [(arch,) for arch in architectures]
            cursor.executemany("INSERT OR IGNORE INTO architectures (name) VALUES (?)", arch_tuples)
            logger.info("Inserted %d unique architectures", len(arch_tuples))
        
        # Insert maintainers
        if maintainers:
            maintainer_tuples = []
            for maintainer in maintainers:
                maintainer_tuples.append((
                    maintainer.get("name", ""),
                    maintainer.get("email", ""),
                    maintainer.get("github", ""),
                    maintainer.get("github_id")
                ))
            
            cursor.executemany("""
                INSERT OR IGNORE INTO maintainers (name, email, github, github_id)
                VALUES (?, ?, ?, ?)
            """, maintainer_tuples)
            logger.info("Inserted %d unique maintainers", len(maintainer_tuples))

    def _insert_packages_and_relationships(self, cursor: sqlite3.Cursor, packages: List[Dict[str, Any]]) -> None:
        """Insert packages and their relationships to lookup tables."""
        package_tuples = []
        license_relationships = []
        architecture_relationships = []
        maintainer_relationships = []
        variation_tuples = []
        
        for p in packages:
            pkg_id = self._package_id(p)
            
            # Create minimal search text for FTS
            search_parts = [
                p.get("packageName") or "",
                p.get("description") or "",
                p.get("longDescription") or "",
                p.get("attributePath") or "",
                p.get("mainProgram") or "",
            ]
            search_text = " ".join(filter(None, search_parts))
            
            # Package tuple for main packages table
            package_tuples.append((
                pkg_id,
                p.get("packageName") or "",
                p.get("version") or "",
                p.get("attributePath") or "",
                p.get("description") or "",
                p.get("longDescription") or "",
                search_text,
                p.get("homepage") or "",
                p.get("category") or "",
                bool(p.get("broken", False)),
                bool(p.get("unfree", False)),
                bool(p.get("available", True)),
                bool(p.get("insecure", False)),
                bool(p.get("unsupported", False)),
                p.get("mainProgram") or "",
                p.get("position") or "",
                json.dumps(p.get("outputsToInstall")) if p.get("outputsToInstall") else "",
                p.get("lastUpdated") or "",
                int(p.get("content_hash") or 0)
            ))
            
            # Extract system from attribute path for variations
            system = self._extract_system_from_attribute_path(p.get("attributePath", ""))
            if system:
                variation_tuples.append((
                    f"{pkg_id}.{system}",
                    pkg_id,
                    system,
                    p.get("drvPath", ""),
                    json.dumps(p.get("outputs", {}))
                ))
            
            # License relationships
            license_info = p.get("license")
            if license_info:
                if isinstance(license_info, dict):
                    if license_info.get("type") == "array":
                        for lic in license_info.get("licenses", []):
                            if lic and lic.get("shortName"):
                                license_relationships.append((pkg_id, lic["shortName"]))
                    elif license_info.get("shortName"):
                        license_relationships.append((pkg_id, license_info["shortName"]))
                elif isinstance(license_info, str):
                    license_relationships.append((pkg_id, license_info))
            
            # Architecture relationships
            platforms = p.get("platforms", [])
            if isinstance(platforms, list):
                for platform in platforms:
                    if isinstance(platform, str):
                        architecture_relationships.append((pkg_id, platform))
            
            # Maintainer relationships
            package_maintainers = p.get("maintainers", [])
            if isinstance(package_maintainers, list):
                for maintainer in package_maintainers:
                    if isinstance(maintainer, dict):
                        key = (
                            maintainer.get("name", ""),
                            maintainer.get("email", ""),
                            maintainer.get("github", "")
                        )
                        if any(key):
                            maintainer_relationships.append((pkg_id, key))
        
        # Insert packages
        if package_tuples:
            cursor.executemany("""
                INSERT OR REPLACE INTO packages (
                    package_id, package_name, version, attribute_path, description, 
                    long_description, search_text, homepage, category, broken, unfree, 
                    available, insecure, unsupported, main_program, position, 
                    outputs_to_install, last_updated, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, package_tuples)
        
        # Insert variations
        if variation_tuples:
            cursor.executemany("""
                INSERT OR REPLACE INTO package_variations (variation_id, package_id, system, drv_path, outputs)
                VALUES (?, ?, ?, ?, ?)
            """, variation_tuples)
        
        # Insert license relationships
        if license_relationships:
            cursor.executemany("""
                INSERT OR IGNORE INTO package_licenses (package_id, license_id)
                SELECT ?, license_id FROM licenses WHERE short_name = ?
            """, license_relationships)
        
        # Insert architecture relationships
        if architecture_relationships:
            cursor.executemany("""
                INSERT OR IGNORE INTO package_architectures (package_id, arch_id)
                SELECT ?, arch_id FROM architectures WHERE name = ?
            """, architecture_relationships)
        
        # Insert maintainer relationships
        if maintainer_relationships:
            cursor.executemany("""
                INSERT OR IGNORE INTO package_maintainers (package_id, maintainer_id)
                SELECT ?, maintainer_id FROM maintainers 
                WHERE (name = ? OR email = ? OR github = ?) AND (name != '' OR email != '' OR github != '')
            """, [(pkg_id, key[0], key[1], key[2]) for pkg_id, key in maintainer_relationships])

    def _extract_system_from_attribute_path(self, attribute_path: str) -> str:
        """Extract system/architecture from attribute path."""
        if not attribute_path:
            return ""
        
        parts = attribute_path.split(".")
        if len(parts) >= 2:
            # Last part is usually the system (e.g., "x86_64-linux", "aarch64-darwin")
            return parts[-1]
        return ""

    def _package_id(self, p: Dict[str, Any]) -> str:
        # Generate package_id without system suffix for main packages table
        # Use attribute path but remove system part for uniqueness
        attr_path = p.get("attributePath", "").strip()
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
    
    def create_minified_db_from_main(self, main_db_path: str) -> None:
        """Create a minified database with zstd compression from main database."""
        self._ensure_parent_dir()
        
        if MinifiedWriter is None:
            raise ImportError("MinifiedWriter is required for minified database creation but is not available")
        
        logger.info("Creating zstd-compressed minified database at %s from main DB at %s", 
                   self.output_path, main_db_path)
        logger.info("Using MinifiedWriter with zstd compression (this preserves all metadata)")

        # Get zstd configuration from environment
        dict_size = int(os.environ.get("ZSTD_DICT_SIZE", "65536"))
        sample_count = int(os.environ.get("ZSTD_SAMPLE_COUNT", "10000"))
        compression_level = int(os.environ.get("ZSTD_COMPRESSION_LEVEL", "3"))
        
        # Create minified writer with zstd compression
        minified_writer = MinifiedWriter(
            output_path=str(self.output_path),
            s3_bucket=self.s3_bucket,
            s3_key=self.s3_key,
            region=self.region,
            clear_before_upload=self.clear_before_upload,
            dict_size=dict_size,
            sample_count=sample_count,
            compression_level=compression_level
        )
        
        # Extract package data from main database
        logger.info("Extracting package metadata from main database...")
        packages = self._extract_packages_from_main_db(main_db_path)
        
        # Create minified database with zstd compression
        logger.info("Writing compressed minified database with %d packages...", len(packages))
        minified_writer.write_artifact(packages)
        
        logger.info("Zstd-compressed minified database created: %s", self.output_path)

    
    def _extract_packages_from_main_db(self, main_db_path: str) -> List[Dict[str, Any]]:
        """Extract package data from main database for zstd compression."""
        main_conn = sqlite3.connect(main_db_path)
        main_cursor = main_conn.cursor()
        
        # Extract package data from main packages table
        logger.info("Extracting package data from main database...")
        main_cursor.execute("""
            SELECT package_id, package_name, version, attribute_path, description, 
                   long_description, homepage, category, broken, unfree, available, 
                   insecure, unsupported, main_program, position, outputs_to_install, 
                   last_updated, content_hash
            FROM packages
        """)
        
        columns = [desc[0] for desc in main_cursor.description]
        packages = []
        
        for row in main_cursor.fetchall():
            pkg = dict(zip(columns, row))
            package_id = pkg['package_id']
            
            # Convert outputs_to_install back to object if it exists
            if pkg.get('outputs_to_install'):
                try:
                    pkg['outputs_to_install'] = json.loads(pkg['outputs_to_install'])
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # Extract licenses from junction table
            main_cursor.execute("""
                SELECT l.short_name, l.full_name, l.spdx_id, l.url, l.is_free, l.is_redistributable, l.is_deprecated
                FROM licenses l
                JOIN package_licenses pl ON l.license_id = pl.license_id
                WHERE pl.package_id = ?
            """, (package_id,))
            
            licenses = []
            for lic_row in main_cursor.fetchall():
                licenses.append({
                    'shortName': lic_row[0],
                    'fullName': lic_row[1],
                    'spdxId': lic_row[2],
                    'url': lic_row[3],
                    'free': lic_row[4],
                    'redistributable': lic_row[5],
                    'deprecated': lic_row[6]
                })
            
            if len(licenses) == 1:
                pkg['license'] = licenses[0]
            elif len(licenses) > 1:
                pkg['license'] = {
                    'type': 'array',
                    'licenses': licenses
                }
            else:
                pkg['license'] = None
            
            # Extract maintainers from junction table
            main_cursor.execute("""
                SELECT m.name, m.email, m.github, m.github_id
                FROM maintainers m
                JOIN package_maintainers pm ON m.maintainer_id = pm.maintainer_id
                WHERE pm.package_id = ?
            """, (package_id,))
            
            maintainers = []
            for maint_row in main_cursor.fetchall():
                maintainer = {}
                # Only add fields that have values
                if maint_row[0]:  # name
                    maintainer['name'] = maint_row[0]
                if maint_row[1]:  # email
                    maintainer['email'] = maint_row[1]
                if maint_row[2]:  # github
                    maintainer['github'] = maint_row[2]
                if maint_row[3] is not None:  # github_id (can be 0)
                    maintainer['githubId'] = maint_row[3]
                
                # Add maintainer if it has any data (githubId alone is valid)
                if maintainer:
                    maintainers.append(maintainer)
            
            pkg['maintainers'] = maintainers if maintainers else None
            
            # Extract platforms (architectures) from junction table
            main_cursor.execute("""
                SELECT a.name
                FROM architectures a
                JOIN package_architectures pa ON a.arch_id = pa.arch_id
                WHERE pa.package_id = ?
            """, (package_id,))
            
            platforms = [row[0] for row in main_cursor.fetchall()]
            pkg['platforms'] = platforms if platforms else None
            
            packages.append(pkg)
        
        main_conn.close()
        
        # Log statistics about extracted data
        packages_with_licenses = sum(1 for p in packages if p.get('license'))
        packages_with_maintainers = sum(1 for p in packages if p.get('maintainers'))
        packages_with_platforms = sum(1 for p in packages if p.get('platforms'))
        
        logger.info("Extracted %d packages from main database", len(packages))
        logger.info("  - Packages with licenses: %d", packages_with_licenses)
        logger.info("  - Packages with maintainers: %d", packages_with_maintainers)
        logger.info("  - Packages with platforms: %d", packages_with_platforms)
        
        return packages

  
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