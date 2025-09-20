# fdnix Package Minification Container

This container handles the offline minification process for nixpkgs package data using Zstandard compression and SQLite FTS5 search.

## Components

### minify.py
Main minification script that:
- Trains Zstandard compression dictionary from package samples
- Creates compressed SQLite database with FTS5 search capability
- Generates `minified.db` and `shared.dict` artifacts

### Requirements
- Python 3.14+ (for built-in `compression.zstd` module)
- SQLite3 with FTS5 support
- ~1GB RAM for processing 120k+ packages

## Usage
```bash
python -m src.minify
```

## Output Files
- `minified.db`: Compressed SQLite database with package data
- `shared.dict`: Zstandard compression dictionary
- `minification_stats.json`: Compression statistics

## Architecture
The system uses a two-table SQLite design:
1. `packages_kv`: Stores compressed package data as BLOBs
2. `packages_fts`: FTS5 virtual table for fast text search

This provides 70-90% size reduction while maintaining fast search performance.