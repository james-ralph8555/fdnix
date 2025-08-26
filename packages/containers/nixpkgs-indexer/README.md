# fdnix Nixpkgs Indexer

This container indexes nixpkgs by combining metadata extraction and embedding generation into a single ECS task that can run both phases sequentially or individually.

## Features

- Metadata Generation: Extracts nixpkgs package metadata using nix-env and stores in DuckDB
- Embedding Generation: Creates semantic embeddings using AWS Bedrock and stores in DuckDB with VSS index
- Unified Execution: Can run both phases in sequence or individually based on configuration
- S3 Integration: Automatically uploads final DuckDB artifact to S3
- Security: Runs as a non-root user

## Environment Variables

### Required
- `AWS_REGION`: AWS region for services
- `BEDROCK_MODEL_ID`: Bedrock model ID for embeddings (only needed for embedding mode)

### Optional
- `PROCESSING_MODE`: Mode to run - "metadata", "embedding", or "both" (default: "both")
- `OUTPUT_PATH`: Local path for DuckDB file (default: "/out/fdnix.duckdb")
- `ARTIFACTS_BUCKET`: S3 bucket for artifact upload
- `DUCKDB_KEY`: S3 key for DuckDB artifact
- `DUCKDB_PATH`: Path to DuckDB file for embedding phase (defaults to OUTPUT_PATH)

## Processing Modes

### "metadata" mode
1. Clones nixpkgs repository.
2. Extracts package metadata using `nix-env -qaP --json`, then cleans and normalizes fields.
3. Creates DuckDB with `packages` and `packages_fts_source` tables, and builds an FTS index (DuckDB `fts` extension).
4. Optionally uploads the DuckDB artifact to S3 when `ARTIFACTS_BUCKET` and `DUCKDB_KEY` are set.

### "embedding" mode
1. Reads existing DuckDB file (downloads from S3 when `ARTIFACTS_BUCKET`/`DUCKDB_KEY` are provided and the local file is missing).
2. Generates embeddings via AWS Bedrock (e.g., `cohere.embed-english-v3`) for packages without embeddings; resumes idempotently.
3. Writes vectors to the `embeddings` table and creates/refreshes a VSS index for vector search (DuckDB `vss` extension).
4. Optionally uploads the updated DuckDB to S3.

### "both" mode (default)
1. Runs metadata phase first
2. Then runs embedding phase on the generated DuckDB
3. Results in complete DuckDB with both metadata and embeddings

## Usage

The container automatically determines what to run based on the `PROCESSING_MODE` environment variable. For unified processing that handles both metadata extraction and embedding generation:

```bash
docker run -e AWS_REGION=us-east-1 \
           -e BEDROCK_MODEL_ID=cohere.embed-english-v3 \
           -e PROCESSING_MODE=both \
           -e ARTIFACTS_BUCKET=my-bucket \
           -e DUCKDB_KEY=snapshots/fdnix.duckdb \
           fdnix/nixpkgs-indexer
```

## Database Schema

The indexer produces the following schema:

- `packages` table: Complete nixpkgs metadata
- `packages_fts_source` table: Full-text search content
- `embeddings` table: Vector embeddings with VSS index

## Image & Dependencies

- Base: Python 3.11 image with Nix tools for metadata extraction
- Python deps: `boto3`, `duckdb`, `numpy`, `requests`
- Runs as non-root user

