# Step Function Quick Guide

## Full Pipeline
Run complete nixpkgs evaluation + processing:

```json
[]
```

## Processing Only
Skip evaluation, process existing data:

```json
[
  {
    "Name": "JSONL_INPUT_KEY",
    "Value": "evaluations/2025-09-11T18:48:01.632Z/nixpkgs-raw.jsonl.br"
  },
  {
    "Name": "LANCEDB_DATA_KEY", 
    "Value": "snapshots/2025-09-11T18:48:01.632Z/fdnix-data.lancedb"
  },
  {
    "Name": "LANCEDB_MINIFIED_KEY",
    "Value": "snapshots/2025-09-11T18:48:01.632Z/fdnix.lancedb"
  },
  {
    "Name": "DEPENDENCY_S3_KEY",
    "Value": "dependencies/2025-09-11T18:48:01.632Z/fdnix-deps.json"
  }
]
```

Only `JSONL_INPUT_KEY` is required - other parameters use timestamped defaults if omitted. Note that JSONL files must have `.br` extension for brotli compression.