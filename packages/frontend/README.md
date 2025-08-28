# fdnix Frontend (SolidJS + Vite)

A fast, static frontend for fdnix — a hybrid search over nixpkgs. Built with SolidJS, Vite, and Tailwind CSS. It queries the fdnix Search API (AWS Lambda + API Gateway) and renders results with filters, pagination, and copyable install commands.

## Quick Start

- Prerequisites: Node >= 18, npm >= 9
- Install deps from the repo root: `npm install`
- Configure env: `cp .env.example .env` and set `VITE_API_BASE_URL`
- Dev server: `npm run dev` (opens on `http://localhost:3000`)
- Build: `npm run build` (outputs to `dist/`)
- Preview production build: `npm run preview`

## Environment Variables

These are loaded by Vite at build/dev time.

- `VITE_API_BASE_URL` (required): Base URL for the Search API. Examples:
  - `https://api.fdnix.com/v1`
  - `https://xxxx.execute-api.us-east-1.amazonaws.com/v1`
  Trailing slash is fine; the app normalizes it.
- `VITE_DEV_MODE` (optional): Development toggle for future local-only behaviors.
- `VITE_PORT` (optional): Present in `.env.example` for reference; the dev server port is currently set in `vite.config.ts` (3000).

If `VITE_API_BASE_URL` is missing, the app will throw an explicit error on start to make misconfiguration obvious.

## Available Scripts

- `dev`: Starts Vite dev server.
- `build`: Builds a static production bundle to `dist/`.
- `preview`: Serves the production build locally for verification.
- `lint`: Runs ESLint with `@typescript-eslint` and `eslint-plugin-solid`.
- `format`: Formats the codebase with Prettier.

Scripts are defined in `package.json`. Engines are enforced (`node >= 18`, `npm >= 9`).

## Project Structure

- `index.html`: App entry HTML (no SSR; static index).
- `public/`: Static assets (e.g., `about.html`, icons). Served at the root.
- `src/`
  - `index.tsx`: App bootstrap and mounting.
  - `App.tsx`: Root UI with search, filters, pagination, and API health.
  - `components/`: UI components (`SearchInput`, `SearchResults`, `FilterPanel`, `PaginationControls`, `LoadingSpinner`).
  - `services/api.ts`: Minimal API client that uses `VITE_API_BASE_URL`. Throws on HTTP/network errors.
  - `types/`: TypeScript types that match the API payloads.
  - `utils/`: Small helpers (`format`, `package`).
- `vite.config.ts`: Vite + Solid plugin, dev server (port 3000), build output options.
- `tailwind.config.js`, `postcss.config.js`, `src/index.css`: Tailwind setup and custom utility classes.

## API Contract (used by the UI)

- `GET /health`: Liveness/health endpoint. Used to show API online/offline status in the header.
- `GET /search` with query params:
  - `q` (required): Search query string.
  - `limit`, `offset` (optional): Pagination.
  - `license`, `category` (optional): Basic filtering.

The expected response shapes are defined in `src/types/index.ts`.

## Development Notes

- The dev server runs on `http://localhost:3000` and expects the API to allow CORS from the browser.
- Debounced search: input triggers queries after a short delay; hitting Enter triggers immediately.
- Install commands: each result offers a quick copy for common `nix-env`/`nix-shell` flows.
- About and licensing details are available at `/about.html` in `public/`.

## Testing

This package doesn’t include tests yet. The repository standard is Vitest + Testing Library for the frontend (naming `*.test.ts(x)` next to sources). If you add tests here, please wire a `test` npm script and follow the repo’s testing guidelines.

## Linting & Formatting

- Lint: `npm run lint`
- Format: `npm run format`

ESLint is configured in `.eslintrc.cjs` with Solid and TypeScript rules; Prettier handles formatting.

## Deployment

- The build is completely static (`dist/`) and can be hosted on any static host.
- In this repo, the recommended path is S3 + CloudFront managed by CDK (`packages/cdk`, see `frontend-stack.ts`).
- Set `VITE_API_BASE_URL` appropriately at build time for the target environment.
- DNS is managed in Cloudflare. For AWS:
  - Use ACM certificates in `us-east-1` for CloudFront.
  - Validate via DNS (add ACM CNAMEs in Cloudflare).
  - Use CNAME flattening at the apex (e.g., `fdnix.com` → CloudFront domain).

Security notes from the repo apply: never commit secrets (these `VITE_` vars are public build-time config), use least-privilege IAM, and prefix AWS resources `fdnix-`.

## Troubleshooting

- API offline indicator: Check the `/health` endpoint and your `VITE_API_BASE_URL` value. Confirm CORS for browser access.
- Missing API base URL: Set `VITE_API_BASE_URL` in `.env` or the build environment.
- Dev port: The port is set in `vite.config.ts` (3000). Update there if you need a different port.
- Empty results: Try broader queries; ensure the API is reachable; check browser console for network errors.

## Contributing

- Follow Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, etc.).
- Keep PRs focused; include screenshots for UI changes.
- Update docs when behavior changes. See repo root `README.md` and `INIT.md` for broader context.

## License & Attribution

- Project code: MIT (see repository root `LICENSE`).
- Package data originates from the Nixpkgs repository (MIT). See `/about.html` for attribution and disclaimers.

