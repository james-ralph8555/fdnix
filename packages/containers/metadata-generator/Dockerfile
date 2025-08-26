# Stage 1: bring in Nix to enable nix-env during extraction
FROM nixos/nix:2.18.1 AS nix-stage
RUN nix-env -i git

# Stage 2: Python runtime matching embedding-generator
FROM python:3.11-slim AS runtime

# Set working directory
WORKDIR /app

# Install system deps and uv (match embedding-generator pattern)
RUN apt-get update && apt-get install -y \
    git \
    bash \
    curl \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

# Add uv to PATH
ENV PATH="/root/.local/bin:$PATH"

# Copy Nix store/basics from the nix stage
COPY --from=nix-stage /nix /nix
COPY --from=nix-stage /etc/passwd /etc/passwd
COPY --from=nix-stage /etc/group /etc/group

# Set up Nix environment for nix-env
ENV PATH="/nix/var/nix/profiles/default/bin:$PATH"
ENV NIX_PATH="nixpkgs=/nix/var/nix/profiles/per-user/root/channels/nixos"

# Copy Python project metadata and install runtime deps via uv
COPY pyproject.toml ./
RUN uv pip install --system boto3 botocore requests

# Copy application source
COPY src/ ./src/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Environment
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Required env (overridden by CDK)
ENV DYNAMODB_TABLE=fdnix-packages
ENV AWS_REGION=us-east-1

# Entry point
CMD ["uv", "run", "python", "src/index.py"]
