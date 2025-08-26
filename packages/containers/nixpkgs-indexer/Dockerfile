# Use a single stage based on the requested nixos/nix image version
FROM nixos/nix:2.18.1

# Set working directory
WORKDIR /app

# Install all system and Python dependencies using Nix in a single layer.
# This provides Python, uv, Git, a compiler, and the required Python packages.
# Using '-iA' installs from the nixpkgs attribute path.
RUN nix-env -iA \
    nixpkgs.git \
    nixpkgs.gcc \
    nixpkgs.python311 \
    nixpkgs.uv \
    nixpkgs.python311Packages.boto3 \
    nixpkgs.python311Packages.botocore \
    nixpkgs.python311Packages.requests \
    nixpkgs.python311Packages.duckdb \
    nixpkgs.python311Packages.numpy

# OCI labels
LABEL org.opencontainers.image.title="fdnix nixpkgs-indexer"
LABEL org.opencontainers.image.description="Indexes nixpkgs metadata and embeddings for fdnix search"

# Copy application source code.
# We do this before creating the user to set ownership correctly in the next step.
COPY src/ ./src/

# Note: We no longer copy pyproject.toml and run `uv pip install`,
# because Nix has already installed the dependencies declaratively from nixpkgs.

# Create a non-root user for better security.
# The nixos/nix image is minimal but includes `useradd`.
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

# Configure the environment for the application
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Optional environment variables that can be overridden at runtime
ENV AWS_REGION=us-east-1
ENV PROCESSING_MODE=both

# Define the entry point for the container.
# We can now call python directly since all dependencies were installed
# into the default Nix profile, which is already on the PATH.
CMD ["python", "src/index.py"]
