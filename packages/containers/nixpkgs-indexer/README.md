# fdnix Nixpkgs Indexer

This container indexes nixpkgs via a three‑phase pipeline with clear, S3‑backed artifacts between stages:

1) Metadata → 2) Embeddings (Bedrock batch) → 3) Minified DB

Each phase can run independently or in sequence, and artifacts are uploaded to S3 for observability and reuse.

## Features

- Metadata Generation: Extracts nixpkgs package metadata using nix-env and writes a full DuckDB database
- Embedding Generation: Generates semantic embeddings with Amazon Titan Embeddings (Text v2) via AWS Bedrock batch inference; vectors are stored in the full DuckDB with a VSS index
- Minified Database: Derives a minified DuckDB (from the full DB, after embeddings) with essential columns and an FTS index
- Unified Execution: Run individual phases or all three in order based on configuration
- S3 Integration: Uploads full and minified artifacts to distinct keys for traceability between stages
- Security: Runs as a non-root user

## Environment Variables

### Required
- `AWS_REGION`: AWS region for services

### Required (embedding mode)
- `BEDROCK_ROLE_ARN`: IAM role ARN that Bedrock uses for batch inference S3 access
- S3 buckets for Bedrock batch IO (one of):
  - `ARTIFACTS_BUCKET`: Single bucket used for both input and output prefixes; or
  - `BEDROCK_INPUT_BUCKET` and `BEDROCK_OUTPUT_BUCKET`: Separate buckets for input/output

### Optional
- `PROCESSING_MODE`: One of `metadata` | `embedding` | `minified` | `both` | `full` (default: `both` → runs all three phases)
- `OUTPUT_PATH`: Local path for the full database (default: "/out/fdnix-data.duckdb")
- `OUTPUT_MINIFIED_PATH`: Local path for the minified database (default: "/out/fdnix.duckdb")
- `ARTIFACTS_BUCKET`: S3 bucket for artifact upload (also used as default Bedrock batch IO bucket)
- `DUCKDB_DATA_KEY`: S3 key for the full database (e.g., `snapshots/fdnix-data.duckdb`)
- `DUCKDB_MINIFIED_KEY`: S3 key for the minified database (e.g., `snapshots/fdnix.duckdb`)
- `DUCKDB_PATH`: Path to DuckDB file for embedding phase (defaults to the full DB)

Bedrock model and batch settings:
- `BEDROCK_MODEL_ID`: Model ID (default: `amazon.titan-embed-text-v2:0`)
- `BEDROCK_OUTPUT_DIMENSIONS`: Embedding dimensions (default: `256`)
- `BEDROCK_BATCH_SIZE`: Max records per batch job (default: `50000`)
- `BEDROCK_POLL_INTERVAL`: Seconds between job status polls (default: `60`)
- `BEDROCK_MAX_WAIT_TIME`: Max seconds to wait for job completion (default: `7200`)

Layer publish (optional):
- `PUBLISH_LAYER`: When truthy (`true`/`1`/`yes`), publish the DuckDB to a Lambda Layer after processing
- `LAYER_ARN`: Unversioned Lambda Layer ARN or name (e.g., `arn:aws:lambda:us-east-1:123456789012:layer:fdnix-database-layer`)

FTS tuning (metadata phase):
- `FTS_STOPWORDS`: Stopwords language (default: `english`)
- `FTS_STEMMER`: Stemmer language (default: `english`, set empty to disable)
- `FTS_INDEX_NAME`: Index name (default: `packages_fts_idx`)

VSS tuning (embedding phase):
- `VSS_HNSW_M`: HNSW M (default: `16`)
- `VSS_EF_CONSTRUCTION`: HNSW ef_construction (default: `200`)
- `VSS_EF_SEARCH`: ef_search session setting (default: `40`)
 - Persistence: on-disk HNSW index persistence is enabled by executing `SET hnsw_enable_experimental_persistence = true` inside DuckDB.

## Processing Modes

### "metadata"
1. Extracts package metadata from the nixpkgs channel using `nix-env -qaP --json`, then cleans and normalizes fields (no git clone).
2. Creates the full DuckDB (`fdnix-data.duckdb`) with complete metadata only (no FTS index here).
3. Uploads the full DB to S3 when `ARTIFACTS_BUCKET` and `DUCKDB_DATA_KEY` are provided.

### "embedding"
1. Opens the full DuckDB (downloads from S3 when configured and the local file is missing).
2. Generates embeddings via AWS Bedrock batch inference using Amazon Titan Embeddings (Text v2):
   - Builds a JSONL input with `(recordId, modelInput)` and uploads to S3.
   - Submits a Bedrock model invocation job and polls for completion.
   - Downloads JSONL results and maps vectors back to packages; resumes idempotently using content hashes.
3. Writes vectors to `embeddings` and creates/refreshes a VSS index in the full DB.
4. Uploads the updated full DB back to S3 (same `DUCKDB_DATA_KEY`).

### "minified"
1. Derives a minified DuckDB (`fdnix.duckdb`) from the full DB, copying essential fields, embeddings, and FTS source.
2. Builds the FTS index in the minified DB.
3. Uploads the minified DB to S3 when `DUCKDB_MINIFIED_KEY` is provided.

### "both" (default) and "full"
- Run all three phases in sequence: metadata → embedding → minified.
- Alias: `full` behaves the same as `both`.

## Usage

The container automatically determines what to run based on the `PROCESSING_MODE` environment variable. For unified processing that handles both metadata extraction and embedding generation:

