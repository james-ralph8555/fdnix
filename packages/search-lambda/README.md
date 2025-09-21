# fdnix Search Lambda (Rust)

Rust AWS Lambda for fdnix search over nixpkgs using SQLite FTS with Zstandard dictionary-based compression. Serves a simple HTTP API (API Gateway → Lambda) that performs:

- Full-text search: SQLite FTS5 with BM25 ranking
- High-compression storage: Zstandard dictionary-based decompression
- Optimized for minified, normalized database schema

This package was migrated from C++ to Rust. See MIGRATION.md for the full rationale and details.

## Package Structure

- `src/main.rs`: Lambda runtime, routing, health check
- `src/sqlite_client.rs`: SQLite FTS integration with Zstandard dictionary decompression
- `src/lib.rs`: Path detection, client initialization, and request handling
- `Cargo.toml`: Rust project config (AWS SDK, LanceDB, Arrow, Tokio)
- `build.rs`: Simplified (no database‑specific build flags)
- `flake.nix`: Reproducible build (optional) and Lambda package assembly
- `package.json`: npm scripts that wrap cargo for monorepo workflows
- `test-event.json`: Sample API Gateway event for testing

## Runtime Model

- Runtime: `provided.al2023` (custom runtime), binary named `bootstrap`
- Database: SQLite database with FTS5 virtual tables, opened from Lambda layer
- Schema: Minified schema with compressed package data and shared compression dictionary
- Search Flow:
  - FTS: BM25 via SQLite FTS5 over searchable metadata
  - Decompression: Zstandard dictionary-based decompression for full package data
  - Filtering: Support for broken/unfree package filtering

## API

- `GET /v1/search`
  - Query params: `q` (string, required for search), `limit` (int), `offset` (int), `license` (string), `category` (string)
  - When `ENABLE_EMBEDDINGS` is enabled, the query is embedded via Bedrock and hybrid search runs; otherwise FTS-only
  - Response: JSON with `packages[]`, `totalCount`, `queryTimeMs`, and `searchType` (`hybrid` or `fts`)

- `GET /v1/search` with no `q`
  - Returns a health/status payload including client initialization and basic env hints

## Configuration (env)

- Database files: `/opt/fdnix/minified.db` (SQLite database) and `/opt/fdnix/shared.dict` (compression dictionary) [required]
- `AWS_REGION`: AWS region for Lambda deployment
- Both files must be present in the Lambda layer for proper initialization

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

# Build a deployable package with CA certs (REQUIRED for CDK deployment)
nix build .#lambda-package
ls -l result # contains lambda-deployment.zip and lambda-files/ directory

# Dev shell with the right toolchain
nix develop
```

## Deploy

**Important**: For CDK deployment, you MUST use the lambda-package build:

```bash
# Required: Build the lambda package for CDK
nix build .#lambda-package

# Verify the structure CDK expects
ls -la result/lambda-files/bootstrap  # Should show 50MB executable

# Deploy from packages/cdk
cd ../cdk
cdk deploy FdnixSearchApiStack
```

The CDK stack looks for `bootstrap` at `packages/search-lambda/result/lambda-files/bootstrap`. The `lambda-package` build creates this structure with CA certificates included.

## Testing

- Unit tests: `npm run test` (runs `cargo test`)
- Sample event: see `test-event.json` (invoke in AWS Console/SAM)
- Logging: structured logs via `tracing`; health endpoint returns init state

## Nix Details

- `flake.nix` provides a reproducible build and packages a Lambda‑ready zip with CA certificates
- No external database toolchains are required; LanceDB is linked via Rust crates
- CA bundle is included to enable HTTPS calls to Bedrock

## Troubleshooting

- Lambda fails to start: Ensure both `minified.db` and `shared.dict` are present in `/opt/fdnix/`
- Dictionary loading errors: Verify the compression dictionary file exists and is readable
- FTS query errors: Check that the SQLite database contains the required `packages_kv` and `packages_fts` tables
- Decompression failures: Verify the dictionary matches the database compression settings

## Notes

- Monorepo scripts: `npm run build` and `npm run test` are wired for CI/CD
- A debug build is useful locally, but the `build-dev` script currently copies from `release/`; adjust if you need a true debug artifact
