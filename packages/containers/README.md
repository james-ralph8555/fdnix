# fdnix Data Processing Containers

Status: **Two-stage pipeline architecture** for optimal resource utilization - nixpkgs evaluation (Stage 1) followed by data processing (Stage 2).

## What's Included

### Current Architecture (Two-Stage Pipeline)

- **Stage 1: Nixpkgs Evaluator** (`packages/containers/nixpkgs-evaluator/`)
  - Purpose: Extract raw package metadata using nix-eval-jobs
  - Resources: 8 vCPU, 48GB RAM (optimized for nix-eval-jobs)  
  - Output: Raw JSONL file uploaded to S3
  
- **Stage 2: Nixpkgs Processor** (`packages/containers/nixpkgs-processor/`)
  - Purpose: Process JSONL into LanceDB, generate embeddings, publish layers
  - Resources: 2 vCPU, 6GB RAM (optimized for data processing)
  - Input: Raw JSONL from Stage 1
  - Phases: Metadata processing, Embeddings, Minified (run individually or all)
  - Outputs: Main dataset `fdnix-data.lancedb` + Minified dataset `fdnix.lancedb`

## How It Runs in AWS

### Two-Stage Pipeline Architecture

- **Orchestration**: Step Functions `fdnix-daily-pipeline` runs nightly (02:00 UTC) via EventBridge
- **Sequence**: 
  1. **Stage 1** (Evaluator): ECS Fargate runs nixpkgs-evaluator (8vCPU/48GB) to extract raw JSONL
  2. **Stage 2** (Processor): ECS Fargate runs nixpkgs-processor (2vCPU/6GB) to process JSONL into final datasets
- **Communication**: Stage 1 writes JSONL to S3, Stage 2 reads from the same location (timestamp-coordinated)
- **Resources (from CDK)**:
  - Artifacts bucket: `fdnix-artifacts` (stores JSONL + LanceDB datasets)
  - Lambda Layer: `fdnix-db-layer` (packages the minified dataset)
  - Logs: separate log groups for evaluator and processor
  - ECR: two repositories (`fdnix-nixpkgs-evaluator`, `fdnix-nixpkgs-processor`)

## Container Environment Variables

- Common:
  - `AWS_REGION`: AWS region used by clients.
  - `ARTIFACTS_BUCKET`: S3 bucket for LanceDB artifacts (e.g., `fdnix-artifacts`).
  - `LANCEDB_DATA_KEY`: S3 key prefix for the main dataset (e.g., `snapshots/fdnix-data.lancedb`).
  - `LANCEDB_MINIFIED_KEY`: S3 key prefix for the minified dataset used by the Lambda layer (e.g., `snapshots/fdnix.lancedb`).
  - `PROCESSING_MODE`: `metadata` | `embedding` | `minified` | `both` | `full` (default: `both` → all phases).
  - FTS (optional tuning): `FTS_STOPWORDS` (default `english`), `FTS_STEMMER` (default `english`).
- Embeddings (AWS Bedrock batch):
  - `BEDROCK_ROLE_ARN`: IAM role ARN Bedrock uses for batch inference S3 access (required for embedding mode).
  - `BEDROCK_MODEL_ID`: Model ID (default: `amazon.titan-embed-text-v2:0`).
  - `BEDROCK_OUTPUT_DIMENSIONS`: Embedding dimensions (default: `256`).
  - `BEDROCK_INPUT_BUCKET` and `BEDROCK_OUTPUT_BUCKET`: Separate buckets for input/output (or set a single `ARTIFACTS_BUCKET`).
  - `BEDROCK_BATCH_SIZE`: Max records per batch job (default: `10000`).
  - `BEDROCK_POLL_INTERVAL`: Seconds between job status polls (default: `60`).
  - `BEDROCK_MAX_WAIT_TIME`: Max seconds to wait for job completion (default: `7200`).
  - Vector index tuning: `VECTOR_INDEX_PARTITIONS` (default `256`), `VECTOR_INDEX_SUB_VECTORS` (default `8`).
- Local paths (optional):
- `OUTPUT_PATH`: Local path for the main dataset (default: `/out/fdnix-data.lancedb`).
- `OUTPUT_MINIFIED_PATH`: Local path for the minified dataset (default: `/out/fdnix.lancedb`).
- `LANCEDB_PATH`: Input LanceDB path for embedding mode (defaults to the main dataset).

