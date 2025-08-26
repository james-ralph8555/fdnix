# fdnix Metadata Generator

Generates normalized nixpkgs metadata and writes to DynamoDB. Part of the Phase 2 ingestion pipeline.

## Features

- Extracts nixpkgs via `nix-env -qaP --json` (shallow clone, robust parsing)
- Cleans and validates fields (description/homepage/license/platforms/maintainers)
- Batches writes to DynamoDB with retries and unprocessed item handling
- Scales to 120k+ packages with progress logs

## Environment

- `DYNAMODB_TABLE` (required): Target table (e.g., `fdnix-packages`).
- `AWS_REGION` (required): AWS region.

IAM: Write access to the table; CloudWatch Logs for `/fdnix/metadata-generator`.

## Build

- `docker build -t fdnix/metadata-generator packages/containers/metadata-generator`

## Run

- `docker run --rm \
  -e DYNAMODB_TABLE=fdnix-packages \
  -e AWS_REGION=us-east-1 \
  fdnix/metadata-generator`

Requires AWS credentials in the environment (or via Docker credential helpers).

## Data Model

Primary keys in DynamoDB: `packageName` (PK), `version` (SK)

Item attributes written:
- `attributePath`, `description`, `homepage`, `license`
- `platforms` (string[]), `maintainers` (string[])
- `broken` (bool), `unfree` (bool)
- `lastUpdated` (ISO string), `hasEmbedding` (bool, default false)

## Operational Notes

- Performance: Writes in batches of 25; brief pauses to reduce throttling
- Retry: Exponential backoff for retryable errors; falls back to per-item writes
- Nix: The image includes nix + git; no host nix required
- ECS Sizing (CDK): `cpu=1024`, `memory=3072MiB`

## Logs & Troubleshooting

- Watch `/fdnix/metadata-generator` for progress and warnings
- Common issues:
  - Missing env vars → container exits with an error
  - Throttling on DynamoDB → automatic retry/backoff; check table limits
  - Network/clone failures → retried; ensure egress access in the VPC
