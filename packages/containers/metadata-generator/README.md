# fdnix Metadata Generator

Generates normalized nixpkgs metadata and writes it into a DuckDB file. Part of the Phase 2 ingestion pipeline.

## Features

- Extracts nixpkgs via `nix-env -qaP --json --meta` (robust parsing)
- Cleans and validates fields (description/homepage/license/platforms/maintainers)
- Writes structured rows into a DuckDB file: `packages`, `packages_fts_source`
- Builds an FTS index (DuckDB `fts` extension) over relevant text
- Scales to 120k+ packages with progress logs

## Environment

- `AWS_REGION` (required): AWS region (for S3 upload if enabled).
- `ARTIFACTS_BUCKET` (optional): S3 bucket to upload intermediate/final artifacts.
- `DUCKDB_KEY` (optional): S3 key for the DuckDB artifact (e.g., `snapshots/fdnix.duckdb`).

IAM: S3 write access to the artifacts bucket; CloudWatch Logs for `/fdnix/metadata-generator`.

## Build

- `docker build -t fdnix/metadata-generator packages/containers/metadata-generator`

## Run

- `docker run --rm \
  -e AWS_REGION=us-east-1 \
  -e ARTIFACTS_BUCKET=fdnix-artifacts \
  -e DUCKDB_KEY=snapshots/fdnix.duckdb \
  -v "$PWD":/out \
  fdnix/metadata-generator`

Outputs a local DuckDB file at `/out/fdnix.duckdb`. If `ARTIFACTS_BUCKET` and `DUCKDB_KEY` are set, also uploads the artifact to S3.

## Data Model

DuckDB schema outline:
- `packages(package_id, name, version, description, homepage, license, platforms, maintainers, broken, unfree, available, mainProgram, lastUpdated)`
- `packages_fts_source(package_id, text)` with FTS index built on `text`

The embedding generator consumes this file to append embeddings and build a VSS index in a subsequent step before publishing the final artifact as a Lambda Layer.

## Operational Notes

- Performance: Streamed inserts into DuckDB with periodic checkpoints
- Nix: The image includes nix + git; no host nix required
- ECS Sizing (CDK): `cpu=1024`, `memory=3072MiB`

## Logs & Troubleshooting

- Watch `/fdnix/metadata-generator` for progress and warnings
- Common issues:
  - Missing env vars → container exits with an error
  - Network/clone failures → retried; ensure egress access in the VPC
  - FTS extension not available → ensure DuckDB `fts` can be installed/loaded in the image