## AWS Guidelines

- Naming: Prefix resources with `fdnix-` (e.g., `fdnix-artifacts`, `fdnix-nixpkgs-indexer`, `fdnix-db-layer`).
- Secrets: Store API keys in SSM Parameter Store or Secrets Manager; reference them in task definitions or via CDK.
- IAM: Grant least-privilege to ECS tasks for S3 read/write on the configured keys and for Lambda layer publishing when enabled.
- DNS/TLS: DNS via Cloudflare; ACM certificates for CloudFront in `us-east-1` (handled in CDK).

## Build (Local)

From repo root:

- Nixpkgs Indexer:
  - `docker build -t fdnix/nixpkgs-indexer packages/containers/nixpkgs-indexer`

## Run (Local)

- All phases with S3 artifacts (Bedrock batch):
  - `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=both -e ARTIFACTS_BUCKET=fdnix-artifacts -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb -e LANCEDB_MINIFIED_KEY=snapshots/fdnix.lancedb -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole fdnix/nixpkgs-indexer`
- Single phases:
  - Metadata: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=metadata -e ARTIFACTS_BUCKET=fdnix-artifacts -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb fdnix/nixpkgs-indexer`
  - Embedding (Bedrock batch): `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=embedding -e ARTIFACTS_BUCKET=fdnix-artifacts -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb -e BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/BedrockBatchRole fdnix/nixpkgs-indexer`
  - Minified: `docker run --rm --env-file .env -e AWS_REGION=us-east-1 -e PROCESSING_MODE=minified -e ARTIFACTS_BUCKET=fdnix-artifacts -e LANCEDB_DATA_KEY=snapshots/fdnix-data.lancedb -e LANCEDB_MINIFIED_KEY=snapshots/fdnix.lancedb fdnix/nixpkgs-indexer`

For AWS runs, provide `ARTIFACTS_BUCKET` and prefixes for one or both artifacts (`LANCEDB_DATA_KEY` and/or `LANCEDB_MINIFIED_KEY`). Embedding mode requires Bedrock batch configuration (`BEDROCK_ROLE_ARN`, buckets, and model settings). No external API keys are required for Bedrock.

## Deployment Notes

- CDK defines a single ECR repo and task definition sized for the end-to-end job.
- Push image to ECR (example):
  - `aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com`
  - `docker tag fdnix/nixpkgs-indexer $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-indexer:latest`
  - `docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-indexer:latest`
- Step Functions then publishes/updates the Lambda Layer from the artifact.

## Outputs and Schema

- Main dataset: `fdnix-data.lancedb` (complete metadata + embeddings once generated)
- Minified dataset: `fdnix.lancedb` (only essential columns + FTS; simplified license/maintainers; used by Lambda layer)
- Core table present in both:
  - `packages(...)` with typed columns and a `vector` field; FTS over key text fields; IVF‑PQ vector index when embeddings are present.

See `packages/containers/nixpkgs-indexer/README.md` for detailed usage and troubleshooting.

## Phase Details

- Metadata phase:
  - Extracts package metadata from nixpkgs using `nix-eval-jobs` (clones nixpkgs release branch).
  - Processes JSONL output to extract both package metadata and dependency information.
  - Cleans and normalizes fields (ids, names, attrs, descriptions, maintainers, etc.).
  - Writes `packages` table and `dependencies` table to LanceDB.
  - Optionally uploads the main dataset directory and dependency data to S3 under the configured key prefixes.
- Embedding phase:
  - Opens existing LanceDB (downloads from S3 when `ARTIFACTS_BUCKET` plus the relevant key is provided if not present locally).
  - Generates text embeddings via AWS Bedrock batch inference (Amazon Titan Embeddings). Batch I/O is managed via S3; the task polls for completion.
  - Stores/updates vectors on the `packages.vector` field.
  - Builds/refreshes IVF‑PQ vector index via LanceDB.
  - Optionally uploads the updated main dataset to S3.
- Minified phase:
  - Creates `fdnix.lancedb` by copying essential data and embeddings from the main dataset.
  - Builds the FTS index in the minified dataset.
  - Uploads the minified dataset to S3 for use by the Lambda layer.

## Benefits of Minified Layer

- Reduced Lambda layer size and faster cold starts
- Optimized query performance over a smaller dataset
- Lower storage and transfer costs
- Full database preserved in S3 for debugging and analytics
