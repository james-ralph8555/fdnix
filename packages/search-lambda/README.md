# fdnix Search Lambda (Rust)

Rust AWS Lambda for fdnix hybrid search over nixpkgs. Serves a simple HTTP API (API Gateway → Lambda) that performs:

- Hybrid ranking: DuckDB FTS (BM25) + semantic vectors (VSS)
- Optional real-time query embeddings via AWS Bedrock Runtime
- Reciprocal Rank Fusion to combine FTS and vector results

This package was migrated from C++ to Rust. See MIGRATION.md for the full rationale and details.

## What Changed (C++ → Rust)

- Simplified builds: single `cargo build` replaces CMake + vcpkg
- Portable binaries: musl-static `bootstrap` runs in scratch/al2023
- Better DX: `cargo test`, `cargo fmt`, `cargo clippy`
- Same behavior: API, query flow, env config, and health checks preserved

## Package Structure

- `src/main.rs`: Lambda runtime, routing, health check
- `src/duckdb_client.rs`: DuckDB integration, FTS + VSS + RRF
- `src/bedrock_client.rs`: Bedrock Runtime client for embeddings
- `Cargo.toml`: Rust project config (AWS SDK, DuckDB, Tokio)
- `build.rs`: Static linking and DuckDB extension flags
- `flake.nix`: Reproducible build with a custom DuckDB (fts/vss)
- `Dockerfile`: Multi-stage build to a static `bootstrap`
- `package.json`: npm scripts that wrap cargo for monorepo workflows
- `test-event.json`: Sample API Gateway event for testing

## Runtime Model

- Runtime: `provided.al2023` (custom runtime), binary named `bootstrap`
- Database: Minified DuckDB provided via a Lambda Layer at `/opt/fdnix/fdnix.duckdb`
- Extensions: FTS/VSS built in; DB contains `packages`, `packages_fts_source`, and `embeddings`
- Search Flow:
  - FTS: BM25 over `packages_fts_source`
  - Vector: nearest neighbors over `embeddings` (VSS index)
  - Fusion: Reciprocal Rank Fusion (RRF) merges both lists

## API

- `GET /v1/search`
  - Query params: `q` (string, required for search), `limit` (int), `offset` (int), `license` (string), `category` (string)
  - When `ENABLE_EMBEDDINGS` is enabled, the query is embedded via Bedrock and hybrid search runs; otherwise FTS-only
  - Response: JSON with `packages[]`, `totalCount`, `queryTimeMs`, and `searchType` (`hybrid` or `fts`)

- `GET /v1/search` with no `q`
  - Returns a health/status payload including client initialization and basic env hints

## Configuration (env)

- `DUCKDB_PATH`: Path to DuckDB file (e.g., `/opt/fdnix/fdnix.duckdb`) [required]
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

Using Nix (reproducible, custom DuckDB with fts/vss):

```bash
# Build the binary
nix build .#default

# Build a deployable zip with CA certs
nix build .#lambda-package
ls -l result # contains lambda-deployment.zip and raw files

# Dev shell with the right toolchain
nix develop
```

Using Docker (static binary into scratch):

```bash
docker build -t fdnix-search-lambda .

# Optionally extract the bootstrap
cid=$(docker create fdnix-search-lambda)
mkdir -p dist
docker cp "$cid":/var/task/bootstrap dist/bootstrap
docker rm "$cid"
```

## Deploy

- Ensure `dist/bootstrap` (or the Nix-built zip) is produced
- Deploy via CDK from `packages/cdk` (stacks expect `bootstrap` packaging)
- Resource naming should use `fdnix-` prefixes per repo guidelines

## Testing

- Unit tests: `npm run test` (runs `cargo test`)
- Sample event: see `test-event.json` (invoke in AWS Console/SAM)
- Logging: structured logs via `tracing`; health endpoint returns init state

## Nix Details (DuckDB and Bindgen)

- `flake.nix` builds a custom DuckDB with `fts` and `vss` statically linked
- Environment config sets `LIBCLANG_PATH` and bindgen include paths
- CA bundle is added to the Lambda package for HTTPS (Bedrock)

## Migration Notes

See `MIGRATION.md` for the full C++ → Rust summary. Highlights:

- Single-toolchain builds with Cargo (no CMake/vcpkg)
- musl-static for predictable, air‑gapped deploys
- Maintains the same API behavior, search logic, and env configuration

## Troubleshooting

- Embeddings disabled: Hybrid falls back to FTS; set `ENABLE_EMBEDDINGS=1`
- FTS query errors: code falls back to a LIKE-based search
- Verify DB path: `DUCKDB_PATH` must point to `/opt/fdnix/fdnix.duckdb`
- Bedrock permissions: Lambda role must allow `bedrock:InvokeModel`

## Notes

- Monorepo scripts: `npm run build` and `npm run test` are wired for CI/CD
- A debug build is useful locally, but the `build-dev` script currently copies from `release/`; adjust if you need a true debug artifact

