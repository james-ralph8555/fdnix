# fdnix Nixpkgs Indexer

This container indexes nixpkgs via a three‑phase pipeline with clear, S3‑backed artifacts between stages:

1) Metadata → 2) Embeddings (Bedrock batch) → 3) Minified Dataset

Each phase can run independently or in sequence, and artifacts are uploaded to S3 for observability and reuse.

## Features

- Metadata Generation: Extracts nixpkgs package metadata using `nix-env` and writes a LanceDB dataset (directory) with a typed schema.
- Embedding Generation: Generates semantic embeddings with Amazon Titan Embeddings (Text v2) via AWS Bedrock batch; vectors are stored in LanceDB and indexed for vector search.
- Minified Dataset: Derives a minified LanceDB dataset (from the main dataset, after embeddings) with essential columns and a Tantivy-backed FTS index.
- Unified Execution: Run individual phases or all three in order based on configuration.
- S3 Integration: Uploads/downloads entire LanceDB directory structures under S3 key prefixes for traceability between stages.
- Security: Runs as a non-root user.

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
- `OUTPUT_PATH`: Local path for the main LanceDB dataset (default: `/out/fdnix-data.lancedb`)
- `OUTPUT_MINIFIED_PATH`: Local path for the minified LanceDB dataset (default: `/out/fdnix.lancedb`)
- `ARTIFACTS_BUCKET`: S3 bucket for artifact upload (also used as default Bedrock batch IO bucket)
- `LANCEDB_DATA_KEY`: S3 key prefix for the main dataset (e.g., `snapshots/fdnix-data.lancedb`)
- `LANCEDB_MINIFIED_KEY`: S3 key prefix for the minified dataset (e.g., `snapshots/fdnix.lancedb`)
- `LANCEDB_PATH`: Path to LanceDB dataset for embedding phase (defaults to the main dataset)

Bedrock model and batch settings:
- `BEDROCK_MODEL_ID`: Model ID (default: `amazon.titan-embed-text-v2:0`)
- `BEDROCK_OUTPUT_DIMENSIONS`: Embedding dimensions (default: `256`)
- `BEDROCK_BATCH_SIZE`: Max records per batch job (default: `10000`)
- `BEDROCK_POLL_INTERVAL`: Seconds between job status polls (default: `60`)
- `BEDROCK_MAX_WAIT_TIME`: Max seconds to wait for job completion (default: `7200`)

Layer publish (optional):
- `PUBLISH_LAYER`: When truthy (`true`/`1`/`yes`), publish the minified dataset as a Lambda Layer after processing.
- `LAYER_ARN`: Unversioned Lambda Layer ARN or name (e.g., `arn:aws:lambda:us-east-1:123456789012:layer:fdnix-database-layer`).
- Note: LanceDB artifacts are directory trees. When publishing a layer, `LANCEDB_MINIFIED_KEY` should reference the packaged layer object (e.g., a zip) stored in S3, or a compatible artifact as produced by your pipeline.

FTS tuning (metadata/minified phases):
- `FTS_STOPWORDS`: Stopwords language (default: `english`).
- `FTS_STEMMER`: Stemmer language (default: `english`, set empty to disable).

Vector index tuning (embedding/minified phases):
- `VECTOR_INDEX_PARTITIONS`: IVF partitions (default: `256`).
- `VECTOR_INDEX_SUB_VECTORS`: PQ sub-vectors (default: `8`).

## Processing Modes

### "metadata"
1. Extracts package metadata from the nixpkgs channel using `nix-env -qaP --json`, then cleans and normalizes fields (no git clone).
2. Creates the main LanceDB dataset (`fdnix-data.lancedb`) with complete metadata.
3. Uploads the dataset directory to S3 when `ARTIFACTS_BUCKET` and `LANCEDB_DATA_KEY` are provided (uploaded under that key prefix).

### "embedding"
1. Opens the main LanceDB dataset (downloads from S3 when configured and the local dataset is missing).
2. Generates embeddings via AWS Bedrock batch inference using Amazon Titan Embeddings (Text v2):
   - Builds a JSONL input with `(recordId, modelInput)` and uploads to S3.
   - Submits a Bedrock model invocation job and polls for completion.
   - Downloads JSONL results and maps vectors back to packages; resumes idempotently using content hashes.
3. Writes vectors to the packages table's `vector` field and creates/refreshes an IVF‑PQ vector index.
4. Uploads the updated main dataset back to S3 (same `LANCEDB_DATA_KEY` prefix).

