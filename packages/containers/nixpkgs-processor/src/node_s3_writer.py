import json
import logging
import brotli
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

try:
    import boto3  # type: ignore
    from botocore.exceptions import ClientError  # type: ignore
except Exception:  # pragma: no cover
    boto3 = None  # type: ignore
    ClientError = Exception  # type: ignore

logger = logging.getLogger("fdnix.node-s3-writer")


class NodeS3Writer:
    """Write individual package nodes as JSON files to S3 for dependency viewer."""
    
    def __init__(
        self,
        s3_bucket: str,
        s3_prefix: str = "nodes/",
        region: str = "us-east-1",
        clear_existing: bool = True,
        batch_size: int = 50,
        max_workers: int = 30,
        compression_level: int = 6
    ) -> None:
        self.s3_bucket = s3_bucket
        self.s3_prefix = s3_prefix.rstrip('/') + '/'  # Ensure trailing slash
        self.region = region
        self.clear_existing = clear_existing
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.compression_level = compression_level
        self._s3_client = None
        self._upload_stats = {
            'success': 0,
            'errors': 0,
            'total': 0
        }
        self._stats_lock = threading.Lock()
        
    def _get_s3_client(self):
        """Get or create S3 client (thread-safe)."""
        if not self._s3_client:
            if boto3 is None:
                raise RuntimeError("boto3 not available for S3 upload")
            self._s3_client = boto3.client("s3", region_name=self.region)
        return self._s3_client
    
    def write_nodes(
        self, 
        packages: List[Dict[str, Any]], 
        dependency_data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Write individual package nodes to S3 with dependency information."""
        if not packages:
            logger.warning("No packages provided for node writing")
            return
            
        logger.info("Writing %d individual package nodes to S3...", len(packages))
        logger.info("S3 destination: s3://%s/%s", self.s3_bucket, self.s3_prefix)
        
        # Clear existing nodes if requested
        if self.clear_existing:
            self._clear_existing_nodes()
        
        # Prepare node data
        nodes_to_write = self._prepare_node_data(packages, dependency_data, metadata)
        
        if not nodes_to_write:
            logger.warning("No valid nodes to write after preparation")
            return
        
        # Write nodes in batches using thread pool
        self._write_nodes_batch(nodes_to_write)
        
        # Log final statistics
        with self._stats_lock:
            logger.info("Node writing completed: %d successful, %d errors, %d total", 
                       self._upload_stats['success'], 
                       self._upload_stats['errors'],
                       self._upload_stats['total'])
    
    def _prepare_node_data(
        self, 
        packages: List[Dict[str, Any]], 
        dependency_data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Prepare node data by combining package metadata with dependency information."""
        logger.info("Preparing node data with dependency information...")
        
        nodes = []
        processed_count = 0
        
        for pkg in packages:
            try:
                # Create node identifier
                package_name = pkg.get("packageName", "")
                version = pkg.get("version", "")
                node_id = f"{package_name}-{version}"
                
                if not package_name or not version:
                    logger.debug("Skipping package with missing name or version: %s", pkg)
                    continue
                
                # Get dependency information for this node
                dep_info = dependency_data.get(node_id, {})
                
                # Create comprehensive node data
                node_data = {
                    # Core package metadata (all LanceDB fields)
                    "nodeId": node_id,
                    "packageName": package_name,
                    "version": version,
                    "attributePath": pkg.get("attributePath", ""),
                    "description": pkg.get("description", ""),
                    "longDescription": pkg.get("longDescription", ""),
                    "homepage": pkg.get("homepage", ""),
                    "license": pkg.get("license"),
                    "platforms": pkg.get("platforms"),
                    "maintainers": pkg.get("maintainers"),
                    "category": pkg.get("category", ""),
                    "broken": pkg.get("broken", False),
                    "unfree": pkg.get("unfree", False),
                    "available": pkg.get("available", True),
                    "insecure": pkg.get("insecure", False),
                    "unsupported": pkg.get("unsupported", False),
                    "mainProgram": pkg.get("mainProgram", ""),
                    "position": pkg.get("position", ""),
                    "outputsToInstall": pkg.get("outputsToInstall", []),
                    "lastUpdated": pkg.get("lastUpdated", ""),
                    
                    # Dependency information (for dependency viewer)
                    "dependencies": {
                        "direct": dep_info.get("direct_dependencies", []),
                        "all": dep_info.get("all_dependencies", []),
                        "count": dep_info.get("dependency_count", 0),
                        "totalCount": dep_info.get("total_dependency_count", 0)
                    },
                    "dependents": {
                        "direct": dep_info.get("direct_dependents", []),
                        "all": dep_info.get("all_dependents", []),
                        "count": dep_info.get("dependent_count", 0),
                        "totalCount": dep_info.get("total_dependent_count", 0)
                    },
                    
                    # Node metadata
                    "nodeMetadata": {
                        "generatedAt": metadata.get("extraction_timestamp") if metadata else None,
                        "nixpkgsBranch": metadata.get("nixpkgs_branch") if metadata else None,
                        "hasDependencies": dep_info.get("dependency_count", 0) > 0,
                        "hasDependents": dep_info.get("dependent_count", 0) > 0
                    }
                }
                
                nodes.append(node_data)
                processed_count += 1
                
                if processed_count % 1000 == 0:
                    logger.info("Prepared %d nodes...", processed_count)
                    
            except Exception as e:
                logger.warning("Error preparing node data for %s: %s", 
                             pkg.get("packageName", "unknown"), e)
                continue
        
        logger.info("Prepared %d nodes for S3 upload", len(nodes))
        return nodes
    
    def _write_nodes_batch(self, nodes: List[Dict[str, Any]]) -> None:
        """Write nodes to S3 in parallel batches."""
        with self._stats_lock:
            self._upload_stats['total'] = len(nodes)
        
        # Split nodes into batches
        batches = [nodes[i:i + self.batch_size] for i in range(0, len(nodes), self.batch_size)]
        
        logger.info("Writing nodes in %d batches of %d (max %d workers)", 
                   len(batches), self.batch_size, self.max_workers)
        
        # Process batches in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_batch = {
                executor.submit(self._write_batch, batch_idx, batch): batch_idx 
                for batch_idx, batch in enumerate(batches)
            }
            
            for future in as_completed(future_to_batch):
                batch_idx = future_to_batch[future]
                try:
                    success_count, error_count = future.result()
                    with self._stats_lock:
                        self._upload_stats['success'] += success_count
                        self._upload_stats['errors'] += error_count
                    
                    logger.debug("Batch %d completed: %d success, %d errors", 
                               batch_idx, success_count, error_count)
                except Exception as e:
                    logger.error("Batch %d failed: %s", batch_idx, e)
                    with self._stats_lock:
                        self._upload_stats['errors'] += len(batches[batch_idx])
    
    def _write_batch(self, batch_idx: int, batch: List[Dict[str, Any]]) -> tuple:
        """Write a batch of nodes to S3."""
        success_count = 0
        error_count = 0
        s3_client = self._get_s3_client()
        
        for node in batch:
            try:
                node_id = node.get("nodeId", "unknown")
                
                # Create S3 key for this node with .br extension for brotli compression
                s3_key = f"{self.s3_prefix}{node_id}.json.br"
                
                # Convert to compact JSON and compress with brotli
                json_data = json.dumps(node, separators=(',', ':'), sort_keys=True)
                compressed_data = brotli.compress(
                    json_data.encode('utf-8'),
                    quality=self.compression_level
                )
                
                # Upload to S3
                s3_client.put_object(
                    Bucket=self.s3_bucket,
                    Key=s3_key,
                    Body=compressed_data,
                    ContentType='application/json',
                    ContentEncoding='br',
                    Metadata={
                        'package-name': node.get("packageName", ""),
                        'version': node.get("version", ""),
                        'category': node.get("category", ""),
                        'generated-by': 'fdnix-nixpkgs-processor',
                        'compression': 'brotli',
                        'compression-quality': str(self.compression_level)
                    }
                )
                
                success_count += 1
                
            except Exception as e:
                logger.warning("Error writing node %s: %s", node.get("nodeId", "unknown"), e)
                error_count += 1
        
        return success_count, error_count
    
    def _clear_existing_nodes(self) -> None:
        """Clear existing node files from S3 prefix."""
        if not self.clear_existing:
            return
            
        logger.info("Clearing existing nodes from s3://%s/%s", self.s3_bucket, self.s3_prefix)
        
        try:
            s3_client = self._get_s3_client()
            
            # List all objects with the prefix
            paginator = s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(
                Bucket=self.s3_bucket,
                Prefix=self.s3_prefix
            )
            
            deleted_count = 0
            objects_to_delete = []
            
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects_to_delete.append({'Key': obj['Key']})
                        
                        # Delete in batches of 1000 (S3 limit)
                        if len(objects_to_delete) >= 1000:
                            s3_client.delete_objects(
                                Bucket=self.s3_bucket,
                                Delete={'Objects': objects_to_delete}
                            )
                            deleted_count += len(objects_to_delete)
                            objects_to_delete = []
            
            # Delete remaining objects
            if objects_to_delete:
                s3_client.delete_objects(
                    Bucket=self.s3_bucket,
                    Delete={'Objects': objects_to_delete}
                )
                deleted_count += len(objects_to_delete)
            
            if deleted_count > 0:
                logger.info("Deleted %d existing node files", deleted_count)
            else:
                logger.info("No existing node files found to delete")
                
        except Exception as e:
            logger.warning("Error clearing existing nodes: %s", e)
    
    def get_upload_stats(self) -> Dict[str, int]:
        """Get upload statistics."""
        with self._stats_lock:
            return self._upload_stats.copy()
    
    def create_index_file(
        self, 
        packages: List[Dict[str, Any]], 
        dependency_stats: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Create an index file with summary information about all nodes."""
        logger.info("Creating node index file...")
        
        try:
            # Create index data
            index_data = {
                "metadata": {
                    "generatedAt": metadata.get("extraction_timestamp") if metadata else None,
                    "nixpkgsBranch": metadata.get("nixpkgs_branch") if metadata else None,
                    "totalPackages": len(packages),
                    "s3Bucket": self.s3_bucket,
                    "s3Prefix": self.s3_prefix,
                    "generatedBy": "fdnix-nixpkgs-processor"
                },
                "dependencyStats": dependency_stats,
                "packages": [
                    {
                        "nodeId": f"{pkg.get('packageName', '')}-{pkg.get('version', '')}",
                        "packageName": pkg.get("packageName", ""),
                        "version": pkg.get("version", ""),
                        "attributePath": pkg.get("attributePath", ""),
                        "description": pkg.get("description", "")[:200],  # Truncate for index
                        "category": pkg.get("category", ""),
                        "broken": pkg.get("broken", False),
                        "unfree": pkg.get("unfree", False)
                    }
                    for pkg in packages
                ]
            }
            
            # Upload index file with brotli compression
            s3_client = self._get_s3_client()
            index_key = f"{self.s3_prefix}index.json.br"
            json_data = json.dumps(index_data, separators=(',', ':'), sort_keys=True)
            compressed_data = brotli.compress(
                json_data.encode('utf-8'),
                quality=self.compression_level
            )
            
            s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=index_key,
                Body=compressed_data,
                ContentType='application/json',
                ContentEncoding='br',
                Metadata={
                    'type': 'node-index',
                    'total-packages': str(len(packages)),
                    'generated-by': 'fdnix-nixpkgs-processor',
                    'compression': 'brotli',
                    'compression-quality': str(self.compression_level)
                }
            )
            
            logger.info("Node index file created at s3://%s/%s", self.s3_bucket, index_key)
            
        except Exception as e:
            logger.error("Error creating node index file: %s", e)
