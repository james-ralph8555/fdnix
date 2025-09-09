# Sharded Nixpkgs Dependency Extraction

## Overview

This implementation solves the stack overflow issues when extracting runtime dependencies from nixpkgs by using a **sharded evaluation strategy**. Instead of evaluating the entire nixpkgs tree in one massive thunk, we break it down into smaller, manageable pieces.

## Why Sharding?

The original monolithic approach (`extract-deps.nix`) is no longer supported because:

1. **Stack Overflow**: Deep recursion in large package ecosystems (Python, Haskell)
2. **Memory Explosion**: Trying to stringify 120k+ packages simultaneously
3. **Alias Loops**: Circular references in legacy package aliases
4. **Thunk Explosion**: Nix evaluator can't handle the massive computation graph

## Architecture

### Files

- **`extract-deps-sharded.nix`**: New sharded Nix evaluation expression
- **`extract_dependencies.py`**: Updated Python orchestrator with sharding support
- **`process_deps.py`**: Updated to handle sharded result metadata

### How It Works

1. **Shard Discovery**: Query `extract-deps-sharded.nix` to get available shards
2. **Prioritized Processing**: Process safe shards first, problematic ones last
3. **Individual Evaluation**: Each shard runs as a separate `nix-instantiate` call
4. **Result Combination**: Merge all successful shard results
5. **Deduplication**: Remove duplicate packages across shards

## Sharding Strategy

### Shard Types

**Safe Shards** (processed first):
- `stdenv`, `coreutils`, `bash`, `gcc` - Core system packages

**Normal Shards**:
- `gitAndTools`, `linuxPackages`, `gnome`, `qt6` - Regular package groups

**Problematic Shards** (processed last with extra safeguards):
- `pythonPackages`, `haskellPackages`, `nodePackages` - Large ecosystems
- `rPackages`, `juliaPackages` - Language-specific packages

### Safeguards Per Shard Type

- **Reduced Depth**: Problematic shards limited to 5 levels deep
- **No Aliases**: `allowAliases = false` for problematic shards
- **Timeouts**: 10min for problematic, 5min for normal shards
- **Resource Limits**: Stack size increased to 16MB

## Usage

### Basic Usage (Sharded)

```bash
python extract_dependencies.py --verbose
```

Monolithic extraction has been removed. The system always uses the sharded evaluator.

### Advanced Options

```bash
python extract_dependencies.py \
  --max-depth 8 \
  --allow-aliases \
  --verbose \
  --nixpkgs /path/to/nixpkgs \
  --system x86_64-linux
```


## Resource Requirements

### Memory Usage
- **Sharded**: 2-4GB peak (controlled per shard)

### Disk Space
- Raw output: ~100-200MB JSON
- Individual shard files: ~2-10MB each
- Logs: ~50-100MB total

## Output Structure

### Sharded Metadata

```json
{
  "metadata": {
    "extraction_method": "sharded",
    "total_shards_processed": 25,
    "shard_details": {
      "pythonPackages": {
        "package_count": 8542,
        "duration_seconds": 180.5
      }
    }
  },
  "packages": [...]
}
```

### Processing Stats

```
📊 Processing Summary:
   📦 Total packages: 45,231
   🔗 Total dependency relations: 127,843
   🎯 Average dependencies per package: 2.8
   📈 Max dependencies for one package: 156
   🏗️  Packages with no dependencies: 12,847
   🧩 Shards processed: 23
   ✅ Shard success rate: 95.7%
```

## Error Handling

### Shard Failures
- Individual shard failures don't stop the entire process
- Failed shards are logged and skipped
- Process stops if >50% of shards fail
- Partial results still saved and usable

### Common Failures
1. **Timeout**: Shard too complex, increase timeout
2. **Stack Overflow**: Reduce max-depth or disable aliases
3. **Memory**: Use resource limits script
4. **Evaluation Error**: Check Nix expression syntax

## Troubleshooting

### Stack Overflow Still Happening?

```bash
# Reduce recursion depth
python extract_dependencies.py --max-depth 5
```

### Memory Issues?

```bash
# Use conservative settings
python extract_dependencies.py --max-depth 3 --verbose
```

### Timeouts?

```bash
# Check which shards are timing out
grep "timed out" output/*/extraction.log

# Adjust timeout in extract_dependencies.py
```

### Low Success Rate?

```bash
# Check failed shards
grep "failed" output/*/extraction.log

# Try without aliases
python extract_dependencies.py --max-depth 3
```

## Performance

| Method  | Success Rate | Memory Peak | Time  | Packages |
|---------|--------------|-------------|-------|----------|
| Sharded | ~95%         | 3-4GB       | 20min | ~45k     |

## Future Improvements

1. **Dynamic Sharding**: Auto-split large shards
2. **Parallel Processing**: Run multiple shards simultaneously  
3. **Incremental Updates**: Only process changed shards
4. **Better Prioritization**: ML-based shard ordering
5. **Shard Caching**: Cache successful shard results

## Migration Notes

- Extraction is now sharded-only
- Output format unchanged (packages + metadata)
- All existing tools work with sharded output