```bash
# All three phases, persisting artifacts to S3 (Bedrock batch for embeddings)
docker run --rm --env-file .env -v "$PWD":/out \
  -e AWS_REGION=us-east-1 \
  -e PROCESSING_MODE=both \
  -e ARTIFACTS_BUCKET=my-bucket \
  -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb \
  -e DUCKDB_MINIFIED_KEY=snapshots/fdnix.duckdb \
  -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole \
  -e BEDROCK_MODEL_ID=amazon.titan-embed-text-v2:0 \
  -e BEDROCK_OUTPUT_DIMENSIONS=256 \
  fdnix/nixpkgs-indexer

# Single phase runs
# 1) Metadata only → writes full DB and uploads to S3
docker run --rm --env-file .env -v "$PWD":/out \
  -e AWS_REGION=us-east-1 -e PROCESSING_MODE=metadata \
  -e ARTIFACTS_BUCKET=my-bucket -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb \
  fdnix/nixpkgs-indexer

# 2) Embeddings only → updates full DB and uploads to S3 using Bedrock batch
docker run --rm --env-file .env -v "$PWD":/out \
  -e AWS_REGION=us-east-1 -e PROCESSING_MODE=embedding \
  -e ARTIFACTS_BUCKET=my-bucket -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb \
  -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole \
  -e BEDROCK_MODEL_ID=amazon.titan-embed-text-v2:0 \
  fdnix/nixpkgs-indexer

# 3) Minified only → consumes full DB and uploads minified DB to S3
docker run --rm --env-file .env -v "$PWD":/out \
  -e AWS_REGION=us-east-1 -e PROCESSING_MODE=minified \
  -e ARTIFACTS_BUCKET=my-bucket \
  -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb \
  -e DUCKDB_MINIFIED_KEY=snapshots/fdnix.duckdb \
  fdnix/nixpkgs-indexer
```

## AWS Integration and Guidelines

- Resource naming: Use `fdnix-` prefixes for AWS resources (e.g., `fdnix-artifacts`, `fdnix-db-layer`).
- Bedrock: No external API keys. Ensure the task role can call Bedrock and pass the batch role:
  - `bedrock:CreateModelInvocationJob`, `bedrock:GetModelInvocationJob`, `bedrock:ListFoundationModels`
  - `iam:PassRole` on `BEDROCK_ROLE_ARN`
- S3: The task role needs `s3:GetObject`, `s3:PutObject`, and `s3:ListBucket` for the artifact keys and the Bedrock batch prefixes.
- Lambda layer: When `PUBLISH_LAYER` is enabled, grant permissions to publish/update the specified layer.
- Regions & certs: If integrating with CloudFront, ACM certificates must be in `us-east-1` (handled by the CDK stacks).
- DNS: Managed via Cloudflare; see CDK docs for setup.

## Database Schema

The indexer produces the following schema:

- Full database (`fdnix-data.duckdb`): Complete nixpkgs metadata and embeddings (when generated)
- Minified database (`fdnix.duckdb`): Essential columns only; simplified license/maintainers; FTS built on minified text
- Core tables in both:
  - `packages` table: Metadata (minified drops non-essential columns)
  - `packages_fts_source` table: Full-text search content; FTS index built only in minified DB
  - `embeddings` table: Vector embeddings with VSS index

## Build & Run

- Build (from repo root):
  - `docker build -t fdnix/nixpkgs-indexer packages/containers/nixpkgs-indexer`
- Run (local output to current dir):
  - Metadata only: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=metadata fdnix/nixpkgs-indexer`
  - Embeddings only (Bedrock batch): `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=embedding -e ARTIFACTS_BUCKET=fdnix-artifacts -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole fdnix/nixpkgs-indexer`
  - Both + upload to S3: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=both -e ARTIFACTS_BUCKET=fdnix-artifacts -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb -e DUCKDB_MINIFIED_KEY=snapshots/fdnix.duckdb -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole fdnix/nixpkgs-indexer`

Notes:
- Embedding mode uses AWS Bedrock batch inference with Amazon Titan Embeddings (Text v2); no external API keys are required. Provide `BEDROCK_ROLE_ARN` and S3 buckets/prefixes.
- S3 upload requires `ARTIFACTS_BUCKET`, `AWS_REGION`, and at least one of `DUCKDB_DATA_KEY` or `DUCKDB_MINIFIED_KEY`.
- Default local artifact paths: `/out/fdnix-data.duckdb` (full) and `/out/fdnix.duckdb` (minified).

## Image & Dependencies

- Base: `nixos/nix:2.31.0`; dependencies installed via Nix (`nix-env`)
- Installed deps: `python313`, `duckdb`, `boto3`, `numpy`, `requests`, `httpx`
- Entry: `python src/index.py`; runs as non-root user

Layer contents: When publishing is enabled, the minified database is placed in the layer under `/opt/fdnix/fdnix.duckdb`.

## Notes on Layer Publishing

- The layer publisher uses the minified database. Set `PUBLISH_LAYER=true` and provide `LAYER_ARN`, `ARTIFACTS_BUCKET`, `AWS_REGION`, and `DUCKDB_MINIFIED_KEY` (required).

## Minified Database Rationale

- Smaller Lambda layer and faster cold starts
- Optimized query performance over a focused schema
- Cost savings on storage and transfer
- Full database preserved in S3 for debugging/analytics

Tip: Consider versioned S3 keys (e.g., `snapshots/2024-08-26/fdnix-data.duckdb`) for snapshotting and rollbacks while keeping a `latest` alias for the pipeline.

## Legacy Support Removal

- Backward compatibility for `DUCKDB_KEY` has been removed. Use `DUCKDB_DATA_KEY` for the full database and `DUCKDB_MINIFIED_KEY` for the minified database. `DUCKDB_MINIFIED_KEY` is required for layer publishing.
