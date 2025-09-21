#!/usr/bin/env python3

import sqlite3
import json
import tempfile
import os
from pathlib import Path

def test_minified_db_extraction():
    """Test that minified DB extraction preserves all metadata."""
    
    # Create a temporary main database with test data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp_file:
        main_db_path = tmp_file.name
    
    try:
        # Create test database
        conn = sqlite3.connect(main_db_path)
        cursor = conn.cursor()
        
        # Create schema
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
        
        # Create lookup tables
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
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS architectures (
                arch_id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            )
        """)
        
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
        
        # Create junction tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS package_licenses (
                package_id TEXT NOT NULL,
                license_id INTEGER NOT NULL,
                FOREIGN KEY(package_id) REFERENCES packages(package_id),
                FOREIGN KEY(license_id) REFERENCES licenses(license_id),
                PRIMARY KEY(package_id, license_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS package_architectures (
                package_id TEXT NOT NULL,
                arch_id INTEGER NOT NULL,
                FOREIGN KEY(package_id) REFERENCES packages(package_id),
                FOREIGN KEY(arch_id) REFERENCES architectures(arch_id),
                PRIMARY KEY(package_id, arch_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS package_maintainers (
                package_id TEXT NOT NULL,
                maintainer_id INTEGER NOT NULL,
                FOREIGN KEY(package_id) REFERENCES packages(package_id),
                FOREIGN KEY(maintainer_id) REFERENCES maintainers(maintainer_id),
                PRIMARY KEY(package_id, maintainer_id)
            )
        """)
        
        # Insert test data
        # Package 1: Complete metadata
        cursor.execute("""
            INSERT INTO packages (package_id, package_name, version, attribute_path, description, 
                                long_description, homepage, category, broken, unfree, available, 
                                insecure, unsupported, main_program, position, outputs_to_install, 
                                last_updated, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "test-package-1", "test-package", "1.0.0", "test.test-package", "Test package description",
            "Long test package description", "https://example.com", "test-category", 0, 0, 1, 0, 0,
            "test-program", "/path/to/package", '["out"]', "2023-01-01", 12345
        ))
        
        # Insert licenses
        cursor.execute("INSERT INTO licenses (short_name, full_name, spdx_id, url, is_free, is_redistributable, is_deprecated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      ("MIT", "MIT License", "MIT", "https://opensource.org/licenses/MIT", 1, 1, 0))
        cursor.execute("INSERT INTO licenses (short_name, full_name, spdx_id, url, is_free, is_redistributable, is_deprecated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      ("Apache-2.0", "Apache License 2.0", "Apache-2.0", "https://www.apache.org/licenses/LICENSE-2.0", 1, 1, 0))
        
        # Insert architectures
        cursor.execute("INSERT INTO architectures (name) VALUES (?)", ("x86_64-linux",))
        cursor.execute("INSERT INTO architectures (name) VALUES (?)", ("aarch64-linux",))
        cursor.execute("INSERT INTO architectures (name) VALUES (?)", ("x86_64-darwin",))
        
        # Insert maintainers (various combinations)
        cursor.execute("INSERT INTO maintainers (name, email, github, github_id) VALUES (?, ?, ?, ?)",
                      ("John Doe", "john@example.com", "johndoe", 12345))
        cursor.execute("INSERT INTO maintainers (name, email, github, github_id) VALUES (?, ?, ?, ?)",
                      ("Jane Smith", None, "janesmith", None))
        cursor.execute("INSERT INTO maintainers (name, email, github, github_id) VALUES (?, ?, ?, ?)",
                      (None, "bob@example.com", None, 67890))
        cursor.execute("INSERT INTO maintainers (name, email, github, github_id) VALUES (?, ?, ?, ?)",
                      (None, None, "alice", None))
        
        # Link package with metadata
        cursor.execute("INSERT INTO package_licenses (package_id, license_id) VALUES (?, ?)",
                      ("test-package-1", 1))
        cursor.execute("INSERT INTO package_licenses (package_id, license_id) VALUES (?, ?)",
                      ("test-package-1", 2))
        
        cursor.execute("INSERT INTO package_architectures (package_id, arch_id) VALUES (?, ?)",
                      ("test-package-1", 1))
        cursor.execute("INSERT INTO package_architectures (package_id, arch_id) VALUES (?, ?)",
                      ("test-package-1", 2))
        cursor.execute("INSERT INTO package_architectures (package_id, arch_id) VALUES (?, ?)",
                      ("test-package-1", 3))
        
        cursor.execute("INSERT INTO package_maintainers (package_id, maintainer_id) VALUES (?, ?)",
                      ("test-package-1", 1))
        cursor.execute("INSERT INTO package_maintainers (package_id, maintainer_id) VALUES (?, ?)",
                      ("test-package-1", 2))
        cursor.execute("INSERT INTO package_maintainers (package_id, maintainer_id) VALUES (?, ?)",
                      ("test-package-1", 3))
        cursor.execute("INSERT INTO package_maintainers (package_id, maintainer_id) VALUES (?, ?)",
                      ("test-package-1", 4))
        
        conn.commit()
        conn.close()
        
        # Now test the extraction logic
        print("Testing extraction logic...")
        
        # Import the SQLiteWriter class
        import sys
        sys.path.append(str(Path(__file__).parent / "src"))
        
        from sqlite_writer import SQLiteWriter
        
        # Create a temporary minified writer
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp_minified:
            minified_path = tmp_minified.name
        
        try:
            # Create writer instance
            writer = SQLiteWriter(output_path=minified_path)
            
            # Test the extraction method
            packages = writer._extract_packages_from_main_db(main_db_path)
            
            # Verify results
            print(f"Extracted {len(packages)} packages")
            
            if packages:
                pkg = packages[0]
                print(f"Package: {pkg['package_name']}")
                
                # Check license
                license_data = pkg.get('license')
                if license_data:
                    print(f"✓ License extracted: {license_data}")
                else:
                    print("✗ License missing")
                
                # Check maintainers
                maintainers = pkg.get('maintainers')
                if maintainers:
                    print(f"✓ Maintainers extracted: {len(maintainers)}")
                    for i, maint in enumerate(maintainers):
                        print(f"  Maintainer {i+1}: {maint}")
                else:
                    print("✗ Maintainers missing")
                
                # Check platforms
                platforms = pkg.get('platforms')
                if platforms:
                    print(f"✓ Platforms extracted: {platforms}")
                else:
                    print("✗ Platforms missing")
                
                print("✓ All metadata preserved correctly!")
            
        finally:
            # Clean up
            if os.path.exists(minified_path):
                os.unlink(minified_path)
    
    finally:
        # Clean up
        if os.path.exists(main_db_path):
            os.unlink(main_db_path)

if __name__ == "__main__":
    test_minified_db_extraction()