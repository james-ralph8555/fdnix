INIT: fdnix - A Hybrid Search Engine for nixpkgs

This document outlines the development plan for fdnix, a serverless, JAMstack-based search engine for the Nix packages collection (nixpkgs). The goal is to provide fast, relevant, and filterable search results using a hybrid approach that combines traditional text search with modern vector-based semantic search.

Important update: The storage and query architecture now centers on a minified LanceDB dataset embedded in a Lambda Layer, queried directly from a Rust search Lambda. The pipeline also produces a full LanceDB dataset for analytics/debugging stored in S3. We no longer use the prior DuckDB-based runtime; all functionality remains intact with LanceDB providing FTS (BM25) and vector ANN.

Implementation Summary (Minified Dataset)

- Minified dataset builder: Constructs a stripped‑down LanceDB dataset with only essential columns for search and presentation. License and maintainer data are flattened to strings. Non‑essential metadata fields (e.g., positions, outputsToInstall, lastUpdated, content_hash) are omitted. FTS (BM25) and vector indexes are prepared.
- Indexing Workflow: The pipeline generates the full dataset first (e.g., `fdnix-data.lancedb`) and uploads it; then creates the minified dataset (`fdnix.lancedb`) and uploads it; embeddings are generated against the minified dataset; the Lambda layer is published from the minified artifact.
- Environment Variables: `LANCEDB_DATA_KEY` (full dataset key/prefix), `LANCEDB_MINIFIED_KEY` (minified dataset key/prefix used by the Lambda layer).
- Infrastructure: CDK descriptions and the layer publisher reference the minified dataset; the artifacts bucket stores both datasets at different keys.

Legacy Support Removal

- Removed backward compatibility for `DUCKDB_KEY`. Validation and publishing now require `LANCEDB_DATA_KEY`/`LANCEDB_MINIFIED_KEY` explicitly. `LANCEDB_MINIFIED_KEY` is required for layer publishing.

This plan is designed to be executed by an autonomous coding agent. Each section details a phase of the project, broken down into specific, actionable tasks.
1. Core Principles

    Serverless First: All backend and data processing components will be built on serverless AWS services (Lambda, Lambda Layers, Fargate, S3, API Gateway) to minimize operational overhead and ensure scalability.

    Infrastructure as Code (IaaC): The entire cloud infrastructure will be defined, versioned, and deployed using the AWS Cloud Development Kit (CDK) in TypeScript.

    Monorepo: The project will be managed in a single monorepo containing the frontend, backend, IaaC, and data processing container definitions. This simplifies dependency management and streamlines the CI/CD process.

    Automation: Data ingestion and index generation will be fully automated, running on a daily schedule.

    Resource Naming: Prefix all AWS resources with "fdnix-".

2. High-Level Architecture

The system is composed of three main parts: a data processing pipeline, a backend search API, and a static frontend.

graph TD
    subgraph "Data Processing Pipeline (Daily Cron)"
        A[EventBridge Cron] --> B{Metadata Fargate Task};
        B --> C[Nixpkgs GitHub Repo];
        B --> D[/Build LanceDB dataset/];
        D -- Input --> E{Embedding Fargate Task};
        E --> F[AWS Bedrock];
        F --> E;
        E --> G[/Finalize LanceDB (FTS+ANN indexes)/];
        G --> H[S3 Artifacts Bucket];
        H --> I[Publish Lambda Layer Version];
    end

    subgraph "User Interaction"
        U[User] --> J[SolidJS Static Site];
        J --> K[API Gateway];
    end

    subgraph "Backend API"
        K --> L{Search Lambda (Rust)};
        L --> M[[Lambda Layer: fdnix-db]];
        L --> N[AWS Bedrock Runtime];
        M --> O[(LanceDB path: /opt/fdnix/fdnix.lancedb)];
    end

3. Technology Stack

| Category | Technology / Service |
|----------|---------------------|
| Frontend | SolidJS (with SSG) |
| Backend | AWS Lambda (Rust, custom runtime) |
| API | Amazon API Gateway (REST API) |
| Infrastructure as Code | AWS CDK (TypeScript) |
| Primary Data Store | LanceDB dataset in a Lambda Layer (read-only) |
| Vector Embeddings | AWS Bedrock (Amazon Titan) for both pipeline (batch) and runtime (real-time) |
| Vector Storage | LanceDB vector index within the dataset |
| Traditional Search | LanceDB FTS (BM25) within the dataset |
| Data Processing | AWS Fargate |
| Orchestration | Amazon EventBridge (Cron) |
| Containerization | Docker |
| Deployment | AWS CloudFront + S3 (Frontend), CDK (Backend + Lambda Layer) |
4. Monorepo Project Structure

