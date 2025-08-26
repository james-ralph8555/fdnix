INIT: fdnix - A Hybrid Search Engine for nixpkgs

This document outlines the development plan for fdnix, a serverless, JAMstack-based search engine for the Nix packages collection (nixpkgs). The goal is to provide fast, relevant, and filterable search results using a hybrid approach that combines traditional text search with modern vector-based semantic search.

This plan is designed to be executed by an autonomous coding agent. Each section details a phase of the project, broken down into specific, actionable tasks.
1. Core Principles

    Serverless First: All backend and data processing components will be built on serverless AWS services (Lambda, Fargate, DynamoDB, S3, API Gateway) to minimize operational overhead and ensure scalability.

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
        B --> D[DynamoDB Table];
        D -- Triggers --> E{Embedding Fargate Task};
        E --> F[Amazon Bedrock];
        F --> E;
        E --> G[S3 Bucket for Vector Index];
        E --> H[OpenSearch Serverless Index];
    end

    subgraph "User Interaction"
        I[User] --> J[SolidJS Static Site];
        J --> K[API Gateway];
    end

    subgraph "Backend API"
        K --> L{Search Lambda};
        L --> G;
        L --> H;
        L --> D;
    end

3. Technology Stack

Category
	

Technology / Service

Frontend
	

SolidJS (with SSG)

Backend
	

AWS Lambda (Node.js)

API
	

Amazon API Gateway (REST API)

Infrastructure as Code
	

AWS CDK (TypeScript)

Primary Data Store
	

Amazon DynamoDB

Vector Embeddings
	

Amazon Bedrock (Cohere)

Vector Index
	

Faiss index file stored in Amazon S3

Traditional Search
	

Amazon OpenSearch Serverless

Data Processing
	

AWS Fargate

Orchestration
	

Amazon EventBridge (Cron)

Containerization
	

Docker

Deployment
	

AWS CloudFront + S3 (Frontend), CDK (Backend)
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
│   └── search-lambda/        # Source code for the backend search API
│       ├── src/
│       └── package.json
├── .gitignore
├── package.json              # Root package.json for monorepo workspace
└── README.md

5. Development Roadmap & Task Breakdown
Phase 1: Foundation & Infrastructure (CDK)

    Objective: Set up the monorepo and define all core AWS resources using the CDK.

    Tasks:

        Initialize Monorepo: Set up a pnpm or npm workspace in the root directory.

        Initialize CDK App: Inside packages/cdk, initialize a new AWS CDK app in TypeScript.

        Define Core Stacks:

            database-stack.ts: Define the DynamoDB table with packageName (String, Hash Key) and version (String, Sort Key). Define the S3 bucket for vector indexes. Define the OpenSearch Serverless Collection.

            pipeline-stack.ts: Define IAM Roles for Fargate tasks. Define ECR repositories for the two containers. Define the Fargate Cluster, Task Definitions, and the EventBridge rule to trigger the pipeline daily.

            search-api-stack.ts: Define the Search Lambda function, the API Gateway endpoint, and the necessary IAM permissions for the Lambda to access DynamoDB, S3, and OpenSearch.

            frontend-stack.ts: Define the S3 bucket for static site hosting and the CloudFront distribution.

Phase 2: Data Ingestion & Processing Pipeline

    Objective: Build the containerized tasks that fetch, process, and index nixpkgs data.

    Tasks:

        Develop Metadata Generator (metadata-generator):

            Create a Dockerfile based on a NixOS image (nixos/nix).

            Write a script that:

                Clones the NixOS/nixpkgs repository.

                Uses nix-env -qaP --json or a similar command to extract metadata for all packages (name, version, description, homepage, license).

                Uses the AWS SDK to batch-write this metadata into the DynamoDB table.

        Develop Embedding Generator (embedding-generator):

            Create a Dockerfile based on a Python image.

            Write a script that:

                Is triggered after the metadata job. It should scan DynamoDB for items that need embedding.

                For each package, it constructs a text document from its metadata (e.g., "Package: cowsay. Version: 3.03. Description: A program which generates ASCII pictures of a cow with a message.").

                Calls the Amazon Bedrock API (Cohere model) to generate a vector embedding for the text document.

                Writes the document and its metadata to the OpenSearch Serverless index.

                Collects all vector embeddings and package identifiers.

                Builds a Faiss index from the embeddings.

                Saves the serialized Faiss index file to the designated S3 bucket.

Phase 3: Backend Search API

    Objective: Create the serverless API that performs the hybrid search.

    Tasks:

        Initialize Lambda Project (search-lambda): Set up a Node.js project with dependencies for AWS SDK, Faiss-node, and OpenSearch.

        Implement API Handler:

            The handler will receive a GET request with a query parameter (e.g., ?q=search-term).

            Parse and sanitize the search term.

        Implement Hybrid Search Logic:

            Vector Search:

                Call Bedrock to generate an embedding for the user's search term.

                On cold start, download the Faiss index from S3 to the Lambda's /tmp storage.

                Load the index and perform a k-NN search to get the top N most semantically similar package IDs.

            Traditional Search:

                Query the OpenSearch index with the user's search term to get a list of relevant package IDs based on keyword matching.

            Combine & Rank:

                Combine the results from both searches.

                Implement a ranking algorithm (e.g., Reciprocal Rank Fusion) to produce a final, ordered list of package IDs.

            Hydrate Results:

                Fetch the full metadata for the ranked package IDs from DynamoDB.

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
