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

## Jamstack + CloudFront/API Gateway

- Single domain: Use one CloudFront distribution for both the static site and the API. Route `/` to S3 and `/api/*` to API Gateway.
- Cross‑stack link: Export the API Gateway endpoint from the Search API stack and reference it in the Frontend stack (already wired via `RestApiOrigin`).
- Frontend calls: Prefer relative paths (`/api/search`) over hardcoded URLs. Build‑time env can set `VITE_API_BASE_PATH=/api` and the client can default to `/api`.
- Cloudflare DNS: Point `fdnix.com` and `www` to the CloudFront domain via CNAME flattening. ACM cert must be in `us-east-1`.
- Security hardening: Inject a secret header at CloudFront to the API origin and validate it with an API Gateway Lambda Authorizer so only requests via your distribution are allowed. For local dev, use a separate dev API (or API key).

## Project Status

- Status: v0.0.1 (early alpha). Core search works; UI and filters are basic and will evolve quickly.

## v0.0.1 Highlights

- Hybrid search path in place: LanceDB FTS (BM25) + optional semantic vectors (ANN) via Bedrock when `ENABLE_EMBEDDINGS=1`.
- Basic filters: license and category, plus UI toggles for broken/unfree (API supports them; UI wiring is partial).
- Copyable commands: install and temporary shell snippets per result.
- Typical search latency: 300–700 ms per query (baseline to improve).
- Minified LanceDB dataset shipped via Lambda layer for faster cold starts and lower transfer.

## Roadmap (P07 — fdnix)

Quick Wins
- Faceted filters: platform (x86_64-linux/darwin/aarch64), license, broken/insecure, maintainers.
  - Status: license filter done; category present. Broken/unfree toggles exist in UI but not sent to API yet. Platforms/maintainers are displayed on results but not yet filterable.
- Reverse-deps & “who uses this?” (count + list with attrpaths).
  - Status: planned.
- Compare view: select 2–3 packages → versions, size, deps, build inputs, platforms side-by-side.
  - Status: planned.
- Copyable snippets: nix shell -p, nix run, flake.nix output template.
  - Status: partial. Implemented `nix-env -iA` (install) and `nix-shell -p` (temporary shell) commands for users. To add: `nix shell -p`, `nix run`, flake template. Note: Backend now uses `nix-eval-jobs` for data extraction.

Medium
- Semantic search over description/readme/maintainers using the LanceDB setup; rerank by attrpath exact matches.
  - Status: hybrid search implemented with LanceDB (FTS + vector) when embeddings enabled. Attrpath exact-match rerank: planned.
- Evals: add an evaluation harness and small gold set to measure relevance, regressions, and ranking quality.
  - Status: planned.
- Update & CVE badges (NVD by package name + aliases); warn on broken/insecure flags.
  - Status: planned.
- “Recipe builder”: pick multiple packages → output a minimal flake with devShell + pre-commit hooks.
  - Status: planned.

Differentiators
- Reverse overlay diff: show what an overlay changes (added/removed/overridden attrs).
  - Status: planned.
- “Where did this come from?”: link to the exact nixpkgs file + line, with last 3 commits affecting it.
  - Status: planned.
- Offline index: download once, works fully in browser with WASM search.
  - Status: planned.

Self-Hosted
- Dockerized self-hosted setup (API + static UI).
  - Status: planned. Current container is for the indexing pipeline; add API/UI images and compose.

Infrastructure
- Frontend/API unification: ensure CloudFront `/api/*` → API Gateway stage and switch frontend to relative calls (remove hard dependency on `VITE_API_BASE_URL`).
  - Status: routing in CDK present via `RestApiOrigin`; update frontend client to default to `/api` with optional `VITE_API_BASE_PATH`.
- Cross‑stack outputs/props: expose API endpoint or object and pass to Frontend stack for origin wiring and build‑time config.
  - Status: partial (stacks already depend; tighten prop passing or outputs if needed).
- API hardening: CloudFront‑injected secret header + Lambda Authorizer on API Gateway.
  - Status: planned. Store secret in Secrets Manager; validate in authorizer; disable direct public access.
