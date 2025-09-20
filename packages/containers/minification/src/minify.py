#!/usr/bin/env python3
"""
fdnix Package Minification Pipeline

This script implements the offline minification process using Python 3.14's
built-in compression.zstd module to create compressed SQLite databases
with FTS5 search capability.

Output:
- minified.db: Compressed SQLite database 
- shared.dict: Zstandard compression dictionary
- minification_stats.json: Compression statistics
"""

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from random import sample
from typing import Any, Dict, List, Optional, Tuple

try:
    import compression.zstd as zstd
except ImportError:
    print("ERROR: Python 3.14+ required for compression.zstd module")
    sys.exit(1)

from nixpkgs_extractor import NixpkgsExtractor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("fdnix.minify")


class PackageMinifier:
    def __init__(self, 
                 db_path: str = "minified.db", 
                 dict_path: str = "shared.dict",
                 sample_size: int = 10000,
                 dict_capacity: int = 110592):  # 108KB dictionary
        self.db_path = Path(db_path)
        self.dict_path = Path(dict_path)
        self.sample_size = sample_size
        self.dict_capacity = dict_capacity
        self.compressor = None
        self.decompressor = None
        self.stats = {
            "total_packages": 0,
            "compressed_size": 0,
            "uncompressed_size": 0,
            "compression_ratio": 0.0,
            "dict_size": 0,
            "processing_time": 0.0
        }

    def minify_packages(self) -> bool:
        """Main minification pipeline"""
        start_time = time.time()
        logger.info("Starting package minification pipeline")
        
        try:
            # Extract all packages
            extractor = NixpkgsExtractor()
            all_packages = extractor.extract_all_packages()
            self.stats["total_packages"] = len(all_packages)
            logger.info(f"Extracted {len(all_packages)} packages")
            
            # Process the packages
            if not self.process_packages(all_packages):
                return False
            
            # Calculate final statistics
            self.stats["processing_time"] = time.time() - start_time
            
            # Save statistics
            self._save_stats()
            
            logger.info("Minification completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Minification failed: {e}")
            return False

    def process_packages(self, packages: List[Dict[str, Any]]) -> bool:
        """Process a list of packages directly (for testing or custom data sources)"""
        start_time = time.time()
        logger.info(f"Processing {len(packages)} packages...")
        
        try:
            # Update package count
            self.stats["total_packages"] = len(packages)
            
            # Train compression dictionary
            if not self._train_dictionary(packages):
                return False
            
            # Initialize compressor with dictionary
            self._initialize_compressor()
            
            # Create SQLite database
            if not self._create_database(packages):
                return False
            
            # Calculate statistics
            self._calculate_stats()
            
            logger.info("Package processing completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Package processing failed: {e}")
            return False

    def _train_dictionary(self, packages: List[Dict[str, Any]]) -> bool:
        """Train Zstandard compression dictionary from package samples"""
        logger.info("Training compression dictionary...")
        
        # Sample packages for dictionary training
        sample_packages = sample(packages, min(self.sample_size, len(packages)))
        
        # Prepare training samples
        samples = []
        for pkg in sample_packages:
            pkg_json = json.dumps(pkg, separators=(',', ':'), ensure_ascii=False)
            samples.append(pkg_json.encode('utf-8'))
        
        try:
            # Train dictionary
            dictionary = zstd.train_dictionary(self.dict_capacity, samples)
            
            # Save dictionary to file
            with open(self.dict_path, 'wb') as f:
                f.write(dictionary)
            
            self.stats["dict_size"] = len(dictionary)
            logger.info(f"Dictionary trained and saved: {len(dictionary)} bytes")
            return True
            
        except Exception as e:
            logger.error(f"Dictionary training failed: {e}")
            return False

    def _initialize_compressor(self) -> None:
        """Initialize Zstandard compressor with trained dictionary"""
        # Load dictionary
        with open(self.dict_path, 'rb') as f:
            dictionary = f.read()
        
        # Initialize compressor and decompressor
        self.compressor = zstd.ZstdCompressor(dict_data=dictionary)
        self.decompressor = zstd.ZstdDecompressor(dict_data=dictionary)
        
        logger.info("Compressor initialized with trained dictionary")

    def _create_database(self, packages: List[Dict[str, Any]]) -> bool:
        """Create SQLite database with compressed package data and FTS5 search"""
        logger.info("Creating SQLite database...")
        
        try:
            # Remove existing database
            if self.db_path.exists():
                self.db_path.unlink()
            
            # Create database connection
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA journal_mode=WAL")  # Better write performance
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=10000")   # Increase cache size
            
            # Create schema
            self._create_schema(conn)
            
            # Populate database
            self._populate_database(conn, packages)
            
            # Optimize database
            conn.execute("VACUUM")
            conn.execute("ANALYZE")
            
            conn.close()
            
            logger.info(f"Database created: {self.db_path.stat().st_size} bytes")
            return True
            
        except Exception as e:
            logger.error(f"Database creation failed: {e}")
            return False

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Create SQLite schema with compressed data and FTS5 tables"""
        # Key-value table for compressed package data
        conn.execute("""
            CREATE TABLE packages_kv (
                id TEXT PRIMARY KEY,
                data BLOB NOT NULL
            )
        """)
        
        # FTS5 virtual table for search
        conn.execute("""
            CREATE VIRTUAL TABLE packages_fts USING fts5(
                id,
                name,
                description,
                content='packages_kv',
                content_rowid='id',
                tokenize='unicode61'
            )
        """)
        
        # Create indexes for better performance
        conn.execute("CREATE INDEX idx_packages_fts_id ON packages_fts(id)")
        
        logger.info("Database schema created")

    def _populate_database(self, conn: sqlite3.Connection, packages: List[Dict[str, Any]]) -> None:
        """Populate database with compressed package data"""
        logger.info("Populating database with compressed packages...")
        
        conn.execute("BEGIN TRANSACTION")
        
        try:
            for i, pkg in enumerate(packages):
                # Generate package ID
                pkg_id = f"{pkg['packageName']}-{pkg['version']}"
                
                # Prepare package data for compression
                pkg_data = {
                    "packageId": pkg_id,
                    "packageName": pkg["packageName"],
                    "version": pkg["version"],
                    "description": pkg["description"],
                    "homepage": pkg["homepage"],
                    "license": pkg["license"],
                    "attributePath": pkg["attributePath"],
                    "category": pkg.get("category", ""),
                    "broken": pkg["broken"],
                    "unfree": pkg["unfree"],
                    "available": pkg["available"],
                    "maintainers": pkg["maintainers"],
                    "platforms": pkg["platforms"],
                    "longDescription": pkg["longDescription"],
                    "mainProgram": pkg["mainProgram"],
                    "position": pkg["position"],
                    "outputsToInstall": pkg["outputsToInstall"],
                    "lastUpdated": pkg["lastUpdated"]
                }
                
                # Convert to JSON and compress
                pkg_json = json.dumps(pkg_data, separators=(',', ':'), ensure_ascii=False)
                uncompressed_size = len(pkg_json.encode('utf-8'))
                self.stats["uncompressed_size"] += uncompressed_size
                
                compressed_data = self.compressor.compress(pkg_json.encode('utf-8'))
                self.stats["compressed_size"] += len(compressed_data)
                
                # Insert compressed data into key-value table
                conn.execute(
                    "INSERT INTO packages_kv (id, data) VALUES (?, ?)",
                    (pkg_id, compressed_data)
                )
                
                # Insert searchable fields into FTS table
                conn.execute(
                    "INSERT INTO packages_fts (id, name, description) VALUES (?, ?, ?)",
                    (pkg_id, pkg_data["packageName"], pkg_data["description"])
                )
                
                if (i + 1) % 1000 == 0:
                    logger.info(f"Processed {i + 1}/{len(packages)} packages")
                    conn.execute("COMMIT")
                    conn.execute("BEGIN TRANSACTION")
            
            conn.execute("COMMIT")
            logger.info("Database population completed")
            
        except Exception as e:
            conn.execute("ROLLBACK")
            raise e

    def _calculate_stats(self) -> None:
        """Calculate compression statistics"""
        if self.stats["uncompressed_size"] > 0:
            self.stats["compression_ratio"] = (
                1.0 - (self.stats["compressed_size"] / self.stats["uncompressed_size"])
            ) * 100

    def _save_stats(self) -> None:
        """Save minification statistics to JSON file"""
        stats_file = Path("minification_stats.json")
        
        stats_output = {
            "timestamp": datetime.now().isoformat(),
            "database_path": str(self.db_path),
            "dictionary_path": str(self.dict_path),
            **self.stats
        }
        
        with open(stats_file, 'w') as f:
            json.dump(stats_output, f, indent=2)
        
        logger.info(f"Statistics saved to {stats_file}")

    def verify_database(self) -> bool:
        """Verify the created database and compression dictionary"""
        logger.info("Verifying minification output...")
        
        try:
            # Check files exist
            if not self.db_path.exists():
                logger.error("Database file not found")
                return False
            
            if not self.dict_path.exists():
                logger.error("Dictionary file not found")
                return False
            
            # Test database connection
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            # Check tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            
            if 'packages_kv' not in tables or 'packages_fts' not in tables:
                logger.error("Required tables not found in database")
                return False
            
            # Test data compression/decompression
            cursor.execute("SELECT id, data FROM packages_kv LIMIT 1")
            row = cursor.fetchone()
            
            if row:
                pkg_id, compressed_data = row
                try:
                    decompressed = self.decompressor.decompress(compressed_data)
                    pkg_data = json.loads(decompressed.decode('utf-8'))
                    logger.info(f"Successfully tested compression/decompression for package: {pkg_data['packageName']}")
                except Exception as e:
                    logger.error(f"Compression/decompression test failed: {e}")
                    return False
            
            # Test FTS search
            cursor.execute("SELECT COUNT(*) FROM packages_fts")
            fts_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM packages_kv")
            kv_count = cursor.fetchone()[0]
            
            if fts_count != kv_count:
                logger.error(f"FTS count ({fts_count}) doesn't match KV count ({kv_count})")
                return False
            
            conn.close()
            
            logger.info("Database verification completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Database verification failed: {e}")
            return False

    def generate_statistics(self) -> Dict[str, Any]:
        """Generate and return current minification statistics"""
        # Calculate final statistics if needed
        self._calculate_stats()
        
        # Return statistics dict with all available metrics
        return {
            "timestamp": datetime.now().isoformat(),
            "database_path": str(self.db_path),
            "dictionary_path": str(self.dict_path),
            **self.stats
        }


def main():
    """Main entry point"""
    minifier = PackageMinifier()
    
    if minifier.minify_packages():
        if minifier.verify_database():
            logger.info("Minification pipeline completed successfully!")
            
            # Print summary statistics
            stats = minifier.stats
            print(f"\n=== Minification Summary ===")
            print(f"Total packages: {stats['total_packages']:,}")
            print(f"Uncompressed size: {stats['uncompressed_size']:,} bytes")
            print(f"Compressed size: {stats['compressed_size']:,} bytes")
            print(f"Compression ratio: {stats['compression_ratio']:.1f}%")
            print(f"Dictionary size: {stats['dict_size']:,} bytes")
            print(f"Processing time: {stats['processing_time']:.1f} seconds")
            print(f"Database file: {minifier.db_path}")
            print(f"Dictionary file: {minifier.dict_path}")
            
            return 0
        else:
            logger.error("Database verification failed")
            return 1
    else:
        logger.error("Minification failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())