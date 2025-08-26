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