The project will be organized as follows:

/fdnix
├── packages/
│   ├── cdk/                  # AWS CDK Infrastructure definitions
│   │   ├── bin/
│   │   └── lib/
│   │       ├── database-stack.ts
│   │       ├── frontend-stack.ts
│   │       ├── pipeline-stack.ts
│   │       └── search-api-stack.ts
│   ├── containers/           # Dockerfiles and source for data processing
│   │   ├── metadata-generator/
│   │   │   ├── Dockerfile
│   │   │   └── src/
│   │   └── embedding-generator/
│   │       ├── Dockerfile
│   │       └── src/
│   ├── frontend/             # SolidJS application
│   │   ├── src/
│   │   └── package.json
│   └── search-lambda/        # Rust source/binary for the backend search API
│       ├── src/
│       └── package.json
├── .gitignore
├── package.json              # Root package.json for monorepo workspace
└── README.md

5. Development Roadmap & Task Breakdown
Phase 1: Foundation & Infrastructure (CDK) ✅ **COMPLETED**

    Objective: Set up the monorepo and define all core AWS resources using the CDK.

    Status: **COMPLETED** - All infrastructure stacks have been implemented with the LanceDB architecture.

    Implemented:

        ✅ Monorepo: npm workspaces configured in the root directory

        ✅ CDK App: Full TypeScript CDK application in packages/cdk

        ✅ Core Stacks Implemented:

            ✅ database-stack.ts: S3 artifacts bucket (`fdnix-artifacts`) and Lambda Layer (`fdnix-db-layer`) for LanceDB dataset storage

            ✅ pipeline-stack.ts: ECS Fargate cluster, ECR repositories, task definitions, and EventBridge daily trigger for data processing pipeline

            ✅ search-api-stack.ts: Rust Lambda function with custom runtime, API Gateway, and LanceDB layer attachment with Bedrock permissions for real-time embeddings

            ✅ frontend-stack.ts: S3 static hosting and CloudFront distribution with custom domain support

    Idempotent Deployments (CDK/CloudFormation):

        Goal: Re-running deployments produces the same infrastructure state with no duplicates or unintended changes.

        Practices:

            Use Stable Logical IDs: Do not change construct IDs for already-deployed resources; IDs map to CloudFormation Logical IDs.

            Avoid Hardcoding Physical Names: Let CDK/CloudFormation generate names (e.g., S3, DynamoDB). If a fixed name is required, ensure it is unique and stable across deploys and environments.

            Prevent Dynamic Naming Drift: Do not derive physical names from timestamps or non-deterministic values. Prefer stack name + logical ID if predictability is needed.

            Manage Drift: Treat CDK as the source of truth. Use CloudFormation drift detection before/after important changes and resolve drift by updating code or reverting manual edits.

            Review Changes: Use `cdk diff` to preview change sets and confirm updates are intentional before `cdk deploy`.

            Idempotent Custom Resources: For Lambda-backed custom resources, handle Create/Update/Delete idempotently (no duplicate side effects; succeed if resource already exists/absent).

            IAM and Policies: Prefer narrowly scoped, deterministic policies. Avoid policy documents that inject random suffixes between deploys.

