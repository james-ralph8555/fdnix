# Repository Guidelines

## Project Structure & Module Organization

Planned monorepo layout (see INIT.md):

- `packages/cdk/` — AWS CDK (TypeScript) stacks (`database-stack.ts`, `pipeline-stack.ts`, `search-api-stack.ts`, `frontend-stack.ts`).
- `packages/containers/` — Data pipeline containers:
  - `metadata-generator/`
  - `embedding-generator/`
- `packages/search-lambda/` — Hybrid search API (Rust).
- `packages/frontend/` — SolidJS app (SSG via Vite).
- `INIT.md` — implementation plan; `README.md` — user-facing overview.
- Note: `nixpkgs/` is vendored documentation; do not modify.

## Build, Test, and Development Commands

- Install dependencies (from repo root): `npm install`.
- Frontend dev: `(cd packages/frontend && npm run dev)` (Vite dev server).
- Frontend build: `(cd packages/frontend && npm run build)` (outputs `dist/`).
- CDK synth/deploy: run from `packages/cdk`: `npx cdk synth` / `npx cdk deploy`.
- Lambda build/test: `(cd packages/search-lambda && npm run build)` / `(cd packages/search-lambda && npm run test)`.
- Containers: `docker build -t fdnix/nixpkgs-indexer packages/containers/nixpkgs-indexer`.

## Coding Style & Naming Conventions

- TypeScript/JavaScript: 2-space indent, Prettier formatting, ESLint for linting.
- Naming: `camelCase` for vars/functions, `PascalCase` for types/classes, `kebab-case` for file/dir names.
- Commit to TypeScript where possible for CDK/frontend; Lambda is Rust and should follow idiomatic Rust patterns with strict typing.

## Testing Guidelines

- Frontend: Vitest + Testing Library; name tests `*.test.ts(x)` next to sources.
- Lambda: Rust `cargo test` (invoked via `npm run test`).
- Run all tests (from repo root): `npm run test`. Target a minimum of smoke tests per package.

## Commit & Pull Request Guidelines

- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `perf:`.
- PRs: include description, linked issue/task from `INIT.md`, screenshots for UI changes, and rollout notes if infra touches CDK.
- Keep PRs small and focused; update `README.md`/docs when behavior changes.

## Security & Configuration Tips

- AWS resource naming: Prefix all AWS resources with `fdnix-`.
- Do not commit secrets. Use AWS SSM Parameter Store/Secrets Manager.
- Principle of least privilege for IAM; CDK stacks should scope permissions narrowly.
- Local AWS access: prefer `aws-vault` or environment profiles; never hardcode keys.

### DNS & Certificates

- DNS is managed in Cloudflare. Do not add AWS DNS resources in this project.
- CloudFront requires ACM certificates in `us-east-1`; validate via DNS by adding ACM‑provided CNAMEs in Cloudflare.
- Use CNAME flattening at the apex: point `fdnix.com` and `www` to the CloudFront distribution domain.

## Architecture Overview

fdnix performs hybrid search over nixpkgs using LanceDB for both full‑text (BM25) and vector ANN search, fused via reciprocal rank fusion in a Rust AWS Lambda. The LanceDB dataset is packaged in a Lambda layer (read‑only) and refreshed by a daily indexing pipeline. The SolidJS static UI queries the API. See `INIT.md` for implementation details.
