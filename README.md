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

## Search Tips

- Quotes for specifics: Use quotes to prefer tight keyword matches (e.g., `"nix fmt"`).
- Broaden with concepts: Try problem‑oriented phrases like `"markdown to pdf"` or `"http client"`.
- Mix modes naturally: The engine fuses semantic and keyword scores for you — start broad, then filter.

## How It Works (High‑Level)

- Frontend provides a fast, static UI.
- A serverless API blends semantic understanding and keyword signals to rank results over a compact, read‑only dataset bundled with the function.
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

### Backend Details
- For backend and infrastructure details, see:
  - `packages/search-lambda/README.md`
  - `packages/cdk/README.md`

### Project Structure
- Monorepo with workspaces under `packages/`:
  - `cdk/` (AWS CDK in TypeScript)
  - `containers/` (unified `nixpkgs-indexer/` container for metadata + embeddings + optional layer publish)
  - `search-lambda/` (C++ Lambda backend)
  - `frontend/` (SolidJS)
- CDK commands must be run from the `packages/cdk` workspace
- Deployment uses AWS CDK; the frontend is served via S3 + CloudFront

Container notes: The previous separate `metadata-generator` and `embedding-generator` images have been replaced by a single `nixpkgs-indexer` image that runs both phases. The container can also publish the DuckDB artifact to the Lambda layer as part of the same ECS task. See `packages/containers/README.md`.

If you want to track progress or help prioritize features, check `INIT.md` and open an issue.



## Custom Domain (Cloudflare)

- DNS: Managed in Cloudflare. Point `fdnix.com` and `www` to the CloudFront distribution domain using CNAMEs (Cloudflare will CNAME‑flatten the apex record).
- TLS: CloudFront uses an ACM certificate in `us-east-1`. The CDK requests a DNS‑validated certificate when a domain is provided; add the ACM validation CNAMEs to Cloudflare to complete issuance, then traffic will serve over HTTPS.
- Settings: In Cloudflare, set SSL/TLS mode to “Full (strict)”.

See `packages/cdk/README.md` for step‑by‑step DNS and validation instructions.

## Roadmap

- Migrate builds from Docker to Nix for reproducible, lightweight development and CI.
