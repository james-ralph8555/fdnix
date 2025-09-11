# Step Function Quick Guide

## Full Pipeline
Run complete nixpkgs evaluation + processing:

```json
{}
```

## Processing Only
Skip evaluation, process existing data:

```json
{
  "jsonlInputKey": "evaluations/2025-09-11T08:59:59.773Z/nixpkgs-raw.jsonl",
  "lancedbDataKey": "snapshots/2025-09-11T08:59:59.773Z/fdnix-data.lancedb",
  "lancedbMinifiedKey": "snapshots/2025-09-11T08:59:59.773Z/fdnix.lancedb",
  "dependencyS3Key": "dependencies/2025-09-11T08:59:59.773Z/fdnix-deps.json"
}
```

Only `jsonlInputKey` is required - other parameters use timestamped defaults if omitted.