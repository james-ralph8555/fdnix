# NixGraph: Nixpkgs Runtime Dependency Tree Extraction

A comprehensive tool for extracting the full runtime dependency tree from Nixpkgs without building any packages. This tool leverages Nix's evaluation capabilities to gather dependency data and outputs structured JSON for analysis by external tools.

## Overview

NixGraph solves the problem of analyzing package dependencies in the Nix ecosystem by:

- **Evaluation-only approach**: No packages are built, only metadata is evaluated
- **Runtime focus**: Extracts only `buildInputs` and `propagatedBuildInputs` (excludes build-time dependencies)
- **Comprehensive coverage**: Traverses all packages including nested sets like `pythonPackages`
- **Clean output**: Processes raw data into structured, analysis-ready JSON
- **Scalable**: Handles the entire nixpkgs repository (100k+ packages)

## Quick Start

```bash
# Extract dependencies using default nixpkgs
./extract-dependencies.sh

# Extract from specific nixpkgs path
./extract-dependencies.sh --nixpkgs ./nixpkgs

# Extract with unfree packages enabled
./extract-dependencies.sh --allow-unfree

# Quick test with subset (useful for development)
./extract-dependencies.sh --test
```

## Requirements

- **Nix package manager** with `nix-instantiate` and `nix` commands
- **Python 3.7+** (for data processing)
- **jq** (optional, for JSON inspection)
- **Sufficient disk space** (~100MB for full extraction output)
- **Memory**: 4GB+ RAM recommended for full nixpkgs evaluation

## Installation

```bash
git clone <repository-url>
cd nixgraph
chmod +x extract-dependencies.sh scripts/process-deps.py
```

Or use the provided Nix flake:

```bash
nix develop
```

## Usage

### Command Line Interface

```bash
./extract-dependencies.sh [OPTIONS]

OPTIONS:
    -h, --help              Show help message
    -p, --nixpkgs PATH      Path to nixpkgs (default: <nixpkgs>)
    -s, --system SYSTEM     Target system (default: current system)
    -u, --allow-unfree      Allow unfree packages (default: false)
    -o, --output DIR        Output directory (default: ./output)
    -v, --verbose           Verbose output
    --raw                   Output raw JSON without processing
    --test                  Run with small subset for testing
```

### Examples

```bash
# Basic extraction
./extract-dependencies.sh

# Extract from local nixpkgs checkout
./extract-dependencies.sh --nixpkgs /path/to/nixpkgs

# Extract for different system
./extract-dependencies.sh --system aarch64-linux

# Verbose output with unfree packages
./extract-dependencies.sh --verbose --allow-unfree

# Custom output location
./extract-dependencies.sh --output /tmp/nixgraph-results
```

### Direct Nix Evaluation

You can also use the Nix expression directly:

```bash
# Raw JSON output
nix-instantiate --eval --json --strict extract-deps.nix

# With custom nixpkgs
nix-instantiate --eval --json --strict \
  --arg nixpkgs '"/path/to/nixpkgs"' \
  extract-deps.nix

# Using nix command (with flakes)
nix eval --json -f extract-deps.nix
```

## Output Format

### Directory Structure

After running extraction, you'll find:

```
output/nixgraph_20250909_143022/
├── dependencies_raw.json          # Raw output from Nix evaluation
├── dependencies_processed.json    # Cleaned and processed data
├── extraction.log                # Detailed extraction log
└── summary.txt                   # Human-readable summary
```

### JSON Schema

#### Raw Output (`dependencies_raw.json`)

```json
{
  "metadata": {
    "nixpkgs_version": "24.11.20241201.1234567",
    "extraction_timestamp": "1725894622",
    "total_packages": 125678,
    "system": "x86_64-linux",
    "allow_unfree": false
  },
  "packages": [
    {
      "id": "hello-2.12",
      "pname": "hello",
      "version": "2.12",
      "buildInputs": ["/nix/store/abc123-glibc-2.38"],
      "propagatedBuildInputs": [],
      "attrPath": "hello"
    }
  ]
}
```

#### Processed Output (`dependencies_processed.json`)

```json
{
  "metadata": {
    "nixpkgs_version": "24.11.20241201.1234567",
    "extraction_timestamp": "1725894622",
    "total_packages": 125678,
    "system": "x86_64-linux",
    "allow_unfree": false,
    "processing": {
      "original_package_count": 125678,
      "processed_package_count": 123456,
      "unique_package_count": 120000
    }
  },
  "statistics": {
    "totalPackages": 120000,
    "totalDependencyRelations": 450000,
    "packagesWithNoDependencies": 15000,
    "averageDependenciesPerPackage": 3.75,
    "maxDependenciesPerPackage": 150,
    "topDependencies": [
      ["glibc", 50000],
      ["gcc", 25000],
      ["bash", 20000]
    ],
    "uniqueDependencyNames": 85000
  },
  "packages": [
    {
      "id": "hello-2.12",
      "pname": "hello",
      "version": "2.12",
      "attrPath": "hello",
      "buildInputs": [
        {
          "name": "glibc",
          "version": "2.38",
          "id": "glibc-2.38",
          "original_path": "/nix/store/abc123-glibc-2.38"
        }
      ],
      "propagatedBuildInputs": [],
      "totalDependencies": 1
    }
  ]
}
```

