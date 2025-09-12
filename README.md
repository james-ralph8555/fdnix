# fdnix — Fast Search for nixpkgs

Fast, relevant, filterable search for the Nix packages collection. fdnix helps you find the right package quickly with purpose‑built search and a clean UI.

## Features

- Text search: Relevance‑tuned keyword search across package metadata.
- Lightning fast: Static frontend + serverless API for low latency.
- Smart ranking: Prioritizes strong matches and well‑described packages.
- Fresh data: Automated daily indexing keeps package info up to date.
- Rich results: Name, version, description, license, and homepage when available.
- Privacy‑friendly: Static frontend; no tracking beyond essential API access.

## Why fdnix?

- Find packages by name, task, or capability (e.g., "http server").
- Narrow quickly with filters like license and category.
- Copy ready‑to‑use commands directly from results.

## Quick Start

- Open the fdnix web app and start typing a package name or task (e.g., "http server", "postgres").
- Use filters (license, category as available) to narrow results.
- Click a result to view metadata and jump to the homepage or docs.

## Search Tips

- Quotes for specifics: Use quotes to prefer tight matches (e.g., "nix fmt").
- Start broad, then filter: Try task‑oriented phrases like "markdown to pdf".
- Exact names: Include the exact package name if you know it.

## How It Works (High‑Level)

- Frontend: fast, static UI.
- Serverless API: ranks text‑search results over a compact, read‑only index packaged with the function.
- Daily refresh: the indexing pipeline updates data with minimal downtime.

## Jamstack + CloudFront/API Gateway

- Single domain: Use one CloudFront distribution for both the static site and the API. Route `/` to S3 and `/api/*` to API Gateway.
- Cross‑stack link: Export the API Gateway endpoint from the Search API stack and reference it in the Frontend stack (already wired via `RestApiOrigin`).
- Frontend calls: Prefer relative paths (`/api/search`) over hardcoded URLs. Build‑time env can set `VITE_API_BASE_PATH=/api` and the client can default to `/api`.
- Cloudflare DNS: Point `fdnix.com` and `www` to the CloudFront domain via CNAME flattening. ACM cert must be in `us-east-1`.
- Security hardening: Inject a secret header at CloudFront to the API origin and validate it with an API Gateway Lambda Authorizer so only requests via your distribution are allowed. For local dev, use a separate dev API (or API key).

## Project Status

- Status: v0.0.1 (early alpha). Core search works; UI and filters are basic and will evolve quickly.

## v0.0.1 Highlights

- Fast text search across nixpkgs metadata.
- Basic filters: license and category, plus UI toggles for broken/unfree (API wiring is partial).
- Copyable commands: install and temporary shell snippets per result.
- Typical search latency: 300–700 ms per query (baseline to improve).

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
- Relevance evaluations: add a small gold set to measure ranking quality and regressions.
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

### Pipeline Architecture

The data processing pipeline is orchestrated using AWS Step Functions with conditional execution paths:

- Full pipeline: when no existing JSONL data is provided, runs nixpkgs evaluation followed by processing.
- Processing only: when JSONL input is provided, skips evaluation and processes existing data directly.
- Outputs: artifacts are written to S3 and used to publish the compact search index for the API.
- Deployment: uses AWS CDK; the frontend is served via S3 + CloudFront.

Container notes: The `nixpkgs-indexer` image runs a three‑phase pipeline: metadata → index → publish. It uploads the compact search index to S3 and can optionally publish the associated Lambda layer in the same ECS task. See `packages/containers/README.md` and `packages/containers/nixpkgs-indexer/README.md`.

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

