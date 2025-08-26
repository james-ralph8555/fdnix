INIT: fdnix - A Hybrid Search Engine for nixpkgs

This document outlines the development plan for fdnix, a serverless, JAMstack-based search engine for the Nix packages collection (nixpkgs). The goal is to provide fast, relevant, and filterable search results using a hybrid approach that combines traditional text search with modern vector-based semantic search.

Important update: The storage and query architecture now centers on a single DuckDB database file embedded in a Lambda Layer, queried directly from the search Lambda. We no longer use S3 Vectors, S3 Tables/Athena, or a Rust runtime for the search function.

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
        B --> D[/Build .duckdb/];
        D -- Input --> E{Embedding Fargate Task};
        E --> F[Amazon Bedrock];
        F --> E;
        E --> G[/Finalize .duckdb (FTS+VSS indexes)/];
        G --> H[S3 Artifacts Bucket];
        H --> I[Publish Lambda Layer Version];
    end

    subgraph "User Interaction"
        U[User] --> J[SolidJS Static Site];
        J --> K[API Gateway];
    end

    subgraph "Backend API"
        K --> L{Search Lambda (C++)};
        L --> M[[Lambda Layer: fdnix-db]];
        L --> N[Amazon Bedrock];
        M --> O[(DuckDB file: /opt/fdnix/fdnix.duckdb)];
    end

3. Technology Stack

| Category | Technology / Service |
|----------|---------------------|
| Frontend | SolidJS (with SSG) |
| Backend | AWS Lambda (C++, custom runtime) |
| API | Amazon API Gateway (REST API) |
| Infrastructure as Code | AWS CDK (TypeScript) |
| Primary Data Store | DuckDB file in a Lambda Layer (read-only) |
| Vector Embeddings | Amazon Bedrock (Cohere) |
| Vector Storage | DuckDB VSS index within the database file |
| Traditional Search | DuckDB FTS index within the database file |
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
│   └── search-lambda/        # C++ source/binary for the backend search API
│       ├── src/
│       └── package.json
├── .gitignore
├── package.json              # Root package.json for monorepo workspace
└── README.md

5. Development Roadmap & Task Breakdown
Phase 1: Foundation & Infrastructure (CDK) ✅ **COMPLETED**

    Objective: Set up the monorepo and define all core AWS resources using the CDK.

    Status: **COMPLETED** - All infrastructure stacks have been implemented with the DuckDB architecture.

    Implemented:

        ✅ Monorepo: npm workspaces configured in the root directory

        ✅ CDK App: Full TypeScript CDK application in packages/cdk

        ✅ Core Stacks Implemented:

            ✅ database-stack.ts: S3 artifacts bucket (`fdnix-artifacts`) and Lambda Layer (`fdnix-db-layer`) for DuckDB file storage

            ✅ pipeline-stack.ts: ECS Fargate cluster, ECR repositories, task definitions, and EventBridge daily trigger for data processing pipeline

            ✅ search-api-stack.ts: C++ Lambda function with custom runtime, API Gateway, and DuckDB layer attachment with Bedrock permissions

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

                Writes normalized rows into a DuckDB file (e.g., tables `packages`, `packages_fts_source`).

                Prepares data for FTS by materializing a text column and building an FTS index (DuckDB `fts` extension) over relevant fields.

        Develop Embedding Generator (embedding-generator):

            Create a Dockerfile based on a Python image with DuckDB + `vss`/`fts` extensions available.

            Write a script that:

                Runs after the metadata job, consuming the intermediate DuckDB file.

                For each package row, constructs a text document from metadata (e.g., "Package: cowsay. Version: 3.03. Description: ...").

                Calls the Amazon Bedrock API (Cohere model) to generate a vector embedding for the text document.

                Writes embeddings into the same DuckDB file (e.g., table `embeddings(package_id, vector)`), and builds/refreshes a VSS index using the DuckDB `vss` extension.

                Finalizes indexes: ensure FTS index (BM25) and VSS index (e.g., HNSW/IVF) are created and analyzable in read-only mode.

                Uploads the finalized `.duckdb` artifact to an S3 artifacts bucket.

        Publish/Attach Lambda Layer:

            A final Step Functions task (or a small Lambda) pulls the artifact from the S3 artifacts bucket and publishes a new Lambda Layer version (e.g., `fdnix-db-layer`). The search Lambda is configured to use the latest/aliased layer version. The DuckDB file is placed under `/opt/fdnix/fdnix.duckdb` in the layer.

Phase 3: Backend Search API

    Objective: Create the serverless API that performs the hybrid search.

    Tasks:

        Initialize Lambda Project (search-lambda): Scaffold a C++ Lambda targeting the custom runtime (`provided.al2023`). Package the compiled binary as `bootstrap` for deployment. Link to DuckDB (C API/C++ API) to query the database file bundled in the Lambda Layer. Use the AWS SDK for C++ to call Bedrock for runtime query embeddings.

        Implement API Handler:

            The handler will receive a GET request with a query parameter (e.g., ?q=search-term).

            Parse and sanitize the search term.

        Implement Hybrid Search Logic:

            Vector Search (VSS):

                Call Bedrock to generate an embedding for the user's search term.

                Query the DuckDB VSS index in `/opt/fdnix/fdnix.duckdb` using the embedding to retrieve top-K similar package IDs.

            Traditional Search (FTS):

                Use DuckDB FTS to query the same database file and return a list of relevant package IDs based on keyword/BM25 scoring.

            Combine & Rank:

                Combine the results from both searches (e.g., Reciprocal Rank Fusion or score normalization + weighted sum) to produce a final, ordered list of package IDs.

            Hydrate Results:

                Read full metadata for the ranked package IDs directly from DuckDB tables.

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

6. Notes on DuckDB in Lambda

    - The DuckDB file is packaged in a Lambda Layer at `/opt/fdnix/fdnix.duckdb` and opened read-only.
    - Required DuckDB extensions (`fts`, `vss`) must be either:
        - Pre-installed in the layer and LOADed at runtime, or
        - Statically compiled into the DuckDB library used by the Lambda binary.
    - The pipeline ensures indexes are built offline; the Lambda never mutates the database file.
    - Layer updates are published by the pipeline and attached to the function via an alias to avoid code deploys for data-only changes.

7. Roadmap Addendum

    Migrate builds to Nix:

        Adopt Nix-based builds and dev environments across packages to improve reproducibility and contributor onboarding. Replace Docker-centric local builds with Nix (e.g., dev shells and build scripts), keeping container images only where needed for deployment targets.
