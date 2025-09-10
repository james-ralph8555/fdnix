# Step Function Usage

The updated step function now supports conditional execution based on input parameters.

## Execution Modes

### 1. Full Pipeline (Evaluation + Processing)
Execute the complete pipeline starting with nixpkgs evaluation:

```json
{}
```

or 

```json
{
  "someOtherParameter": "value"
}
```

This will run: `EvaluatorTask` → `ProcessorTaskWithEvaluatorOutput`

### 2. Processing Only (Skip Evaluation)
Skip evaluation and process existing JSONL outputs:

```json
{
  "jsonlInputKey": "evaluations/2025-09-10/nixpkgs-raw.jsonl"
}
```

Optional additional parameters with defaults:
```json
{
  "jsonlInputKey": "evaluations/2025-09-10/nixpkgs-raw.jsonl",
  "lancedbDataKey": "snapshots/2025-09-10/fdnix-data.lancedb",
  "lancedbMinifiedKey": "snapshots/2025-09-10/fdnix.lancedb", 
  "dependencyS3Key": "dependencies/2025-09-10/fdnix-deps.json"
}
```

This will run: `SetDefaultKeys` → `ProcessorTask` (evaluation skipped)

## Input Parameters

- `jsonlInputKey` (required for skip mode): S3 key to existing JSONL evaluation output
- `lancedbDataKey` (optional): S3 key for main LanceDB output (defaults to timestamped path)
- `lancedbMinifiedKey` (optional): S3 key for minified LanceDB output (defaults to timestamped path)
- `dependencyS3Key` (optional): S3 key for dependency output (defaults to timestamped path)

## Use Cases

1. **Daily scheduled runs**: Use full pipeline mode (no input parameters)
2. **Re-processing existing data**: Use processing-only mode with `jsonlInputKey`
3. **Testing processor changes**: Use processing-only mode with existing evaluation data
4. **Manual runs with specific outputs**: Provide all S3 keys for custom output locations