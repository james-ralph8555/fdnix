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

- Frontend (SolidJS SSG) provides a fast, static UI.
- API (AWS Lambda via API Gateway) accepts your query and performs a hybrid search:
  - Vector search: Embeds your query and searches a Faiss index stored in S3.
  - Keyword search: Queries an OpenSearch Serverless index for textual relevance.
  - Fusion + hydrate: Combines and ranks results, then fetches full metadata from DynamoDB.
- A daily data pipeline refreshes metadata, embeddings, and indexes.

## Project Status

- Status: Early development. Core architecture and plan are defined.
- Roadmap: See `INIT.md` for the step‑by‑step implementation plan.

## Contributing

- Feedback: File issues with feature ideas or rough edges you hit while searching.
- Code: Contributions are welcome once the initial scaffolding lands. The project uses a monorepo with workspaces for infrastructure, data processing containers, the search API, and the frontend.

## Development (Heads‑Up)

While the code is being scaffolded, the following is the expected layout and tooling:

- Monorepo with workspaces under `packages/`:
  - `cdk/` (AWS CDK in TypeScript)
  - `containers/` (`metadata-generator/`, `embedding-generator/`)
  - `search-lambda/` (Node.js Lambda)
  - `frontend/` (SolidJS)
- Deployment uses AWS CDK; the frontend is served via S3 + CloudFront.

If you want to track progress or help prioritize features, check `INIT.md` and open an issue.

## Custom Domain (Cloudflare)

- DNS: Managed in Cloudflare. Point `fdnix.com` and `www` to the CloudFront distribution domain using CNAMEs (Cloudflare will CNAME‑flatten the apex record).
- TLS: CloudFront uses an ACM certificate in `us-east-1`. The CDK requests a DNS‑validated certificate when a domain is provided; add the ACM validation CNAMEs to Cloudflare to complete issuance, then traffic will serve over HTTPS.
- Settings: In Cloudflare, set SSL/TLS mode to “Full (strict)”.

See `packages/cdk/README.md` for step‑by‑step DNS and validation instructions.
