import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import lancedb
from lancedb.pydantic import LanceModel, Vector
from pydantic import BaseModel
import pyarrow as pa

try:
    import boto3  # type: ignore
except Exception:  # pragma: no cover - boto3 may be absent in local-only runs
    boto3 = None  # type: ignore


logger = logging.getLogger("fdnix.lancedb-writer")


class Package(LanceModel):
    package_id: str
    package_name: str
    version: str
    attribute_path: str
    description: str
    long_description: str
    homepage: str
    license: str
    platforms: str
    maintainers: str
    category: str
    broken: bool
    unfree: bool
    available: bool
    insecure: bool
    unsupported: bool
    main_program: str
    position: str
    outputs_to_install: str
    last_updated: str
    has_embedding: bool
    content_hash: int
    vector: Vector(256)  # 256-dimensional vector for embeddings


class Dependency(LanceModel):
    package_id: str
    pname: str 
    version: str
    attribute_path: str
    build_inputs: str  # JSON string of dependencies
    propagated_build_inputs: str  # JSON string of dependencies
    total_dependencies: int
    last_updated: str


class LanceDBWriter:
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
        self._db = None
        self._table = None

    def write_artifact(self, packages: List[Dict[str, Any]]) -> None:
        self._ensure_parent_dir()
        logger.info("Creating LanceDB at %s", self.output_path)

        # Connect to LanceDB
        self._db = lancedb.connect(str(self.output_path))
        
        # Convert packages to LanceDB format
        lance_packages = self._convert_packages_to_lance_format(packages)
        
        # Create table with schema
        try:
            # Try to open existing table
            self._table = self._db.open_table("packages")
            logger.info("Opened existing packages table")
            
            # Add new data (this will append to existing data)
            if lance_packages:
                self._table.add(lance_packages)
                logger.info("Added %d packages to existing table", len(lance_packages))
        except (FileNotFoundError, ValueError):
            # Table doesn't exist, create new one
            if lance_packages:
                self._table = self._db.create_table("packages", data=lance_packages, schema=Package)
                logger.info("Created new packages table with %d packages", len(lance_packages))
            else:
                # Create empty table with schema
                self._table = self._db.create_table("packages", schema=Package)
                logger.info("Created empty packages table")

        # Create FTS index on relevant text fields
        self._create_fts_index()

        # Create vector index for embeddings if any packages have embeddings
        if any(pkg.get("hasEmbedding", False) for pkg in packages):
            self._create_vector_index()

        logger.info("LanceDB artifact written: %s", self.output_path)

        if self.s3_bucket and self.s3_key:
            self._upload_to_s3()
    
    def write_dependency_artifact(self, dependencies: List[Dict[str, Any]]) -> None:
        """Write dependency data to a separate LanceDB table."""
        self._ensure_parent_dir()
        logger.info("Writing dependency data to LanceDB at %s", self.output_path)

        # Connect to LanceDB (reuse existing connection if available)
        if not self._db:
            self._db = lancedb.connect(str(self.output_path))
        
        # Convert dependencies to LanceDB format
        lance_dependencies = self._convert_dependencies_to_lance_format(dependencies)
        
        # Create or update dependencies table
        try:
            # Try to open existing dependencies table
            deps_table = self._db.open_table("dependencies")
            logger.info("Opened existing dependencies table")
            
            # Add new data (this will append to existing data)
            if lance_dependencies:
                deps_table.add(lance_dependencies)
                logger.info("Added %d dependencies to existing table", len(lance_dependencies))
        except (FileNotFoundError, ValueError):
            # Table doesn't exist, create new one
            if lance_dependencies:
                deps_table = self._db.create_table("dependencies", data=lance_dependencies, schema=Dependency)
                logger.info("Created new dependencies table with %d dependencies", len(lance_dependencies))
            else:
                # Create empty table with schema
                deps_table = self._db.create_table("dependencies", schema=Dependency)
                logger.info("Created empty dependencies table")

        # Create FTS index on dependency table text fields
        self._create_dependency_fts_index(deps_table)

        logger.info("Dependency LanceDB artifact written: %s", self.output_path)

    def _ensure_parent_dir(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _convert_packages_to_lance_format(self, packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert package dictionaries to LanceDB format."""
        lance_packages = []
        
        for p in packages:
            pkg_id = self._package_id(p)
            
            # Create package record with proper field mapping
            lance_pkg = {
                "package_id": pkg_id,
                "package_name": p.get("packageName") or "",
                "version": p.get("version") or "",
                "attribute_path": p.get("attributePath") or "",
                "description": p.get("description") or "",
                "long_description": p.get("longDescription") or "",
                "homepage": p.get("homepage") or "",
                "license": json.dumps(p.get("license")) if p.get("license") is not None else "",
                "platforms": json.dumps(p.get("platforms")) if p.get("platforms") is not None else "",
                "maintainers": json.dumps(p.get("maintainers")) if p.get("maintainers") is not None else "",
                "category": p.get("category") or "",
                "broken": bool(p.get("broken", False)),
                "unfree": bool(p.get("unfree", False)),
                "available": bool(p.get("available", True)),
                "insecure": bool(p.get("insecure", False)),
                "unsupported": bool(p.get("unsupported", False)),
                "main_program": p.get("mainProgram") or "",
                "position": p.get("position") or "",
                "outputs_to_install": json.dumps(p.get("outputsToInstall")) if p.get("outputsToInstall") is not None else "",
                "last_updated": p.get("lastUpdated") or "",
                "has_embedding": bool(p.get("hasEmbedding", False)),
                # Be tolerant of None/empty values
                "content_hash": int(p.get("content_hash") or 0),
                "vector": p.get("vector", [0.0] * 256)  # Default to zero vector if no embedding
            }
            
            lance_packages.append(lance_pkg)

        return lance_packages
    
    def _convert_dependencies_to_lance_format(self, dependencies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert dependency dictionaries to LanceDB format."""
        lance_dependencies = []
        
        for dep in dependencies:
            lance_dep = {
                "package_id": dep.get("packageId") or "",
                "pname": dep.get("pname") or "",
                "version": dep.get("version") or "",
                "attribute_path": dep.get("attributePath") or "",
                "build_inputs": json.dumps(dep.get("buildInputs", [])),
                "propagated_build_inputs": json.dumps(dep.get("propagatedBuildInputs", [])),
                "total_dependencies": int(dep.get("totalDependencies", 0)),
                "last_updated": dep.get("lastUpdated") or ""
            }
            lance_dependencies.append(lance_dep)
            
        return lance_dependencies

    def _create_fts_index(self) -> None:
        """Create full-text search index on relevant fields."""
        if not self._table:
            logger.warning("No table available for FTS index creation")
            return
            
        try:
            # Configure FTS with environment overrides
            stopwords = os.environ.get("FTS_STOPWORDS", "english").strip() or "english"
            stemmer = os.environ.get("FTS_STEMMER", "english").strip()
            
            # Get table schema to check which fields exist
            schema = self._table.schema
            available_fields = [field.name for field in schema]
            logger.debug("Available fields in table: %s", available_fields)
            
            # Create FTS index on text fields that actually exist - prioritized for search relevance
            potential_fts_fields = [
                "package_name",      # Most important for name searches
                "description",       # Key for content searches
                "long_description",  # Extended content
                "attribute_path",    # Important for Nix attribute searches
                "main_program",      # Executable names
                "package_id"         # Full identifiers
            ]
            fts_fields = [field for field in potential_fts_fields if field in available_fields]
            
            if not fts_fields:
                logger.warning("No suitable text fields found for FTS index")
                logger.warning("Available fields: %s", available_fields)
                logger.warning("Attempted fields: %s", potential_fts_fields)
                return
            
            logger.info("Creating FTS index on fields: %s (stopwords=%s, stemmer=%s)", 
                       fts_fields, stopwords, stemmer or "<none>")
            
            # LanceDB's native FTS index creation (Lance-based, not tantivy)
            # Index all relevant fields for comprehensive hybrid search
            self._table.create_fts_index(fts_fields, use_tantivy=False)
            
            logger.info("FTS index created successfully")
        except Exception as e:
            logger.error("Failed to create FTS index: %s", e)
            # Don't raise - FTS is optional
    
    def _create_dependency_fts_index(self, deps_table) -> None:
        """Create full-text search index on dependency table fields."""
        if not deps_table:
            logger.warning("No dependency table available for FTS index creation")
            return
            
        try:
            # Get table schema to check which fields exist
            schema = deps_table.schema
            available_fields = [field.name for field in schema]
            logger.debug("Available fields in dependency table: %s", available_fields)
            
            # Create FTS index on text fields for dependency searching
            potential_fts_fields = [
                "package_id",        # Package identifiers
                "pname",            # Package names
                "attribute_path"    # Attribute paths
            ]
            fts_fields = [field for field in potential_fts_fields if field in available_fields]
            
            if not fts_fields:
                logger.warning("No suitable text fields found for dependency FTS index")
                return
            
            logger.info("Creating dependency FTS index on fields: %s", fts_fields)
            
            # LanceDB's native FTS index creation
            deps_table.create_fts_index(fts_fields, use_tantivy=False)
            
            logger.info("Dependency FTS index created successfully")
        except Exception as e:
            logger.error("Failed to create dependency FTS index: %s", e)
            # Don't raise - FTS is optional

    def _create_vector_index(self) -> None:
        """Create vector index for embeddings."""
        if not self._table:
            logger.warning("No table available for vector index creation")
            return
            
        try:
            # Configure vector index parameters
            num_partitions = int(os.environ.get("VECTOR_INDEX_PARTITIONS", "256"))
            num_sub_vectors = int(os.environ.get("VECTOR_INDEX_SUB_VECTORS", "8"))
            
            logger.info("Creating vector index (partitions=%d, sub_vectors=%d)", 
                       num_partitions, num_sub_vectors)
            
            # Create IVF-PQ index on vector column with cosine distance for semantic search
            self._table.create_index(
                "vector",
                index_type="IVF_PQ",
                num_partitions=num_partitions,
                num_sub_vectors=num_sub_vectors,
                distance_type="cosine"
            )
            
            logger.info("Vector index created successfully")
        except Exception as e:
            logger.error("Failed to create vector index: %s", e)
            # Try without distance_type parameter in case of version compatibility issues
            try:
                logger.info("Retrying index creation without distance_type parameter...")
                self._table.create_index(
                    "vector",
                    index_type="IVF_PQ",
                    num_partitions=num_partitions,
                    num_sub_vectors=num_sub_vectors
                )
                logger.info("Vector index created successfully (without distance_type)")
            except Exception as e2:
                logger.error("Failed to create vector index on retry: %s", e2)
                # Don't raise - vector index is optional for functionality

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
            "Uploading LanceDB dataset to s3://%s/%s (region=%s)",
            self.s3_bucket,
            self.s3_key,
            self.region,
        )
        
        # Clear existing objects if requested
        if self.clear_before_upload:
            logger.info("Clearing existing objects before upload...")
            self._delete_s3_objects(self.s3_bucket, self.s3_key)
        
        # Upload the entire LanceDB directory
        s3 = boto3.client("s3", region_name=self.region)
        
        # LanceDB creates a directory structure, upload all files
        for file_path in self.output_path.rglob("*"):
            if file_path.is_file():
                # Calculate relative path for S3 key
                relative_path = file_path.relative_to(self.output_path)
                s3_file_key = f"{self.s3_key}/{relative_path}".replace("\\", "/")
                
                logger.debug("Uploading %s to %s", file_path, s3_file_key)
                s3.upload_file(str(file_path), self.s3_bucket, s3_file_key)
        
        logger.info("Upload complete.")

    def create_minified_db_from_main(self, main_db_path: str) -> None:
        """Create a minified database by copying essential data from main database.
        
        For LanceDB, this mainly involves copying the table with a subset of columns
        and ensuring proper indexing.
        """
        self._ensure_parent_dir()
        logger.info("Creating minified LanceDB at %s from main DB at %s", self.output_path, main_db_path)

        # Connect to main database
        main_db = lancedb.connect(main_db_path)
        main_table = main_db.open_table("packages")
        
        # Connect to minified database
        self._db = lancedb.connect(str(self.output_path))
        
        # Read all data from main table
        data = main_table.to_pandas()
        
        # Select essential columns for minified version
        essential_columns = [
            "package_id", "package_name", "version", "attribute_path", "description", 
            "homepage", "license", "maintainers", "broken", "unfree", "available", 
            "insecure", "unsupported", "main_program", "has_embedding", "content_hash", "vector"
        ]
        
        # Filter to essential columns (keep only what exists)
        available_columns = [col for col in essential_columns if col in data.columns]
        minified_data = data[available_columns]
        
        # Create minified table
        if not minified_data.empty:
            self._table = self._db.create_table("packages", data=minified_data.to_dict("records"))
            logger.info("Created minified table with %d packages", len(minified_data))
        else:
            # Create empty table with schema
            self._table = self._db.create_table("packages", schema=Package)
            logger.info("Created empty minified table")

        # Create indexes on minified table
        self._create_fts_index()
        
        if any(minified_data.get("has_embedding", False)) if not minified_data.empty else False:
            self._create_vector_index()

        # Also copy dependencies table if it exists
        try:
            main_deps_table = main_db.open_table("dependencies")
            deps_data = main_deps_table.to_pandas()
            
            if not deps_data.empty:
                minified_deps_table = self._db.create_table("dependencies", data=deps_data.to_dict("records"))
                self._create_dependency_fts_index(minified_deps_table)
                logger.info("Copied dependencies table with %d entries", len(deps_data))
        except (FileNotFoundError, ValueError):
            logger.info("No dependencies table found in main database")

        logger.info("Minified LanceDB artifact written: %s", self.output_path)

        if self.s3_bucket and self.s3_key:
            self._upload_to_s3()


class DependencyS3Writer:
    """Separate writer for comprehensive dependency JSON output to S3."""
    
    def __init__(
        self,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        self.s3_bucket = s3_bucket
        self.s3_key = s3_key
        self.region = region

    def write_dependency_json(self, dependencies: List[Dict[str, Any]], metadata: Dict[str, Any] = None) -> None:
        """Write comprehensive dependency data as JSON to S3."""
        if not (self.s3_bucket and self.s3_key and self.region):
            logger.info("Dependency S3 upload not configured; skipping comprehensive dependency output.")
            return
            
        if boto3 is None:
            logger.error("boto3 not available for dependency S3 upload")
            return
        
        # Create comprehensive dependency output
        output_data = {
            "metadata": metadata or {},
            "dependencies": dependencies,
            "stats": self._calculate_dependency_stats(dependencies)
        }
        
        # Convert to JSON
        json_data = json.dumps(output_data, indent=2, sort_keys=True)
        
        logger.info("Uploading comprehensive dependency data to s3://%s/%s", self.s3_bucket, self.s3_key)
        
        # Upload to S3
        s3 = boto3.client("s3", region_name=self.region)
        s3.put_object(
            Bucket=self.s3_bucket,
            Key=self.s3_key,
            Body=json_data.encode('utf-8'),
            ContentType='application/json'
        )
        
        logger.info("Dependency JSON upload complete.")
    
    def _calculate_dependency_stats(self, dependencies: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate statistics about the dependency data."""
        if not dependencies:
            return {}
        
        total_packages = len(dependencies)
        total_dep_relations = sum(dep.get("totalDependencies", 0) for dep in dependencies)
        
        # Count packages with no dependencies
        no_deps = sum(1 for dep in dependencies if dep.get("totalDependencies", 0) == 0)
        
        # Calculate averages
        avg_deps = total_dep_relations / total_packages if total_packages > 0 else 0
        max_deps = max((dep.get("totalDependencies", 0) for dep in dependencies), default=0)
        
        return {
            "totalPackages": total_packages,
            "totalDependencyRelations": total_dep_relations,
            "packagesWithNoDependencies": no_deps,
            "averageDependenciesPerPackage": round(avg_deps, 2),
            "maxDependenciesPerPackage": max_deps
        }
