# Use a single stage based on the requested nixos/nix image version
FROM nixos/nix:2.31.0

# Set working directory
WORKDIR /app

# Copy the Nix environment definition into the container
COPY packages/containers/nixpkgs-indexer/environment.nix /tmp/environment.nix

# Install Nix dependencies including nix-eval-jobs
# We also clean up temporary files in the same layer to keep the final image clean.
RUN nix-env -if /tmp/environment.nix && \
    rm -rf /tmp/environment.nix

# LanceDB native FTS is used instead of tantivy-py

# OCI labels
LABEL org.opencontainers.image.title="fdnix nixpkgs-indexer"
LABEL org.opencontainers.image.description="Indexes nixpkgs metadata and embeddings for fdnix search"

# Copy application source code.
# We do this before creating the user to set ownership correctly in the next step.
COPY packages/containers/nixpkgs-indexer/src/ ./src/

# Configure the environment for the application
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Optional environment variables that can be overridden at runtime
ENV AWS_REGION=us-east-1
ENV PROCESSING_MODE=both

# Lance Core thread configuration to prevent CPU reservation warnings
ENV LANCE_CPU_THREADS=1
ENV LANCE_IO_THREADS=4

# Define the entry point for the container.
# We can now call python directly since all dependencies were installed
# into the default Nix profile, which is already on the PATH.
CMD ["python", "src/index.py"]
