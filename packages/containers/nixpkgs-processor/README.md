# nixpkgs-processor

A container-based data processing pipeline that transforms raw nixpkgs package metadata into searchable SQLite databases with full-text search capabilities for the fdnix search engine.

## Architecture Overview

This processor implements a multi-phase data transformation pipeline that converts nix-eval-jobs JSONL output into optimized SQLite databases with full-text search capabilities.

```mermaid
graph TB
    A[S3 JSONL Input] --> B[S3JsonlReader]
    B --> C[DataProcessor] 
    C --> D[SQLite Writer]
    C --> E[Dependency Writer]
    D --> F[Minified DB Creation]
    F --> G[Layer Publisher]
    G --> H[S3 Artifact Upload]
    
    subgraph "Data Processing Phase"
        C
        D
        E
    end
    
    subgraph "Publishing Phase"
        F
        G
        H
    end
```

## Data Processing Pipeline

### Phase 1: JSONL Reading (`s3_jsonl_reader.py`)

Reads raw nix-eval-jobs output from S3:
- Downloads compressed JSONL files from S3
- Extracts metadata header with extraction timestamp and branch info
- Parses individual package records with error tolerance
- Returns structured package data and metadata

**Input Format**: JSONL with first line containing `_metadata` object followed by package records

### Phase 2: Data Processing (`data_processor.py`) 

Transforms raw nix package data into structured format:

#### Package Metadata Processing
- **Name/Version Parsing**: Extracts package name and version from nix name strings
- **License Normalization**: Handles complex license objects (string, array, object types)
- **Maintainer Processing**: Structures maintainer data with name, email, GitHub info
- **Category Classification**: Maps nixpkgs categories to user-friendly classifications
- **Platform Handling**: Processes supported platform lists
- **Status Flags**: Tracks broken, unfree, insecure, unsupported status

#### Dependency Processing
- **Store Path Analysis**: Extracts dependency names from Nix store paths
- **Build Input Classification**: Processes inputDrvs into buildInputs
- **Dependency Counting**: Calculates total dependency metrics
- **Relationship Mapping**: Creates package-to-dependency mappings

```mermaid
flowchart TD
    A[Raw Package JSON] --> B[Name/Version Parser]
    A --> C[License Processor]
    A --> D[Maintainer Processor]
    A --> E[Category Classifier]
    A --> F[Dependency Analyzer]
    
    B --> G[Structured Package]
    C --> G
    D --> G
    E --> G
    
    F --> H[Dependency Record]
    
    G --> I[LanceDB Package Table]
    H --> J[LanceDB Dependency Table]
```

### Phase 3: Database Creation (`sqlite_writer.py`)

Creates optimized SQLite databases with FTS capabilities:

#### Package Table Schema
- **Core Fields**: package_id, package_name, version, attribute_path
- **Content Fields**: description, long_description, homepage
- **Metadata Fields**: license, maintainers, platforms, category
- **Status Fields**: broken, unfree, available, insecure, unsupported
- **Search Fields**: main_program, position, outputs_to_install
- **Metadata Fields**: content_hash

#### FTS Virtual Table Schema
- **Search Fields**: package_id, package_name, attribute_path, description, long_description, main_program
- **Content Storage**: Minimal (rowid reference only) - stores no actual content data
- **Index Type**: SQLite FTS5 virtual table for efficient full-text search

#### Index Creation
- **FTS Index**: Full-text search on all text fields with SQLite FTS5
- **Performance Indexes**: Regular indexes on package_name, category, and status fields

### Phase 4: Individual Node S3 Writing (`node_s3_writer.py`)

Creates individual JSON files for dependency viewer module:

#### Dependency Graph Processing (`dependency_graph.py`)
- **Graph Construction**: Builds NetworkX directed graph from package dependencies
- **Relationship Analysis**: Calculates direct and transitive dependencies/dependents
- **Graph Statistics**: Provides comprehensive dependency metrics
- **Circular Detection**: Identifies circular dependency cycles

