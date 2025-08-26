# fdnix — Hybrid Search for nixpkgs

Fast, relevant, filterable search for the Nix packages collection. fdnix blends traditional keyword matching with modern semantic (vector) search to help you find the right package quickly.

## Features

- Hybrid search: Combines semantic similarity with keyword relevance for better results.
- Lightning fast: Serverless backend and a static frontend deliver low‑latency responses.
- Smart ranking: Merges results from vector and keyword search (e.g., reciprocal rank fusion).
- Fresh data: Automated daily indexing keeps package info up to date.
- Rich results: Shows name, version, description, license, and homepage when available.
- Built for exploration: Encourages discovery beyond exact names via semantic matching.
- Privacy‑friendly: Static frontend; no tracking beyond essential API access.

## Why fdnix?

- Broader recall than plain keyword search — find related packages even if you don’t know the exact name.
- Better precision through fusion of keyword and semantic signals.
- Simple, responsive UI designed to get you to the right package quickly.

## Quick Start

- Open the fdnix web app and start typing a package name, concept, or task (e.g., “http server”, “postgres”, “image processing”).
- Use the filters (license, category — as they are introduced) to narrow results.
- Click a result to view metadata and jump to the package’s homepage or documentation.

Note: If you’re looking for the implementation details and deployment plan, see `INIT.md`.

## Implementation Summary

- Complete Minified DuckDB: Lambda ships a minified DuckDB containing only essential columns for search (name, version, attr path, description, homepage, simplified license/maintainers, flags). The full metadata database is still produced for analytics/debugging and stored in S3.
- Updated Indexing Workflow: The container builds the full database first (`fdnix-data.duckdb`), then derives a minified database (`fdnix.duckdb`) from it, builds FTS, and publishes the minified artifact to the Lambda layer.
- Env Configuration: `DUCKDB_DATA_KEY` stores the full database; `DUCKDB_MINIFIED_KEY` stores the minified database used by the layer.
- Infra Alignment: CDK descriptions and the layer publisher reference the minified database; the artifacts bucket stores both DBs using different keys.

Benefits: smaller Lambda layer, faster cold starts, improved query latency, lower storage/transfer costs, and preserved inspectability via the full database in S3.

Legacy Support Removal: Backward compatibility for `DUCKDB_KEY` has been removed. Use only:
- `DUCKDB_DATA_KEY`: S3 key for the full database upload
- `DUCKDB_MINIFIED_KEY`: S3 key for the minified database and layer publishing
- `ARTIFACTS_BUCKET` and `AWS_REGION` are required when uploading/publishing

## Search Tips

- Quotes for specifics: Use quotes to prefer tight keyword matches (e.g., `"nix fmt"`).
- Broaden with concepts: Try problem‑oriented phrases like `"markdown to pdf"` or `"http client"`.
- Mix modes naturally: The engine fuses semantic and keyword scores for you — start broad, then filter.

## How It Works (High‑Level)

- Frontend provides a fast, static UI.
- A serverless API blends semantic understanding and keyword signals to rank results over a compact, read‑only minified DuckDB bundled in a Lambda layer; the full database is retained in S3 for diagnostics and analytics.
- Embeddings: pipeline uses AWS Bedrock batch (Amazon Titan Embeddings) to precompute vectors; the runtime API uses Google Gemini to embed user queries.
- A daily pipeline refreshes the dataset and rolls out updates with minimal downtime.

## Project Status

- Status: Early development. Core architecture and plan are defined.
- Roadmap: See `INIT.md` for the step‑by‑step implementation plan.

## Development & Deployment

### Quick Start
```bash
# Install dependencies (repo root)
npm install

# Build the search Lambda bootstrap (required before API deploy)
(cd packages/search-lambda && npm run build)

# Or build via Docker and extract bootstrap
# docker build -t fdnix-search-lambda packages/search-lambda
# cid=$(docker create fdnix-search-lambda) && \
#   mkdir -p packages/search-lambda/dist && \
#   docker cp "$cid":/bootstrap packages/search-lambda/dist/bootstrap && \
#   docker rm "$cid"

# Run CDK commands from the CDK folder
cd packages/cdk

# Bootstrap CDK (one-time setup)
npx cdk bootstrap

# Deploy all infrastructure
npm run deploy

# View deployment diff
npm run diff

# Generate CloudFormation templates
npm run synth
```

