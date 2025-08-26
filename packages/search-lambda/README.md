# fdnix-search-api (Rust)

Planned Rust implementation of the fdnix hybrid search Lambda.

- Runtime: AWS Lambda custom runtime (`provided.al2023`).
- Packaging: Compile to a static binary named `bootstrap` and zip for upload.
- Handler: Use `lambda_http` or similar for request handling.
- SDKs: AWS Rust SDK for DynamoDB, S3, and (optionally) OpenSearch; Bedrock via AWS SDK.

Current status: A minimal Node.js handler is deployed as a temporary stub to keep CDK wiring and API Gateway in place. It will be replaced by the Rust `bootstrap` binary.

Build outline (to be implemented):
- `cargo build --release --target x86_64-unknown-linux-gnu`
- Copy artifact to `bootstrap` and package for Lambda

## Best Practices for Building

- Match Lambda environment:
  - Build inside Amazon Linux 2023 to ensure glibc compatibility with the `provided.al2023` runtime.
  - Example one-liner using Docker:
    - `docker run --rm -it -v "$PWD":/workspace -w /workspace public.ecr.aws/amazonlinux/amazonlinux:2023 bash -lc "dnf -y install gcc gcc-c++ unzip tar gzip make && curl https://sh.rustup.rs -sSf | sh -s -- -y && source $HOME/.cargo/env && cd packages/search-lambda && ./build.sh"`

- Alternatively, build static with MUSL:
  - Use target `x86_64-unknown-linux-musl` and prefer TLS stacks that don’t require system OpenSSL (e.g., `rustls`). Some crates may need adjustments for musl.

- Optimize size and cold start:
  - Build in `--release` with `lto = true`, `codegen-units = 1`, `opt-level = "z"`.
  - Strip symbols: `strip dist/bootstrap`.

- Verify output:
  - Ensure `dist/bootstrap` exists and is executable before running CDK deploy.

- Deploy flow:
  - Build: `pnpm --filter search-lambda build`
  - Deploy: `pnpm --filter cdk cdk deploy`
