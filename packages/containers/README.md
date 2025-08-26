# fdnix Data Processing Containers

Status: ✅ Phase 2 Complete — Data Processing Pipeline is ready to deploy.

## What’s Included

- Metadata Generator
  - Complete nixpkgs extraction via `nix-env -qaP --json`
  - Efficient batching for 120k+ packages
  - Robust retries and validation
  - DynamoDB write via AWS SDK v3
- Embedding Generator
  - AWS Bedrock with `cohere.embed-english-v3`
  - S3 vector storage using compressed batch files (no OpenSearch)
  - Vector index creation for search
  - Scans only packages missing embeddings
- Infrastructure Integration
  - CDK-aligned env vars and IAM
  - Step Functions orchestration (metadata → embeddings)
  - Daily automation via EventBridge cron
  - Right-sized CPU/memory per task

## How It Runs in AWS

- Orchestration: Step Functions `fdnix-daily-pipeline` runs nightly (02:00 UTC) via EventBridge rule `fdnix-daily-pipeline-trigger`.
- Sequence: ECS Fargate runs containers sequentially
  1) Metadata Generator → (wait) → 2) Embedding Generator.
- Resources (from CDK):
  - DynamoDB table: `fdnix-packages` (PK: `packageName`, SK: `version`)
  - S3 bucket: `fdnix-vec` (vectors + index)
  - Logs: `/fdnix/metadata-generator`, `/fdnix/embedding-generator`
  - ECR repos: `fdnix-metadata-generator`, `fdnix-embedding-generator`

## Container Environment Variables

- Common:
  - `AWS_REGION`: AWS region used by clients.
- Metadata Generator:
  - `DYNAMODB_TABLE`: DynamoDB table (e.g., `fdnix-packages`).
- Embedding Generator:
  - `DYNAMODB_TABLE`: DynamoDB table (e.g., `fdnix-packages`).
  - `S3_BUCKET`: S3 bucket for vectors/index (e.g., `fdnix-vec`).
  - `BEDROCK_MODEL_ID`: Bedrock model id (CDK uses `cohere.embed-english-v3`).

## Build (Local)

From repo root:

- Metadata Generator:
  - `docker build -t fdnix/metadata-generator packages/containers/metadata-generator`
- Embedding Generator:
  - `docker build -t fdnix/embedding-generator packages/containers/embedding-generator`

## Run (Local)

- Metadata Generator:
  - `docker run --rm -e DYNAMODB_TABLE=fdnix-packages -e AWS_REGION=us-east-1 fdnix/metadata-generator`
- Embedding Generator:
  - `docker run --rm -e DYNAMODB_TABLE=fdnix-packages -e S3_BUCKET=fdnix-vec -e AWS_REGION=us-east-1 -e BEDROCK_MODEL_ID=cohere.embed-english-v3 fdnix/embedding-generator`

Requires AWS credentials with access to DynamoDB, S3, and Bedrock (InvokeModel). Runtime is data/compute heavy.

## Deployment Notes

- CDK creates ECR repos and task defs with sizing:
  - Metadata: `cpu=1024`, `memory=3072MiB`
  - Embedding: `cpu=2048`, `memory=6144MiB`
- Push images to ECR (example):
  - `aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com`
  - `docker tag fdnix/metadata-generator $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-metadata-generator:latest`
  - `docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-metadata-generator:latest`
  - Repeat for `fdnix-embedding-generator`.
- CDK wires env vars and IAM automatically; Step Functions triggers the nightly pipeline.

## Outputs and Formats

- DynamoDB items: package metadata records with `hasEmbedding` boolean.
- S3 vectors: `vectors/batch_*.json.gz` with `{ vectors, count, dimension }`.
- S3 index: `vector-index/index.json.gz` summarizing batch files, counts, and dimension.

See each container README for details and troubleshooting.
