# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository contains **fdnix**, a hybrid search engine for nixpkgs that combines semantic vector search with traditional keyword matching. The project includes a complete copy of the nixpkgs repository under `nixpkgs/` and aims to build a serverless, JAMstack-based search application.

## Architecture

The project is designed as a monorepo with the following planned structure:
- **Frontend**: SolidJS static site with CloudFront/S3 hosting
- **Backend**: AWS Lambda search API with API Gateway
- **Data Pipeline**: Daily automated indexing using AWS Fargate containers
- **Infrastructure**: AWS CDK for Infrastructure as Code
- **Data Stores**: DynamoDB (metadata), S3 (Faiss vector index), OpenSearch Serverless (keyword search)

## Key Components

### nixpkgs Directory
Contains a complete copy of the Nixpkgs repository (NixOS package collection) with:
- **flake.nix**: Primary flake interface for Nixpkgs
- **lib/**: Nixpkgs library functions  
- **pkgs/**: Package definitions (120,000+ packages)
- **nixos/**: NixOS system modules and tests
- **shell.nix**: Development shell with nixpkgs-review and gh tools

### fdnix Search Engine
- **README.md**: User-facing documentation for the search interface
- **INIT.md**: Detailed technical implementation plan with step-by-step roadmap

## Common Commands

### Nix Development
```bash
# Enter development shell with tools
nix-shell

# With specific nixpkgs for testing changes to tools
nix-shell --arg nixpkgs ./.

# Format all Nix files
nix fmt  # or treefmt

# Build and test changes
nix build .#<package>

# Review package dependencies
nix-shell -p nixpkgs-review --run "nixpkgs-review wip"
```

### Package Development Workflow
```bash
# Review PR changes
nixpkgs-review pr <PR_NUMBER>

# Review uncommitted changes  
nixpkgs-review wip

# Test specific packages
nix build .#<package> --show-trace
```

### CI and Quality Checks
```bash
# Run nixpkgs-vet tool locally (from nixpkgs/ directory)
./ci/nixpkgs-vet.sh master

# Format check
treefmt --check

# Build with sandboxing (recommended)
# Ensure sandbox = true in /etc/nix/nix.conf
```

## Development Guidelines

### Branch Strategy
- **master**: Main development branch
- **staging**: For mass rebuilds (>500 packages)
- **release-YY.MM**: Stable release branches
- Always test with `sandbox = true` enabled

### Code Standards
- Use nixfmt for formatting (enforced by CI)
- Follow kebab-case for file/directory names
- Use lowerCamelCase for variable names
- Test changes with `nixpkgs-review` before submitting
- Include clear commit messages explaining intent

### Testing Requirements
- Build test packages in sandbox mode
- Run relevant NixOS tests if available (nixos/tests/)
- Execute binaries in ./result/bin/ to verify functionality
- Use `nix build --show-trace` for debugging

## fdnix Implementation Plan

The project follows a phased approach:

1. **Phase 1**: CDK infrastructure setup (database, pipeline, API, frontend stacks)
2. **Phase 2**: Data processing containers (metadata extraction from nixpkgs, embedding generation)  
3. **Phase 3**: Search API implementation (hybrid vector + keyword search with ranking fusion)
4. **Phase 4**: SolidJS frontend with responsive UI and filtering

See INIT.md for detailed implementation steps and architecture diagrams.

## Important Notes

- This is a monorepo combining both the search engine implementation and a complete nixpkgs copy
- The nixpkgs directory follows standard Nixpkgs contribution guidelines
- The project targets AWS serverless deployment with daily automated data updates
- Use `nix develop` or `nix-shell` for consistent development environments
- Prefix all AWS resources for this project with `fdnix-`.
 - DNS is managed via Cloudflare (not Route53). For the frontend, create CNAMEs in Cloudflare pointing `fdnix.com` (apex, flattened) and `www` to the CloudFront distribution domain, and set SSL/TLS to Full (strict).
 - CloudFront certificates must be in `us-east-1`. Validate ACM certificates via DNS by adding the ACM‑provided CNAME records in Cloudflare.
