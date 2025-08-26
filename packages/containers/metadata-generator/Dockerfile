# Multi-stage build to combine NixOS with Node.js for AWS SDK
FROM nixos/nix:2.18.1 AS nix-stage

# Install git in the Nix stage 
RUN nix-env -i git

# Stage 2: Node.js runtime for AWS SDK
FROM node:18-alpine AS runtime

# Install git and other necessary tools
RUN apk add --no-cache git bash

# Copy Nix from the first stage
COPY --from=nix-stage /nix /nix
COPY --from=nix-stage /etc/passwd /etc/passwd
COPY --from=nix-stage /etc/group /etc/group

# Set up Nix environment
ENV PATH="/nix/var/nix/profiles/default/bin:$PATH"
ENV NIX_PATH="nixpkgs=/nix/var/nix/profiles/per-user/root/channels/nixos"

# Create app directory
WORKDIR /app

# Copy package.json and install dependencies
COPY package*.json ./
RUN npm install --only=production

# Copy application source
COPY src/ ./src/

# Create non-root user for security
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001 -G nodejs

# Change ownership of the app directory
RUN chown -R nodejs:nodejs /app

# Switch to non-root user
USER nodejs

# Set default environment variables (can be overridden by CDK)
ENV DYNAMODB_TABLE=fdnix-packages
ENV AWS_REGION=us-east-1

# Command to run the application
CMD ["node", "src/index.js"]