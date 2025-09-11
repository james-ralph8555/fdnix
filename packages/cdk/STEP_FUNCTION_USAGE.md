# Step Function Quick Guide

## Full Pipeline
Run complete nixpkgs evaluation + processing:

```json
{}
```

## Processing Only
Skip evaluation, process existing data (brotli-compressed JSONL required):

```json
{
  "JSONL_INPUT_KEY": "evaluations/2025-09-11T18:48:01.632Z/nixpkgs-raw.jsonl.br"
}
```

- Only `JSONL_INPUT_KEY` is read from the execution input.
- The state machine generates timestamped values for `jsonlInputKey`, `lancedbDataKey`, `lancedbMinifiedKey`, and `dependencyS3Key` internally and passes them to the processor task.
- JSONL files must be brotli-compressed and end with `.jsonl.br`.
