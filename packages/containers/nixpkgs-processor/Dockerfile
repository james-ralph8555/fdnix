# Stage 2 Container: nixpkgs-processor
# Purpose: Read JSONL from S3, process into LanceDB, generate embeddings, publish layers
FROM nixos/nix:2.31.0

# Set working directory
WORKDIR /app

# Copy the Nix environment definition
COPY environment.nix /tmp/environment.nix

# Install dependencies for data processing (LanceDB, embeddings, etc.)
RUN nix-env -if /tmp/environment.nix && \
    rm -rf /tmp/environment.nix

# OCI labels
LABEL org.opencontainers.image.title="fdnix nixpkgs-processor"
LABEL org.opencontainers.image.description="Stage 2: Processes JSONL data into LanceDB, generates embeddings, publishes layers"

# Copy application source code
COPY src/ ./src/

# Configure the environment for the application
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Optional environment variables that can be overridden at runtime
ENV AWS_REGION=us-east-1
ENV PROCESSING_MODE=both
# Disable embedding generation in the pipeline by default
ENV ENABLE_EMBEDDINGS=false
# Bedrock configuration
ENV BEDROCK_MODEL_ID=amazon.titan-embed-text-v2:0
ENV BEDROCK_OUTPUT_DIMENSIONS=256
ENV BEDROCK_MAX_RPM=600
ENV BEDROCK_MAX_TOKENS_PER_MINUTE=300000
ENV PROCESSING_BATCH_SIZE=100

# Lance Core thread configuration to prevent CPU reservation warnings
ENV LANCE_CPU_THREADS=1
ENV LANCE_IO_THREADS=4

# Entry point - run the processor
CMD ["python", "src/index.py"]