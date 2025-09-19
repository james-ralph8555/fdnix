# Step Function Quick Guide

## Full Pipeline
Run complete nixpkgs evaluation + processing:

```json
{
  "SQLITE_DATA_KEY": "snapshots/2025-09-11T18:48:01.632Z/fdnix-data.db",
  "SQLITE_MINIFIED_KEY": "snapshots/2025-09-11T18:48:01.632Z/fdnix.db",
  "DEPENDENCY_S3_KEY": "dependencies/2025-09-11T18:48:01.632Z/fdnix-deps.json"
}
```

## Processing Only
Skip evaluation, process existing data (brotli-compressed JSONL required):

```json
{
  "JSONL_INPUT_KEY": "evaluations/2025-09-11T18:48:01.632Z/nixpkgs-raw.jsonl.br",
  "SQLITE_DATA_KEY": "snapshots/2025-09-11T18:48:01.632Z/fdnix-data.db",
  "SQLITE_MINIFIED_KEY": "snapshots/2025-09-11T18:48:01.632Z/fdnix.db",
  "DEPENDENCY_S3_KEY": "dependencies/2025-09-11T18:48:01.632Z/fdnix-deps.json"
}
```

- All S3 keys must be provided as input parameters.
- JSONL files must be brotli-compressed and end with `.jsonl.br`.
