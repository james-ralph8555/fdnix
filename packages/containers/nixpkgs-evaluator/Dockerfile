# Stage 1 Container: nixpkgs-evaluator
# Purpose: Run nix-eval-jobs and output raw JSONL to S3
FROM nixos/nix:latest

# Set working directory
WORKDIR /app

# Copy the Nix environment definition
COPY environment.nix /tmp/environment.nix

# Install minimal dependencies from environment.nix
# AND install the latest nix-eval-jobs by correctly calling its package function.
RUN nix-channel --update
RUN nix-env -if /tmp/environment.nix && \
    rm -rf /tmp/environment.nix

# OCI labels
LABEL org.opencontainers.image.title="fdnix nixpkgs-evaluator"
LABEL org.opencontainers.image.description="Stage 1: Extracts nixpkgs metadata using nix-eval-jobs and outputs raw JSONL"

# Copy application source code
COPY src/ ./src/

# Configure the environment
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default environment variables
ENV AWS_REGION=us-east-1

# Entry point - run the evaluator
CMD ["python", "src/index.py"]