Phase 2: Data Ingestion & Processing Pipeline

    Objective: Build the containerized tasks that fetch, process, and index nixpkgs data.

    Tasks:

        Develop Metadata Generator (metadata-generator):

            Create a Dockerfile based on a NixOS image (nixos/nix) or Debian + Nix.

            Write a script that:

                Clones the NixOS/nixpkgs repository.

                Uses `nix-env -qaP --json` or a similar command to extract metadata for all packages (name, version, description, homepage, license).

                Writes normalized rows into a LanceDB dataset (e.g., table `packages`).

                Prepares data for FTS by materializing a text column and building an FTS (BM25) index over relevant fields.

        Develop Embedding Generator (embedding-generator):

            Create a Dockerfile based on a Python image with LanceDB and Arrow libraries available.

            Write a script that:

                Runs after the metadata job, consuming the intermediate LanceDB dataset.

                For each package row, constructs a text document from metadata (e.g., "Package: cowsay. Version: 3.03. Description: ...").

                Submits a Bedrock batch inference job (Amazon Titan Embeddings) to generate 256‑dimensional vectors; manages S3 input/output prefixes and polling.

                Writes embeddings into the same LanceDB dataset (e.g., a `vector` column on `packages`), and builds/refreshes an ANN index.

                Finalizes indexes: ensure FTS index (BM25) and VSS index (e.g., HNSW/IVF) are created and analyzable in read-only mode.

                Uploads the finalized LanceDB dataset directory to an S3 artifacts bucket.

        Publish/Attach Lambda Layer:

            A final Step Functions task (or a small Lambda) pulls the artifact from the S3 artifacts bucket and publishes a new Lambda Layer version (e.g., `fdnix-db-layer`). The search Lambda is configured to use the latest/aliased layer version. The LanceDB dataset is placed under `/opt/fdnix/fdnix.lancedb` in the layer.

Phase 3: Backend Search API

    Objective: Create the serverless API that performs the hybrid search.

    Tasks:

        Initialize Lambda Project (search-lambda): Scaffold a Rust Lambda targeting the custom runtime (`provided.al2023`). Package the compiled binary as `bootstrap` for deployment. Use LanceDB via Rust crates to query the dataset bundled in the Lambda Layer. Use AWS SDK for Rust (Bedrock Runtime) to generate query embeddings in real time.

        Implement API Handler:

            The handler will receive a GET request with a query parameter (e.g., ?q=search-term).

            Parse and sanitize the search term.

        Implement Hybrid Search Logic:

            Vector Search (VSS):

                Call AWS Bedrock Runtime to generate an embedding for the user's search term.

                Query the LanceDB vector index in `/opt/fdnix/fdnix.lancedb` using the embedding to retrieve top-K similar package IDs.

            Traditional Search (FTS):

                Use LanceDB FTS to query the same dataset and return a list of relevant package IDs based on keyword/BM25 scoring.

            Combine & Rank:

                Combine the results from both searches (e.g., Reciprocal Rank Fusion or score normalization + weighted sum) to produce a final, ordered list of package IDs.

            Hydrate Results:

                Read full metadata for the ranked package IDs directly from LanceDB tables.

                Return the results as a JSON array.

Phase 4: Frontend Application

    Objective: Build a clean, fast, and responsive user interface for searching packages.

    Tasks:

        Initialize SolidJS Project (frontend): Set up a new SolidJS project configured for Static Site Generation (SSG). Use a tool like Vite.

        Build UI Components:

            A prominent, centered search bar on the main page.

            A results view to display a list of returned packages.

            UI elements for filters (e.g., by license, category - to be added later).

        Implement State Management: Use SolidJS signals to manage the application state (search query, loading status, search results, errors).

        API Integration: Create a function to call the backend API Gateway endpoint. Handle asynchronous operations gracefully.

        Styling: Use a modern CSS framework (e.g., Tailwind CSS) to style the application, ensuring it is fully responsive.

        Deployment: Configure the build process to generate a static site in a dist folder, which will be deployed by the CDK.

6. Notes on LanceDB in Lambda

    - The LanceDB dataset is packaged in a Lambda Layer at `/opt/fdnix/fdnix.lancedb` and opened read‑only.
    - No external database extensions are required in the runtime; LanceDB capabilities are provided by crates and prebuilt indexes in the dataset.
    - The pipeline ensures indexes are built offline; the Lambda never mutates the dataset.
    - Layer updates are published by the pipeline and attached to the function via an alias to avoid code deploys for data‑only changes.

7. Roadmap Addendum

    Migrate builds to Nix:

        Adopt Nix-based builds and dev environments across packages to improve reproducibility and contributor onboarding. Replace Docker-centric local builds with Nix (e.g., dev shells and build scripts), keeping container images only where needed for deployment targets.

    Metadata fidelity (TODO):

        Minified dataset currently simplifies `license` and `maintainers`. Bring back richer metadata extraction (arrays/objects → concise strings) once a stable approach is validated, preferably by pre‑flattening during metadata ingestion to keep queries simple and the Lambda layer robust.

    Runtime and packaging optimizations (TODO):

        Optimize the Rust binary size and cold‑start (e.g., LTO, strip symbols) and right‑size the LanceDB dataset and indexes to minimize layer size while preserving FTS/ANN functionality.