### "minified"
1. Derives a minified LanceDB dataset (`fdnix.lancedb`) from the main dataset, copying essential fields and embeddings.
2. Builds the Tantivy FTS index on relevant text fields; refreshes vector index if embeddings present.
3. Uploads the minified dataset to S3 when `LANCEDB_MINIFIED_KEY` is provided (uploaded under that key prefix).

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
  -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb \
  -e LANCEDB_MINIFIED_KEY=snapshots/fdnix.lancedb \
  -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole \
  -e BEDROCK_MODEL_ID=amazon.titan-embed-text-v2:0 \
  -e BEDROCK_OUTPUT_DIMENSIONS=256 \
  fdnix/nixpkgs-indexer

# Single phase runs
# 1) Metadata only → writes main dataset and uploads to S3
docker run --rm --env-file .env -v "$PWD":/out \
  -e AWS_REGION=us-east-1 -e PROCESSING_MODE=metadata \
  -e ARTIFACTS_BUCKET=my-bucket -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb \
  fdnix/nixpkgs-indexer

# 2) Embeddings only → updates main dataset and uploads to S3 using Bedrock batch
docker run --rm --env-file .env -v "$PWD":/out \
  -e AWS_REGION=us-east-1 -e PROCESSING_MODE=embedding \
  -e ARTIFACTS_BUCKET=my-bucket -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb \
  -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole \
  -e BEDROCK_MODEL_ID=amazon.titan-embed-text-v2:0 \
  fdnix/nixpkgs-indexer

# 3) Minified only → consumes main dataset and uploads minified dataset to S3
docker run --rm --env-file .env -v "$PWD":/out \
  -e AWS_REGION=us-east-1 -e PROCESSING_MODE=minified \
  -e ARTIFACTS_BUCKET=my-bucket \
  -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb \
  -e LANCEDB_MINIFIED_KEY=snapshots/fdnix.lancedb \
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

## Dataset Schema

The indexer produces LanceDB datasets (directories):

- Main dataset (`fdnix-data.lancedb`): Complete nixpkgs metadata; embeddings added when generated.
- Minified dataset (`fdnix.lancedb`): Essential columns only; simplified license/maintainers; FTS built on text fields.
- Core table in both:
  - `packages` table with typed columns and a `vector` field for embeddings; Tantivy FTS over key text fields; IVF‑PQ vector index when embeddings are present.

## Build & Run

- Build (from repo root):
  - `docker build -t fdnix/nixpkgs-indexer packages/containers/nixpkgs-indexer`
- Run (local output to current dir):
  - Metadata only: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=metadata fdnix/nixpkgs-indexer`
  - Embeddings only (Bedrock batch): `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=embedding -e ARTIFACTS_BUCKET=fdnix-artifacts -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole fdnix/nixpkgs-indexer`
  - Both + upload to S3: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=both -e ARTIFACTS_BUCKET=fdnix-artifacts -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb -e LANCEDB_MINIFIED_KEY=snapshots/fdnix.lancedb -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole fdnix/nixpkgs-indexer`

Notes:
- Embedding mode uses AWS Bedrock batch inference with Amazon Titan Embeddings (Text v2); no external API keys are required. Provide `BEDROCK_ROLE_ARN` and S3 buckets/prefixes.
- S3 upload requires `ARTIFACTS_BUCKET`, `AWS_REGION`, and at least one of `LANCEDB_DATA_KEY` or `LANCEDB_MINIFIED_KEY` (prefixes).
- Default local artifact paths: `/out/fdnix-data.lancedb` (main) and `/out/fdnix.lancedb` (minified).

## Image & Dependencies

- Base: `nixos/nix:2.31.0`; dependencies installed via Nix (`nix-env`).
- Installed deps: `python313`, `lancedb`, `pydantic`, `pandas`, `pyarrow`, `boto3`, `httpx`, `numpy`.
- Entry: `python src/index.py`; runs as non-root user.

Layer contents: When publishing is enabled, the packaged minified dataset artifact is placed in the layer (path depends on layer packaging).

## Notes on Layer Publishing

- The layer publisher uses the minified dataset artifact. Set `PUBLISH_LAYER=true` and provide `LAYER_ARN`, `ARTIFACTS_BUCKET`, `AWS_REGION`, and `LANCEDB_MINIFIED_KEY` (required).

## Minified Dataset Rationale

- Smaller Lambda layer and faster cold starts
- Optimized query performance over a focused schema
- Cost savings on storage and transfer
- Full database preserved in S3 for debugging/analytics

Tip: Consider versioned S3 prefixes (e.g., `snapshots/2024-08-26/fdnix-data.lancedb/`) for snapshotting and rollbacks while keeping a `latest` alias for the pipeline.

## Legacy Support Removal

- Backward compatibility for `DUCKDB_KEY` has been removed. Use `LANCEDB_DATA_KEY` for the main dataset and `LANCEDB_MINIFIED_KEY` for the minified dataset. `LANCEDB_MINIFIED_KEY` is required for layer publishing.
