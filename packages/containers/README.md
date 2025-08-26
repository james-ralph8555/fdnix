# fdnix Data Processing Containers

Status: Updated for DuckDB-in-Lambda-layer architecture.

## What’s Included

- Metadata Generator
  - Complete nixpkgs extraction via `nix-env -qaP --json`
  - Cleans and normalizes fields
  - Writes structured tables into a DuckDB file
  - Prepares FTS source and builds FTS index (`fts` extension)
- Embedding Generator
  - AWS Bedrock with `cohere.embed-english-v3`
  - Generates per-package embeddings and writes into DuckDB
  - Builds a vector similarity index using DuckDB `vss`
  - Finalizes a single `.duckdb` artifact for query
- Infrastructure Integration
  - CDK-aligned env vars and IAM
  - Step Functions orchestration (metadata → embeddings → publish layer)
  - Daily automation via EventBridge cron
  - Right-sized CPU/memory per task

## How It Runs in AWS

- Orchestration: Step Functions `fdnix-daily-pipeline` runs nightly (02:00 UTC) via EventBridge rule `fdnix-daily-pipeline-trigger`.
- Sequence: ECS Fargate runs containers sequentially
  1) Metadata Generator → (produces intermediate .duckdb)
  2) Embedding Generator → (adds embeddings, builds FTS/VSS) → uploads final `.duckdb` to S3 artifacts
  3) Publish Layer → create new `fdnix-db-layer` version from artifact
- Resources (from CDK):
  - Artifacts bucket: `fdnix-artifacts` (stores `.duckdb`)
  - Lambda Layer: `fdnix-db-layer` (packages the `.duckdb` under `/opt/fdnix/fdnix.duckdb`)
  - Logs: `/fdnix/metadata-generator`, `/fdnix/embedding-generator`
  - ECR repos: `fdnix-metadata-generator`, `fdnix-embedding-generator`

## Container Environment Variables

- Common:
  - `AWS_REGION`: AWS region used by clients.
  - `ARTIFACTS_BUCKET`: S3 bucket for `.duckdb` artifacts (e.g., `fdnix-artifacts`).
  - `DUCKDB_KEY`: S3 key for the artifact (e.g., `snapshots/fdnix.duckdb`).
- Embedding Generator:
  - `BEDROCK_MODEL_ID`: Bedrock model id (e.g., `cohere.embed-english-v3`).

## Build (Local)

From repo root:

- Metadata Generator:
  - `docker build -t fdnix/metadata-generator packages/containers/metadata-generator`
- Embedding Generator:
  - `docker build -t fdnix/embedding-generator packages/containers/embedding-generator`

## Run (Local)

- Metadata Generator (produces local `fdnix.duckdb`):
  - `docker run --rm -v "$PWD":/out -e AWS_REGION=us-east-1 fdnix/metadata-generator`
- Embedding Generator (consumes and updates `fdnix.duckdb`):
  - `docker run --rm -v "$PWD":/out -e AWS_REGION=us-east-1 -e BEDROCK_MODEL_ID=cohere.embed-english-v3 fdnix/embedding-generator`

For AWS runs, provide `ARTIFACTS_BUCKET` and `DUCKDB_KEY` to upload the final artifact. Requires credentials with access to S3 and Bedrock (InvokeModel).

## Deployment Notes

- CDK creates ECR repos and task defs with sizing:
  - Metadata: `cpu=1024`, `memory=3072MiB`
  - Embedding: `cpu=2048`, `memory=6144MiB`
- Push images to ECR (example):
  - `aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com`
  - `docker tag fdnix/metadata-generator $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-metadata-generator:latest`
  - `docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-metadata-generator:latest`
  - Repeat for `fdnix-embedding-generator`.
- A final Step Functions task publishes/updates the Lambda Layer from the artifact.

## Outputs and Formats

- DuckDB file: `fdnix.duckdb` containing tables:
  - `packages(...)` (normalized metadata)
  - `packages_fts_source(...)` and FTS index
  - `embeddings(package_id, vector)` and VSS index

See each container README for details and troubleshooting.
