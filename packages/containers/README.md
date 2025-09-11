# fdnix Data Processing Containers

Status: Two-stage pipeline — Stage 1 evaluates nixpkgs to JSONL, Stage 2 processes to LanceDB, embeddings, and publishable artifacts.

## What’s Included

- Stage 1: Nixpkgs Evaluator (`packages/containers/nixpkgs-evaluator/`)
  - Purpose: Extract raw package metadata using nix-eval-jobs
  - Resources: 8 vCPU, 48GB RAM (optimized for nix-eval-jobs)
  - Output: Brotli-compressed JSONL (`.jsonl.br`) uploaded to S3 with a metadata header line

- Stage 2: Nixpkgs Processor (`packages/containers/nixpkgs-processor/`)
  - Purpose: Read JSONL from S3, process into LanceDB, optionally generate embeddings, create minified DB, write node files, and (optionally) publish a Lambda layer
  - Resources: 2 vCPU, 6GB RAM (optimized for data processing)
  - Inputs: JSONL from Stage 1 (same artifacts bucket)
  - Outputs: Main dataset `fdnix-data.lancedb` and minified dataset `fdnix.lancedb`; node JSON files and stats JSON

## How It Runs in AWS

- Orchestration: Step Functions `fdnix-daily-pipeline` runs nightly (02:00 UTC) via EventBridge
- Sequence:
  1. Stage 1 (Evaluator): ECS Fargate runs nixpkgs-evaluator (8 vCPU/48 GB) to produce `evaluations/.../nixpkgs-raw.jsonl.br`
  2. Stage 2 (Processor): ECS Fargate runs nixpkgs-processor (2 vCPU/6 GB) to generate LanceDB artifacts, stats, and node files
- Communication: Stage 1 writes to S3; Stage 2 reads the `.br` JSONL from the same bucket (timestamp-coordinated)
- Resources (from CDK):
  - Artifacts bucket: `fdnix-artifacts` (stores JSONL + LanceDB datasets)
  - Processed files bucket: `fdnix-processed` (stores stats JSON and node JSON files)
  - Lambda Layer: `fdnix-db-layer` (packages the minified dataset)
  - Logs: separate log groups for evaluator and processor
  - ECR: two repositories (`fdnix-nixpkgs-evaluator`, `fdnix-nixpkgs-processor`)

## Container Environment Variables

Stage 1 — Evaluator:
- `AWS_REGION`: AWS region
- `ARTIFACTS_BUCKET`: S3 bucket for evaluator output
- `JSONL_OUTPUT_KEY` (optional): S3 key for the raw output (default: `evaluations/<ts>/nixpkgs-raw.jsonl`, uploaded as `.jsonl.br`)

Stage 2 — Processor:
- Required input/output:
  - `AWS_REGION`: AWS region
  - `ARTIFACTS_BUCKET`: S3 bucket for LanceDB artifacts and JSONL input
  - `PROCESSED_FILES_BUCKET`: S3 bucket for stats and node JSON files
  - `JSONL_INPUT_KEY`: S3 key to the evaluator’s `.jsonl.br` file
- Processing control:
  - `PROCESSING_MODE`: `metadata` | `embedding` | `minified` | `both` (aliases `all`/`full` → `both`; default `both`)
  - `ENABLE_EMBEDDINGS`: Enable embedding generation (default `true` in code)
  - `FORCE_REBUILD_EMBEDDINGS`: Regenerate all embeddings (default `false`)
  - `ENABLE_NODE_S3`: Write per-package node JSON to S3 (default `true`)
  - `ENABLE_STATS`: Write aggregate stats JSON to S3 (default `true`)
- Bedrock (embeddings):
  - `BEDROCK_MODEL_ID` (default `amazon.titan-embed-text-v2:0`), `BEDROCK_OUTPUT_DIMENSIONS` (default `256`)
  - `BEDROCK_MAX_RPM`, `BEDROCK_MAX_TOKENS_PER_MINUTE`, `PROCESSING_BATCH_SIZE` (tuning)
- Outputs:
  - `LANCEDB_DATA_KEY`: S3 key for main database (defaulted if not set)
  - `LANCEDB_MINIFIED_KEY`: S3 key for minified database (defaulted if not set)
  - `STATS_S3_KEY`: S3 key for stats JSON (defaulted if not set)
  - `NODE_S3_PREFIX`: Prefix for node files (default `nodes/`), `CLEAR_EXISTING_NODES` (default `true`), `NODE_S3_MAX_WORKERS` (default `10`)
- Layer publishing (optional):
  - `PUBLISH_LAYER`: Set to `true` to publish
  - `LAYER_ARN`: Target Lambda layer ARN (requires `LANCEDB_MINIFIED_KEY` and artifacts bucket)

## Build (Local)

From repo root:
- Evaluator: `docker build -t fdnix/nixpkgs-evaluator packages/containers/nixpkgs-evaluator`
- Processor: `docker build -t fdnix/nixpkgs-processor packages/containers/nixpkgs-processor`

## Run (Local)

Stage 1 — Evaluator (writes `.jsonl.br` to S3):
- `docker run --rm -e AWS_REGION=us-east-1 -e ARTIFACTS_BUCKET=fdnix-artifacts -e JSONL_OUTPUT_KEY=evaluations/$(date +%s)/nixpkgs-raw.jsonl fdnix/nixpkgs-evaluator`

Stage 2 — Processor (reads `.jsonl.br`, writes artifacts, stats, and node files):
- `docker run --rm -e AWS_REGION=us-east-1 -e ARTIFACTS_BUCKET=fdnix-artifacts -e PROCESSED_FILES_BUCKET=fdnix-processed -e JSONL_INPUT_KEY=evaluations/<ts>/nixpkgs-raw.jsonl.br -e PROCESSING_MODE=both -e ENABLE_EMBEDDINGS=false fdnix/nixpkgs-processor`

To generate embeddings, set `ENABLE_EMBEDDINGS=true` and configure Bedrock variables as needed.

## Deployment Notes

- Push images to ECR (examples):
  - `aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com`
  - `docker tag fdnix/nixpkgs-evaluator $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-evaluator:latest`
  - `docker tag fdnix/nixpkgs-processor $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-processor:latest`
  - `docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-evaluator:latest`
  - `docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-processor:latest`
- Step Functions coordinates the two tasks and may publish/update the Lambda layer using the minified artifact.

## Outputs and Schema

- Main dataset: `fdnix-data.lancedb` (complete metadata; embeddings when generated)
- Minified dataset: `fdnix.lancedb` (essential columns + FTS; optimized for Lambda)
- Node JSON files: per-package metadata + dependency details under `nodes/`
- Stats JSON: aggregate dependency graph metrics under `stats/`

See subfolder READMEs for detailed usage and troubleshooting:
- `packages/containers/nixpkgs-evaluator/README.md`
- `packages/containers/nixpkgs-processor/README.md`
