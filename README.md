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

- Minified LanceDB: Lambda ships a minified LanceDB dataset containing only essential columns for search (name, version, attr path, description, homepage, simplified license/maintainers, flags). The full dataset is still produced for analytics/debugging and stored in S3.
- Updated Indexing Workflow: The container builds the full dataset first (e.g., `fdnix-data.lancedb`), then derives a minified dataset (`fdnix.lancedb`) from it, builds FTS/ANN indexes, and publishes the minified artifact to the Lambda layer.
- Env Configuration: `LANCEDB_DATA_KEY` stores the full dataset; `LANCEDB_MINIFIED_KEY` stores the minified dataset used by the layer.
- Infra Alignment: CDK descriptions and the layer publisher reference the minified dataset; the artifacts bucket stores both datasets using different keys.

Benefits: smaller Lambda layer, faster cold starts, improved query latency, lower storage/transfer costs, and preserved inspectability via the full database in S3.

Legacy Support Removal: Backward compatibility for `DUCKDB_KEY` has been removed. Use only:
- `LANCEDB_DATA_KEY`: S3 key/prefix for the full dataset upload
- `LANCEDB_MINIFIED_KEY`: S3 key/prefix for the minified dataset and layer publishing
- `ARTIFACTS_BUCKET` and `AWS_REGION` are required when uploading/publishing

## Search Tips

- Quotes for specifics: Use quotes to prefer tight keyword matches (e.g., `"nix fmt"`).
- Broaden with concepts: Try problem‑oriented phrases like `"markdown to pdf"` or `"http client"`.
- Mix modes naturally: The engine fuses semantic and keyword scores for you — start broad, then filter.

## How It Works (High‑Level)

- Frontend provides a fast, static UI.
- A serverless API (Rust Lambda) blends semantic understanding and keyword signals to rank results over a compact, read‑only LanceDB dataset bundled in a Lambda layer; the full dataset is retained in S3 for diagnostics and analytics.
- Rust binary + LanceDB crates: No external database libraries in the runtime; LanceDB handles FTS (BM25) and vector ANN.
- Embeddings: both the pipeline and runtime use AWS Bedrock (Amazon Titan Embeddings) — batch for indexing, real-time for user queries.
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

# Or build manually with Docker
# docker build -t fdnix-search-lambda packages/search-lambda
# cid=$(docker create fdnix-search-lambda) && \
#   mkdir -p packages/search-lambda/dist && \
#   docker cp "$cid":/var/task/bootstrap packages/search-lambda/dist/bootstrap && \
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

- Runtime (API, Bedrock real-time):
  - `AWS_REGION`: Lambda region (auto-set) used for Bedrock Runtime.
  - `BEDROCK_MODEL_ID`: Embedding model id (default: `amazon.titan-embed-text-v2:0`).
  - `BEDROCK_OUTPUT_DIMENSIONS`: Embedding dimensions (default: `256`).

- Pipeline (Bedrock batch):
  - `BEDROCK_MODEL_ID` (default: `amazon.titan-embed-text-v2:0`)
  - `BEDROCK_OUTPUT_DIMENSIONS` (default: `256`)
  - `BEDROCK_ROLE_ARN` (required for batch inference)
  - `BEDROCK_INPUT_BUCKET` and `BEDROCK_OUTPUT_BUCKET` (or a single `ARTIFACTS_BUCKET`)
  - `BEDROCK_BATCH_SIZE` (default: `50000`)
  - `BEDROCK_POLL_INTERVAL` (default: `60`)
  - `BEDROCK_MAX_WAIT_TIME` (default: `7200`)

Secrets: No external API keys required for embeddings. Store any sensitive values in AWS SSM Parameter Store or Secrets Manager. IAM policies across stacks follow least-privilege.

### Backend Details
- For backend and infrastructure details, see:
  - `packages/search-lambda/README.md`
  - `packages/cdk/README.md`

### Project Structure
- Monorepo with workspaces under `packages/`:
  - `cdk/` (AWS CDK in TypeScript)
  - `containers/` (unified `nixpkgs-indexer/` container for metadata → embeddings → minified + optional layer publish)
- `search-lambda/` (Rust Lambda backend)
  - `frontend/` (SolidJS)
- CDK commands must be run from the `packages/cdk` workspace
- Deployment uses AWS CDK; the frontend is served via S3 + CloudFront

Container notes: The previous separate `metadata-generator` and `embedding-generator` images have been replaced by a single `nixpkgs-indexer` image that runs a three-phase pipeline: metadata → embeddings → minified. The minified LanceDB dataset is uploaded to S3 and used by the Lambda layer; the container can optionally publish the layer in the same ECS task. Embeddings are generated via AWS Bedrock batch (Amazon Titan) in the pipeline. See `packages/containers/README.md` and `packages/containers/nixpkgs-indexer/README.md`.

If you want to track progress or help prioritize features, check `INIT.md` and open an issue.



## Custom Domain (Cloudflare)

- DNS: Managed in Cloudflare. Point `fdnix.com` and `www` to the CloudFront distribution domain using CNAMEs (Cloudflare will CNAME‑flatten the apex record).
- TLS: CloudFront uses an ACM certificate in `us-east-1`. The CDK requests a DNS‑validated certificate when a domain is provided; add the ACM validation CNAMEs to Cloudflare to complete issuance, then traffic will serve over HTTPS.
- Settings: In Cloudflare, set SSL/TLS mode to “Full (strict)”.

See `packages/cdk/README.md` for step‑by‑step DNS and validation instructions.

## Roadmap

- Migrate builds from Docker to Nix for reproducible, lightweight development and CI.