- Local/dev access: second dev API Gateway (not behind CloudFront) or API key path for localhost.
  - Status: planned. Gate dev with API key and usage plan; keep prod locked to CloudFront.

Success Metrics
- Primary: search → click → code-snippet copy rate.
- Secondary: median search latency (end-to-end).

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

### Pipeline Architecture

The data processing pipeline is orchestrated using AWS Step Functions with conditional execution paths:

- **Full Pipeline**: When no existing JSONL data is provided, runs nixpkgs evaluation followed by processing
- **Processing Only**: When JSONL input is provided, skips evaluation and processes existing data directly
- **Dynamic S3 Keys**: All output keys are generated using execution timestamps for versioning
- **Fargate Tasks**: Both evaluation and processing run as ECS Fargate tasks with HTTPS-only egress

The Step Functions state machine handles parameter passing between stages and generates timestamped S3 keys for all pipeline artifacts.

### Backend Details
- For backend and infrastructure details, see:
  - `packages/search-lambda/README.md`
  - `packages/cdk/README.md`
  - `packages/cdk/STEP_FUNCTION_USAGE.md`

### Project Structure
- Monorepo with workspaces under `packages/`:
  - `cdk/` (AWS CDK in TypeScript)
  - `containers/` (unified `nixpkgs-indexer/` container for metadata → embeddings → minified + optional layer publish)
- `search-lambda/` (Rust Lambda backend)
  - `frontend/` (SolidJS)
- CDK commands must be run from the `packages/cdk` workspace
- Deployment uses AWS CDK; the frontend is served via S3 + CloudFront

Container notes: The previous separate `metadata-generator` and `embedding-generator` images have been replaced by a single `nixpkgs-indexer` image that runs a three-phase pipeline: metadata → embeddings → minified. The minified LanceDB dataset is uploaded to S3 and used by the Lambda layer; the container can optionally publish the layer in the same ECS task. Embeddings are generated via AWS Bedrock batch (Amazon Titan) in the pipeline. See `packages/containers/README.md` and `packages/containers/nixpkgs-indexer/README.md`.

If you want to track progress or help prioritize features, please open an issue.



## Custom Domain (Cloudflare)

- DNS: Managed in Cloudflare. Point `fdnix.com` and `www` to the CloudFront distribution domain using CNAMEs (Cloudflare will CNAME‑flatten the apex record).
- TLS: CloudFront uses an ACM certificate in `us-east-1`. The CDK requests a DNS‑validated certificate when a domain is provided; add the ACM validation CNAMEs to Cloudflare to complete issuance, then traffic will serve over HTTPS.
- Settings: In Cloudflare, set SSL/TLS mode to “Full (strict)”.

See `packages/cdk/README.md` for step‑by‑step DNS and validation instructions.

## Acknowledgements

Special thanks to the [nix-eval-jobs](https://github.com/nix-community/nix-eval-jobs) project for providing an efficient tool for evaluating and extracting Nix package metadata at scale. This tool is essential to fdnix's data pipeline, enabling fast extraction of package information from the entire nixpkgs repository.

## Legal & Attribution

- Independent project: fdnix is an independent, community-built website. It is not affiliated with, sponsored by, or endorsed by the NixOS Foundation, the Nix Team, or the Nixpkgs project.
- Data source: Package names, versions, descriptions, and license metadata are derived from the Nixpkgs repository.
- Nixpkgs license: The content of Nixpkgs is licensed under the MIT License. We include the full license text on the site’s About page for easy access.
- Trademarks: “Nix”, “Nixpkgs”, and “NixOS” are trademarks managed by the NixOS Foundation. We use these names only for factual, descriptive purposes and avoid any suggestion of official status.

Quick links:
- About/License page: `/about.html`
- NixOS project: https://nixos.org
- Nixpkgs repository: https://github.com/NixOS/nixpkgs

Disclaimer of warranty and liability:
- The license information for individual software packages displayed by fdnix is provided for informational purposes only. This information is extracted from metadata in the Nixpkgs repository and is presented without any guarantee of accuracy or completeness. Users are solely responsible for ensuring their own compliance with the licenses of any software they choose to use, install, or redistribute.

Project license:
- The fdnix code in this repository is licensed under the MIT License (see `LICENSE`).
