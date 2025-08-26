# Repository Guidelines

## Project Structure & Module Organization

Planned monorepo layout (see INIT.md):

- `packages/cdk/` — AWS CDK (TypeScript) stacks (`database-stack.ts`, `pipeline-stack.ts`, `search-api-stack.ts`, `frontend-stack.ts`).
- `packages/containers/` — Data pipeline containers:
  - `metadata-generator/`
  - `embedding-generator/`
- `packages/search-lambda/` — Hybrid search API (Node.js/TypeScript).
- `packages/frontend/` — SolidJS app (SSG via Vite).
- `INIT.md` — implementation plan; `README.md` — user-facing overview.
- Note: `nixpkgs/` is vendored documentation; do not modify.

## Build, Test, and Development Commands

- Install workspaces: `pnpm -w install` (or `npm install` as fallback).
- Frontend dev: `pnpm --filter frontend dev` (Vite dev server).
- Frontend build: `pnpm --filter frontend build` (outputs `dist/`).
- CDK synth/deploy: `pnpm --filter cdk cdk synth` / `pnpm --filter cdk cdk deploy`.
- Lambda build/test: `pnpm --filter search-lambda build` / `pnpm --filter search-lambda test`.
- Containers: `docker build -t fdnix/metadata-generator packages/containers/metadata-generator` (similarly for `embedding-generator`).

## Coding Style & Naming Conventions

- TypeScript/JavaScript: 2-space indent, Prettier formatting, ESLint for linting.
- Naming: `camelCase` for vars/functions, `PascalCase` for types/classes, `kebab-case` for file/dir names.
- Commit to TypeScript where possible; keep Lambda and CDK strictly typed.

## Testing Guidelines

- Frontend: Vitest + Testing Library; name tests `*.test.ts(x)` next to sources.
- Lambda: Jest; name tests `*.test.ts` under `src/` or `__tests__/`.
- Run all tests: `pnpm -w test`. Target a minimum of smoke tests per package.

## Commit & Pull Request Guidelines

- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `perf:`.
- PRs: include description, linked issue/task from `INIT.md`, screenshots for UI changes, and rollout notes if infra touches CDK.
- Keep PRs small and focused; update `README.md`/docs when behavior changes.

## Security & Configuration Tips

- Do not commit secrets. Use AWS SSM Parameter Store/Secrets Manager.
- Principle of least privilege for IAM; CDK stacks should scope permissions narrowly.
- Local AWS access: prefer `aws-vault` or environment profiles; never hardcode keys.

## Architecture Overview

fdnix performs hybrid search over nixpkgs: semantic vectors (Faiss in S3) + keyword relevance (OpenSearch). The API (Lambda) fuses results and hydrates from DynamoDB; a SolidJS static UI queries the API. See `INIT.md` for implementation details.

