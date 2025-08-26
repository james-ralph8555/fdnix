# fdnix Embedding Generator

Generates semantic embeddings for packages using AWS Bedrock and writes them into a DuckDB file, building a vector index for search. Part of the Phase 2 ingestion pipeline.

## Features

- Integrates with Bedrock `cohere.embed-english-v3` (configurable via env)
- Reads package rows from the DuckDB file produced by the metadata step
- Writes embeddings into DuckDB and builds a VSS index (`vss` extension)
- Outputs a single finalized `.duckdb` artifact for the API layer

## Environment

- `AWS_REGION` (required): Region for all AWS clients.
- `BEDROCK_MODEL_ID` (required): Bedrock model id (e.g., `cohere.embed-english-v3`).
- `ARTIFACTS_BUCKET` (optional): S3 bucket for the final DuckDB artifact.
- `DUCKDB_KEY` (optional): S3 key for the artifact (e.g., `snapshots/fdnix.duckdb`).

IAM: S3 read/write to the artifacts bucket; Bedrock `bedrock:InvokeModel`; CloudWatch Logs `/fdnix/embedding-generator`.

## Build

- `docker build -t fdnix/embedding-generator packages/containers/embedding-generator`

## Run

- `docker run --rm \
  -e AWS_REGION=us-east-1 \
  -e BEDROCK_MODEL_ID=cohere.embed-english-v3 \
  -v "$PWD":/out \
  fdnix/embedding-generator`

Requires AWS credentials with access to DynamoDB, S3, and Bedrock (InvokeModel).

## Artifact Layout

- DuckDB file contains:
  - `packages(...)` (copied from metadata phase)
  - `embeddings(package_id, vector)`
  - VSS index built over `embeddings.vector`
  - FTS index retained for keyword search

## Operational Notes

- Batch sizes: tune to model throughput; recommends 64–128 per Bedrock call when supported
- Text construction: name, version, description (+ trimmed fields) up to 2000 chars
- Retries: exponential backoff on Bedrock calls and S3 operations
- Index: created/updated after embedding batches complete
- ECS Sizing (CDK): `cpu=2048`, `memory=6144MiB`

## Logs & Troubleshooting

- Watch `/fdnix/embedding-generator` for batch progress and stats
- Common issues:
  - Bedrock throttling/permissions → ensure `bedrock:InvokeModel` and regional model access
  - Missing env vars → container exits with clear error
  - S3 permissions → verify bucket policies and quotas
  - DuckDB extensions not available → ensure `vss` and `fts` are present in the image
