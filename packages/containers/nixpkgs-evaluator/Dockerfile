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

# Create mount point for RAM disk that will store /nix/store
RUN mkdir -p /nix/store

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

# Create a script to set up RAM disk and run the application
RUN echo '#!/bin/bash\n\
# Mount tmpfs for /nix/store to use RAM for high-speed I/O\n\
echo "Setting up RAM disk for /nix/store (20GB)..."\n\
mount -t tmpfs -o size=20G,mode=755 tmpfs /nix/store\n\
echo "RAM disk mounted successfully"\n\
df -h /nix/store\n\
\n\
# Run the evaluator\n\
exec python src/index.py' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Entry point - set up RAM disk and run the evaluator
CMD ["/app/entrypoint.sh"]
