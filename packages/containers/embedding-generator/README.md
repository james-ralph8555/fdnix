# fdnix Embedding Generator

Generates semantic embeddings for packages using AWS Bedrock and stores vectors in S3 with an index for search. Part of the Phase 2 ingestion pipeline.

## Features

- Integrates with Bedrock `cohere.embed-english-v3` (configurable via env)
- Scans DynamoDB for packages without embeddings; incremental by default
- Writes vectors as compressed batches to S3 and builds an index file
- Updates DynamoDB items to set `hasEmbedding = true`

## Environment

- `DYNAMODB_TABLE` (required): Package metadata table (e.g., `fdnix-packages`).
- `S3_BUCKET` (required): Bucket for vectors and index (e.g., `fdnix-vec`).
- `AWS_REGION` (required): Region for all AWS clients.
- `BEDROCK_MODEL_ID` (required): Bedrock model id (CDK uses `cohere.embed-english-v3`).

IAM: Read/Write to the table and bucket; Bedrock `bedrock:InvokeModel`; CloudWatch Logs `/fdnix/embedding-generator`.

## Build

- `docker build -t fdnix/embedding-generator packages/containers/embedding-generator`

## Run

- `docker run --rm \
  -e DYNAMODB_TABLE=fdnix-packages \
  -e S3_BUCKET=fdnix-vec \
  -e AWS_REGION=us-east-1 \
  -e BEDROCK_MODEL_ID=cohere.embed-english-v3 \
  fdnix/embedding-generator`

Requires AWS credentials with access to DynamoDB, S3, and Bedrock (InvokeModel).

## Storage Layout in S3

- Vectors prefix: `vectors/`
  - Files: `vectors/batch_{index}_{count}.json.gz`
  - Content: `{ vectors: [{ id, vector: number[], metadata }], count, dimension }`
- Index object: `vector-index/index.json.gz`
  - Summarizes total vectors, dimension, and batch files with sizes
  - Metadata headers on the S3 object mirror counts/dimensions

## Operational Notes

- Batch sizes: 50 packages per embedding batch; 100 vectors per S3 batch file
- Text construction: name, version, description (+ trimmed fields) up to 2000 chars
- Retries: exponential backoff on Bedrock calls and S3/DynamoDB operations
- Index: created/updated after embedding batches complete
- ECS Sizing (CDK): `cpu=2048`, `memory=6144MiB`

## Logs & Troubleshooting

- Watch `/fdnix/embedding-generator` for batch progress and stats
- Common issues:
  - Bedrock throttling/permissions → ensure `bedrock:InvokeModel` and regional model access
  - Missing env vars → container exits with clear error
  - S3/DynamoDB throttling → automatic retry/backoff; verify bucket/table policies and quotas
