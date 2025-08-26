# Use Python 3.11 slim image for smaller footprint
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies and uv
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

# Add uv to PATH
ENV PATH="/root/.local/bin:$PATH"

# Copy pyproject.toml and install Python dependencies with uv
COPY pyproject.toml .
RUN uv pip install --system boto3 botocore numpy requests

# Copy application source
COPY src/ ./src/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Set default environment variables (can be overridden by CDK)
ENV DYNAMODB_TABLE=fdnix-packages
ENV S3_BUCKET=fdnix-vec
ENV AWS_REGION=us-east-1
ENV BEDROCK_MODEL_ID=cohere.embed-english-v3

# Command to run the application using uv
CMD ["uv", "run", "python", "src/index.py"]