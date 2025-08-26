# fdnix-search-api (C++)

Planned C++ implementation of the fdnix hybrid search Lambda.

- Runtime: AWS Lambda custom runtime (`provided.al2023`).
- Packaging: Compile to a binary named `bootstrap` and zip for upload.
- Query Engine: DuckDB opened read-only from a Lambda Layer (see below).
- SDKs/Libraries: AWS SDK for C++ (API Gateway events, Bedrock), DuckDB (C/C++ API), minimal HTTP handler.

Current status: A minimal Node.js handler may be deployed as a temporary stub to keep CDK wiring and API Gateway in place. It will be replaced by the C++ `bootstrap` binary.

## Runtime Data Model

- Lambda Layer: `fdnix-db-layer` provides `/opt/fdnix/fdnix.duckdb`.
- The `fdnix.duckdb` file contains:
  - `packages(...)` with full metadata
  - `packages_fts_source(...)` and FTS index (BM25)
  - `embeddings(package_id, vector)` with VSS index (e.g., HNSW/IVF)

## Request Handling

- `GET /v1/search?q=<query>`
  - Embed `q` using Bedrock (e.g., `cohere.embed-english-v3`) via AWS SDK for C++.
  - Run two queries against `/opt/fdnix/fdnix.duckdb`:
    - VSS: nearest neighbors over `embeddings.vector` by the query embedding
    - FTS: BM25 over the FTS index from `packages_fts_source`
  - Fuse results (e.g., RRF or normalized weighted sum), then join to `packages` for metadata.
  - Return JSON array of results with scores.

## Build Outline (to be implemented)

- Build inside Amazon Linux 2023 to match `provided.al2023` glibc.
- Link against DuckDB and AWS SDK for C++ (Bedrock + core deps).
- Produce `dist/bootstrap` binary:
  - Example approach (pseudo):
    - `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release`
    - `cmake --build build --target bootstrap -j`
    - `strip build/bootstrap && cp build/bootstrap dist/bootstrap`

## Best Practices for Building

- Match Lambda environment:
  - Use Amazon Linux 2023 Docker image for reproducible builds and glibc compatibility.
- Optimize size and cold start:
  - Build with `-O2/-Os`, link-time optimization (LTO) where feasible, and strip symbols.
- Verify output:
  - Ensure `dist/bootstrap` exists and is executable before running CDK deploy.

## DuckDB Extensions in Lambda

- The pipeline prebuilds FTS/VSS indexes in the DuckDB file.
- If runtime queries require loading extensions, bundle them in the layer and `LOAD` at startup (or statically compile them into the DuckDB library used by the function).

## Deploy Flow

- Build (from repo root): `(cd packages/search-lambda && npm run build)`
- Deploy (from CDK folder): `(cd packages/cdk && npm run deploy)`