#### Node File Generation
Each package becomes a separate S3 JSON file containing:
- **Core Metadata**: All LanceDB fields (excluding embeddings/FTS)
- **Dependencies**: Direct and transitive dependency lists
- **Dependents**: Packages that depend on this package
- **Metrics**: Dependency counts and relationship statistics
- **Node Linking**: IDs for connecting nodes in dependency viewer

#### S3 Structure
```
s3://bucket/nodes/
├── index.json                 # Summary index with all packages
├── package1-1.0.0.json       # Individual package nodes
├── package2-2.1.3.json
└── ...
```

#### Performance Features
- **Parallel Upload**: Multi-threaded S3 uploads for faster processing
- **Batch Processing**: Efficient batching of S3 operations  
- **Clear Existing**: Optional cleanup of previous node files
- **Index Generation**: Creates searchable index of all nodes

### Phase 5: Minified Database Creation

Creates optimized subset for runtime:
- **Column Filtering**: Keeps only essential fields for search
- **Index Optimization**: Recreates FTS indexes
- **Size Reduction**: Removes debug/build-only metadata
- **Performance**: Optimized for query response time

### Phase 6: Layer Publishing (`layer_publisher.py`)

Publishes SQLite database as AWS Lambda layer:
- **Compression**: Packages single database file into ZIP
- **Version Management**: Updates Lambda layer versions
- **Deployment**: Makes layer available to search APIs

## Environment Configuration

### Required Variables
- `AWS_REGION`: AWS region for services
- `ARTIFACTS_BUCKET`: S3 bucket for input JSONL and LanceDB artifacts
- `PROCESSED_FILES_BUCKET`: S3 bucket for stats JSON and per-package node JSON files
- `JSONL_INPUT_KEY`: S3 key for input JSONL file (must point to the evaluator's brotli-compressed `.jsonl.br`)

### Processing Control
- `PROCESSING_MODE`: `metadata` | `minified` | `both` (aliases `all`/`full` → `both`; default: `both`)
- `ENABLE_NODE_S3`: Enable/disable individual node S3 writing (default: `true`)
- `ENABLE_STATS`: Enable/disable writing aggregate stats JSON (default: `true`)

### Output Configuration
- `SQLITE_DATA_KEY`: S3 key for main database (defaulted if unset, backward compatible with `LANCEDB_DATA_KEY`)
- `SQLITE_MINIFIED_KEY`: S3 key for minified database (defaulted if unset, backward compatible with `LANCEDB_MINIFIED_KEY`)
- `STATS_S3_KEY`: S3 key for stats JSON (defaulted if unset)
- `NODE_S3_PREFIX`: S3 prefix for individual node files (default: `nodes/`)
- `CLEAR_EXISTING_NODES`: Clear existing node files before upload (default: `true`)
- `NODE_S3_MAX_WORKERS`: Max parallel threads for node uploads (default: `10`)

### Layer Publishing (optional)
- `PUBLISH_LAYER`: When `true`, publishes the minified database to the Lambda layer
- `LAYER_ARN`: Target Lambda layer ARN

## Container Deployment

Built on `nixos/nix` with Python dependencies via Nix:
- SQLite for database storage with FTS5
- SQLAlchemy for database operations
- Boto3 for AWS integration  
- Pandas for data processing

Entry point: `python src/index.py`

## Build & Run (examples)

Build:
- `docker build -t fdnix/nixpkgs-processor packages/containers/nixpkgs-processor`

Run (reads evaluator output and generates SQLite artifacts):
- `docker run --rm \
    -e AWS_REGION=us-east-1 \
    -e ARTIFACTS_BUCKET=fdnix-artifacts \
    -e PROCESSED_FILES_BUCKET=fdnix-processed \
    -e JSONL_INPUT_KEY=evaluations/<ts>/nixpkgs-raw.jsonl.br \
    -e PROCESSING_MODE=both \
    fdnix/nixpkgs-processor`

## Error Handling

- **Graceful Degradation**: Continues processing on individual package failures
- **Incremental Recovery**: Supports resuming from partial failures
- **S3 Retry Logic**: Built-in retry for transient AWS errors
- **Validation**: Schema validation and data integrity checks
- **Logging**: Comprehensive logging for debugging and monitoring