### Embeddings Configuration

- Runtime (API, Gemini):
  - `GEMINI_API_KEY`: API key used by the search Lambda to call the Gemini Embeddings API.
  - `GEMINI_MODEL_ID`: Embedding model id (default: `gemini-embedding-001`).
  - `GEMINI_OUTPUT_DIMENSIONS`: Embedding dimensions (default: `256`).
  - `GEMINI_TASK_TYPE`: Embedding task type (default: `SEMANTIC_SIMILARITY`).
  - Rate limits (client safeguards):
    - `GEMINI_MAX_CONCURRENT_REQUESTS` (default: `10`)
    - `GEMINI_REQUESTS_PER_MINUTE` (default: `3000`)
    - `GEMINI_TOKENS_PER_MINUTE` (default: `1000000`)
    - `GEMINI_INTER_BATCH_DELAY` seconds (default: `0.02`)

- Pipeline (Bedrock batch):
  - `BEDROCK_MODEL_ID` (default: `amazon.titan-embed-text-v2:0`)
  - `BEDROCK_OUTPUT_DIMENSIONS` (default: `256`)
  - `BEDROCK_ROLE_ARN` (required for batch inference)
  - `BEDROCK_INPUT_BUCKET` and `BEDROCK_OUTPUT_BUCKET` (or a single `ARTIFACTS_BUCKET`)
  - `BEDROCK_BATCH_SIZE` (default: `50000`)
  - `BEDROCK_POLL_INTERVAL` (default: `60`)
  - `BEDROCK_MAX_WAIT_TIME` (default: `7200`)

Secrets: Store sensitive values (e.g., `GEMINI_API_KEY`) in AWS SSM Parameter Store or Secrets Manager and inject them at deploy/runtime. IAM policies across stacks follow least-privilege.

### Backend Details
- For backend and infrastructure details, see:
  - `packages/search-lambda/README.md`
  - `packages/cdk/README.md`

### Project Structure
- Monorepo with workspaces under `packages/`:
  - `cdk/` (AWS CDK in TypeScript)
  - `containers/` (unified `nixpkgs-indexer/` container for metadata → embeddings → minified + optional layer publish)
- `search-lambda/` (C++ Lambda backend)
  - `frontend/` (SolidJS)
- CDK commands must be run from the `packages/cdk` workspace
- Deployment uses AWS CDK; the frontend is served via S3 + CloudFront

Container notes: The previous separate `metadata-generator` and `embedding-generator` images have been replaced by a single `nixpkgs-indexer` image that runs a three-phase pipeline: metadata → embeddings → minified. The minified DuckDB is uploaded to S3 and used by the Lambda layer; the container can optionally publish the layer in the same ECS task. Embeddings are generated via AWS Bedrock batch (Amazon Titan) in the pipeline. See `packages/containers/README.md` and `packages/containers/nixpkgs-indexer/README.md`.

If you want to track progress or help prioritize features, check `INIT.md` and open an issue.



## Custom Domain (Cloudflare)

- DNS: Managed in Cloudflare. Point `fdnix.com` and `www` to the CloudFront distribution domain using CNAMEs (Cloudflare will CNAME‑flatten the apex record).
- TLS: CloudFront uses an ACM certificate in `us-east-1`. The CDK requests a DNS‑validated certificate when a domain is provided; add the ACM validation CNAMEs to Cloudflare to complete issuance, then traffic will serve over HTTPS.
- Settings: In Cloudflare, set SSL/TLS mode to “Full (strict)”.

See `packages/cdk/README.md` for step‑by‑step DNS and validation instructions.

## Roadmap

- Migrate builds from Docker to Nix for reproducible, lightweight development and CI.
