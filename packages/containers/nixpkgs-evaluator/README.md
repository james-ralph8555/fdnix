# nixpkgs-evaluator

Stage 1 of the fdnix pipeline. Runs `nix-eval-jobs` against `nixpkgs` and uploads a brotli-compressed JSONL file to S3 that the Stage 2 processor consumes.

## What it does

- Clones `nixpkgs` (release-25.05) with shallow depth.
- Executes `nix-eval-jobs` to enumerate packages and metadata.
- Prepends a metadata header line to the JSONL stream (timestamp, branch, count).
- Compresses output with brotli and uploads to S3.

## Environment

- `AWS_REGION` (required): AWS region for S3.
- `ARTIFACTS_BUCKET` (required): S3 bucket to upload the JSONL.
- `JSONL_OUTPUT_KEY` (optional): Base key for output; defaults to `evaluations/<ts>/nixpkgs-raw.jsonl`.
  - Note: the uploaded object is suffixed with `.br` (e.g., `.../nixpkgs-raw.jsonl.br`).

## Build

From repo root:
- `docker build -t fdnix/nixpkgs-evaluator packages/containers/nixpkgs-evaluator`

## Run (local)

Uploads to S3 as `.jsonl.br`:
- `docker run --rm \
    -e AWS_REGION=us-east-1 \
    -e ARTIFACTS_BUCKET=fdnix-artifacts \
    -e JSONL_OUTPUT_KEY=evaluations/$(date +%s)/nixpkgs-raw.jsonl \
    fdnix/nixpkgs-evaluator`

## Output format

- First line: `{ "_metadata": { ... } }` with `extraction_timestamp`, `nixpkgs_branch`, and `total_packages`.
- Subsequent lines: one JSON object per package from `nix-eval-jobs`.
- Content Encoding: `br` (brotli). Content Type: `application/jsonl`.

## Notes

- Requires substantial CPU and memory (e.g., 8 vCPU / 48 GB RAM) for reliable evaluation.
- Temporary clone & work dirs are cleaned up after completion.