## Data Processing

The processing pipeline performs several cleanup operations:

### 1. Store Path Parsing
- Extracts package names and versions from Nix store paths
- Handles complex naming patterns (e.g., `python3.11-requests-2.28.1`)
- Normalizes version strings

### 2. Package Name Normalization
- Removes common prefixes (`lib`, `python-`, etc.)
- Standardizes separators (underscores → hyphens)
- Converts to lowercase for consistency

### 3. Deduplication
- Removes duplicate packages based on ID
- Prefers entries with more complete dependency information

### 4. Statistics Generation
- Calculates dependency graph metrics
- Identifies most depended-upon packages
- Computes distribution statistics

## Analysis Examples

### Using jq

```bash
# Top 10 packages by dependency count
jq -r '.packages | sort_by(.totalDependencies) | reverse | .[0:10] | .[] | "\(.pname): \(.totalDependencies) deps"' dependencies_processed.json

# Packages with no dependencies
jq -r '.packages | map(select(.totalDependencies == 0)) | length' dependencies_processed.json

# Find all Python packages
jq -r '.packages | map(select(.pname | test("^python"))) | length' dependencies_processed.json

# Most depended-upon packages
jq -r '.statistics.topDependencies | .[0:10] | .[] | "\(.[0]): \(.[1]) dependents"' dependencies_processed.json
```

### Using Python

```python
import json

# Load data
with open('dependencies_processed.json') as f:
    data = json.load(f)

# Find packages that depend on a specific library
def find_dependents(target_package):
    dependents = []
    for pkg in data['packages']:
        all_deps = pkg['buildInputs'] + pkg['propagatedBuildInputs']
        if any(dep['name'] == target_package for dep in all_deps):
            dependents.append(pkg['pname'])
    return dependents

# Find all packages depending on openssl
openssl_dependents = find_dependents('openssl')
print(f"Packages depending on OpenSSL: {len(openssl_dependents)}")

# Calculate dependency depth
def calculate_max_depth():
    # This would require more complex graph traversal
    # Left as an exercise for analysis
    pass
```

## Architecture

### Core Components

1. **`extract-deps.nix`**: Core Nix expression that traverses nixpkgs
2. **`extract-dependencies.sh`**: Shell wrapper with argument parsing
3. **`scripts/process-deps.py`**: Python processor for data cleanup
4. **`flake.nix`**: Reproducible development environment

### Design Principles

- **Evaluation-only**: Never triggers package builds
- **Safe traversal**: Uses `lib.tryEval` to handle evaluation failures
- **Runtime focus**: Excludes `nativeBuildInputs` (build-time only)
- **Comprehensive**: Handles nested package sets and complex structures
- **Extensible**: Clean separation between extraction and processing

## Performance

### Benchmarks

| nixpkgs Size | Extraction Time | Output Size | Memory Usage |
|--------------|----------------|-------------|--------------|
| ~120k packages | 15-30 minutes | ~100MB JSON | 4-8GB RAM |
| Test subset | 30-60 seconds | ~1MB JSON | <1GB RAM |

### Optimization Tips

- Use `--test` flag for development/debugging
- Run on machines with adequate RAM (4GB+)
- Consider `--allow-unfree` impact (adds ~10k packages)
- Use SSD storage for faster I/O

## Troubleshooting

### Common Issues

**Out of memory errors**
```bash
# Reduce memory usage by limiting evaluation
export NIX_BUILD_CORES=1
ulimit -v 4000000  # Limit to 4GB
```

**Evaluation failures**
```bash
# Check the extraction log
cat output/*/extraction.log

# Run with verbose output
./extract-dependencies.sh --verbose
```

**Python processing errors**
```bash
# Run processing separately with verbose output
python3 scripts/process-deps.py output/*/dependencies_raw.json processed.json --verbose
```

### Known Limitations

- Some packages may fail evaluation due to platform constraints
- Infinite recursion protection limits traversal depth to 3 levels
- Processing assumes certain store path naming conventions
- Memory usage scales with nixpkgs size

## Development

### Running Tests

```bash
# Quick test with subset
./extract-dependencies.sh --test --verbose

# Validate JSON output
jq . output/*/dependencies_raw.json > /dev/null && echo "Valid JSON"

# Run processing with test data
python3 scripts/process-deps.py output/*/dependencies_raw.json test_processed.json --verbose
```

### Contributing

1. Test changes with `--test` flag first
2. Ensure JSON output remains valid
3. Update documentation for schema changes
4. Consider performance impact of modifications

### Extending

The design supports easy extension:

- **New output formats**: Extend `process-deps.py`
- **Additional metadata**: Modify `extract-deps.nix`
- **Custom filtering**: Add options to shell script
- **Analysis tools**: Build on processed JSON output

## Related Tools

- **nix-visualize**: Visualizes individual package dependencies
- **nixpkgs-review**: Reviews package changes
- **vulnix**: Security vulnerability scanning
- **nix-tree**: Interactive dependency browsing

## License

[License information would go here]

## Changelog

### v1.0.0 (2025-09-09)
- Initial release
- Core extraction functionality
- JSON processing pipeline
- Comprehensive documentation