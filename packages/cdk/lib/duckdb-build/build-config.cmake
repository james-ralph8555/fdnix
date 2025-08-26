# DuckDB Extension Configuration for fdnix Lambda Layer
# This file configures which extensions to build statically into libduckdb.so

# Core extensions for fdnix hybrid search functionality
duckdb_extension_load(fts)        # Full Text Search (BM25, etc.)
duckdb_extension_load(vss)        # Vector Similarity Search (HNSW, IVF)
duckdb_extension_load(json)       # JSON parsing and manipulation
duckdb_extension_load(parquet)    # Parquet file format support (if needed for data pipeline)

# Additional useful extensions that might be needed
duckdb_extension_load(httpfs)     # HTTP file system (for S3 if needed)

# Note: Extensions loaded here are statically compiled into libduckdb.so
# This eliminates the need for runtime extension loading in Lambda
# All functionality will be available immediately when the library is loaded