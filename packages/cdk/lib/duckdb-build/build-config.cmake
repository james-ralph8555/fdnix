# DuckDB Extension Configuration for fdnix Lambda Layer
# This file configures which extensions to build statically into libduckdb.so

# Core extensions for fdnix hybrid search functionality
duckdb_extension_load(fts)        # Full Text Search (BM25, etc.)
duckdb_extension_load(vss)        # Vector Similarity Search (HNSW, IVF)
duckdb_extension_load(json)       # JSON parsing and manipulation
