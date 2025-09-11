# Step Function Quick Guide

## Full Pipeline
Run complete nixpkgs evaluation + processing:

```json
[]
```

## Processing Only
Skip evaluation, process existing data:

```json
{
  "JSONL_INPUT_KEY": "evaluations/2025-09-11T18:48:01.632Z/nixpkgs-raw.jsonl.br",
  "LANCEDB_DATA_KEY": "snapshots/2025-09-11T18:48:01.632Z/fdnix-data.lancedb",
  "LANCEDB_MINIFIED_KEY": "snapshots/2025-09-11T18:48:01.632Z/fdnix.lancedb", 
  "DEPENDENCY_S3_KEY": "dependencies/2025-09-11T18:48:01.632Z/fdnix-deps.json"
}
```

Only `JSONL_INPUT_KEY` is required - other parameters use timestamped defaults if omitted. Note that JSONL files must have `.br` extension for brotli compression.