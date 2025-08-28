# fdnix Search Lambda (Rust)

Rust AWS Lambda for fdnix hybrid search over nixpkgs. Serves a simple HTTP API (API Gateway → Lambda) that performs:

- Hybrid ranking: LanceDB FTS (BM25) + semantic vectors (ANN)
- Optional real-time query embeddings via AWS Bedrock Runtime
- Reciprocal Rank Fusion (RRF) to combine FTS and vector results

This package was migrated from C++ to Rust. See MIGRATION.md for the full rationale and details.

## What Changed (C++ → Rust)

- Simplified builds: single `cargo build` replaces CMake + vcpkg
- Portable binaries: musl-static `bootstrap` runs in scratch/al2023
- Better DX: `cargo test`, `cargo fmt`, `cargo clippy`
- Same behavior: API, query flow, env config, and health checks preserved

## Package Structure

- `src/main.rs`: Lambda runtime, routing, health check
- `src/lancedb_client.rs`: LanceDB integration — vector search, full‑text search, hybrid (RRF), and fallbacks
- `src/bedrock_client.rs`: Bedrock Runtime client for embeddings
- `Cargo.toml`: Rust project config (AWS SDK, LanceDB, Arrow, Tokio)
- `build.rs`: Simplified (no database‑specific build flags)
- `flake.nix`: Reproducible build (optional) and Lambda package assembly
- `package.json`: npm scripts that wrap cargo for monorepo workflows
- `test-event.json`: Sample API Gateway event for testing

## Runtime Model

- Runtime: `provided.al2023` (custom runtime), binary named `bootstrap`
- Database: LanceDB database opened from a filesystem path or URI provided via env
- Tables: `packages` (required). When embeddings are present, a `vector` column stores package embeddings
- Search Flow:
  - FTS: BM25 via LanceDB FTS over package metadata
  - Vector: ANN over the `vector` column
  - Fusion: Reciprocal Rank Fusion (RRF) merges both lists when embeddings are enabled

## API

- `GET /v1/search`
  - Query params: `q` (string, required for search), `limit` (int), `offset` (int), `license` (string), `category` (string)
  - When `ENABLE_EMBEDDINGS` is enabled, the query is embedded via Bedrock and hybrid search runs; otherwise FTS-only
  - Response: JSON with `packages[]`, `totalCount`, `queryTimeMs`, and `searchType` (`hybrid` or `fts`)

- `GET /v1/search` with no `q`
  - Returns a health/status payload including client initialization and basic env hints

## Configuration (env)

- `LANCEDB_PATH`: Path or URI to the LanceDB database (e.g., a directory under `/opt/fdnix/...`) [required]
- `ENABLE_EMBEDDINGS`: `1`/`true`/`yes` to enable Bedrock embeddings (default: disabled)
- `AWS_REGION`: Region for Bedrock (defaults to Lambda region or `us-east-1`)
- `BEDROCK_MODEL_ID`: Embedding model id (default: `amazon.titan-embed-text-v2:0`)
- `BEDROCK_OUTPUT_DIMENSIONS`: Embedding dimensions (default: `256`)

## Build Options

Using Cargo (musl target):

```bash
rustup target add x86_64-unknown-linux-musl

# Release build (recommended for Lambda)
cargo build --release --target x86_64-unknown-linux-musl

# Monorepo script (copies to dist/bootstrap)
npm run build
```

Using Nix (reproducible builds):

```bash
# Build the binary
nix build .#default

# Build a deployable zip with CA certs
nix build .#lambda-package
ls -l result # contains lambda-deployment.zip and raw files

# Dev shell with the right toolchain
nix develop
```

## Deploy

- Ensure `dist/bootstrap` (or the Nix-built zip) is produced
- Deploy via CDK from `packages/cdk` (stacks expect `bootstrap` packaging)
- Resource naming should use `fdnix-` prefixes per repo guidelines

## Testing

- Unit tests: `npm run test` (runs `cargo test`)
- Sample event: see `test-event.json` (invoke in AWS Console/SAM)
- Logging: structured logs via `tracing`; health endpoint returns init state

## Nix Details

- `flake.nix` provides a reproducible build and packages a Lambda‑ready zip with CA certificates
- No external database toolchains are required; LanceDB is linked via Rust crates
- CA bundle is included to enable HTTPS calls to Bedrock

## Troubleshooting

- Embeddings disabled: Hybrid falls back to FTS; set `ENABLE_EMBEDDINGS=1`
- FTS query errors: code falls back to a LIKE-based search
- Verify database: `LANCEDB_PATH` must be set and the `packages` table should exist (with `vector` column for ANN)
- Bedrock permissions: Lambda role must allow `bedrock:InvokeModel`

## Notes

- Monorepo scripts: `npm run build` and `npm run test` are wired for CI/CD
- A debug build is useful locally, but the `build-dev` script currently copies from `release/`; adjust if you need a true debug artifact
