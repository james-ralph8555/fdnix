#!/usr/bin/env python3
"""
NixGraph Dependency Processor

This script processes the raw JSON output from the Nix evaluation and cleans it up
for better consumption by external tools.
"""

import json
import re
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Set, Any, Optional
from collections import defaultdict
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def parse_store_path(path: str) -> Optional[Dict[str, str]]:
    """
    Parse a Nix store path to extract package name and version.
    
    Examples:
    - "/nix/store/abc123-hello-1.0" -> {"name": "hello", "version": "1.0"}
    - "/nix/store/def456-python3.11-requests-2.28.1" -> {"name": "python3.11-requests", "version": "2.28.1"}
    """
    # Extract the store path basename
    if path.startswith('/nix/store/'):
        basename = path.split('/')[-1]
    else:
        basename = path
    
    # Remove the hash prefix (everything up to and including the first hyphen)
    if '-' in basename:
        without_hash = '-'.join(basename.split('-')[1:])
    else:
        return None
    
    # Try to extract version (look for version-like patterns at the end)
    version_patterns = [
        r'^(.+?)-(\d+(?:\.\d+)*(?:[-.].*)?(?:unstable.*)?(?:rc\d+)?(?:alpha\d+)?(?:beta\d+)?)$',
        r'^(.+?)-(\d+(?:\.\d+)*)$',
        r'^(.+?)-(.+)$',  # Fallback - everything after last hyphen is version
    ]
    
    for pattern in version_patterns:
        match = re.match(pattern, without_hash)
        if match:
            name, version = match.groups()
            # Clean up version string
            version = version.replace('_', '.')
            return {"name": name, "version": version}
    
    # If no version pattern matched, treat the whole thing as name
    return {"name": without_hash, "version": "unknown"}


def normalize_package_name(name: str) -> str:
    """Normalize package names for consistency."""
    # Remove common prefixes/suffixes that don't add value
    name = re.sub(r'^(lib|python\d*-|perl-)', '', name)
    
    # Normalize separators
    name = name.replace('_', '-')
    
    return name.lower()


def extract_dependencies(dep_list: List[str]) -> List[Dict[str, str]]:
    """Extract and normalize dependency information from store paths."""
    deps = []
    seen = set()
    
    for dep_path in dep_list:
        if isinstance(dep_path, str) and dep_path.strip():
            parsed = parse_store_path(dep_path.strip())
            if parsed:
                # Create a unique identifier
                dep_id = f"{parsed['name']}-{parsed['version']}"
                if dep_id not in seen:
                    deps.append({
                        "name": normalize_package_name(parsed['name']),
                        "version": parsed['version'],
                        "id": dep_id.lower(),
                        "original_path": dep_path
                    })
                    seen.add(dep_id)
    
    return deps


def process_package(pkg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process a single package entry."""
    try:
        # Basic validation
        if not pkg.get('pname') or not pkg.get('id'):
            return None
        
        # Extract and process dependencies
        build_inputs = extract_dependencies(pkg.get('buildInputs', []))
        propagated_inputs = extract_dependencies(pkg.get('propagatedBuildInputs', []))
        
        # Create processed package entry
        processed = {
            "id": pkg['id'].lower(),
            "pname": normalize_package_name(pkg['pname']),
            "version": pkg.get('version', 'unknown'),
            "attrPath": pkg.get('attrPath', ''),
            "buildInputs": build_inputs,
            "propagatedBuildInputs": propagated_inputs,
            "totalDependencies": len(build_inputs) + len(propagated_inputs)
        }
        
        return processed
        
    except Exception as e:
        logger.warning(f"Failed to process package {pkg.get('id', 'unknown')}: {e}")
        return None


def deduplicate_packages(packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate packages, preferring the most complete entries."""
    package_map = {}
    
    for pkg in packages:
        pkg_id = pkg['id']
        
        if pkg_id not in package_map:
            package_map[pkg_id] = pkg
        else:
            # Keep the one with more dependencies (more complete)
            current = package_map[pkg_id]
            if pkg['totalDependencies'] > current['totalDependencies']:
                package_map[pkg_id] = pkg
    
    return list(package_map.values())


def calculate_statistics(packages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate statistics about the dependency graph."""
    total_packages = len(packages)
    total_dependencies = sum(pkg['totalDependencies'] for pkg in packages)
    
    # Count packages with no dependencies
    no_deps = sum(1 for pkg in packages if pkg['totalDependencies'] == 0)
    
    # Find most depended upon packages
    dependency_counts = defaultdict(int)
    for pkg in packages:
        for dep in pkg['buildInputs'] + pkg['propagatedBuildInputs']:
            dependency_counts[dep['name']] += 1
    
    # Top dependencies
    top_dependencies = sorted(dependency_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    
    # Distribution of dependency counts
    dep_counts = [pkg['totalDependencies'] for pkg in packages]
    avg_deps = sum(dep_counts) / len(dep_counts) if dep_counts else 0
    max_deps = max(dep_counts) if dep_counts else 0
    
    return {
        "totalPackages": total_packages,
        "totalDependencyRelations": total_dependencies,
        "packagesWithNoDependencies": no_deps,
        "averageDependenciesPerPackage": round(avg_deps, 2),
        "maxDependenciesPerPackage": max_deps,
        "topDependencies": top_dependencies,
        "uniqueDependencyNames": len(dependency_counts)
    }


def process_dependencies(input_file: Path, output_file: Path) -> None:
    """Main processing function."""
    logger.info(f"Processing dependencies from {input_file}")
    
    # Load raw data
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load input file: {e}")
        sys.exit(1)
    
    # Extract packages and metadata
    raw_packages = raw_data.get('packages', [])
    metadata = raw_data.get('metadata', {})
    
    logger.info(f"Found {len(raw_packages)} raw packages")
    
    # Process packages
    processed_packages = []
    for pkg in raw_packages:
        processed = process_package(pkg)
        if processed:
            processed_packages.append(processed)
    
    logger.info(f"Successfully processed {len(processed_packages)} packages")
    
    # Deduplicate
    unique_packages = deduplicate_packages(processed_packages)
    logger.info(f"After deduplication: {len(unique_packages)} unique packages")
    
    # Calculate statistics
    stats = calculate_statistics(unique_packages)
    
    # Sort packages by name for consistent output
    unique_packages.sort(key=lambda x: x['pname'])
    
    # Create processed output
    processed_data = {
        "metadata": {
            **metadata,
            "processing": {
                "processed_at": None,  # Could add timestamp here
                "original_package_count": len(raw_packages),
                "processed_package_count": len(processed_packages),
                "unique_package_count": len(unique_packages)
            }
        },
        "statistics": stats,
        "packages": unique_packages
    }
    
    # Save processed data
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(processed_data, f, indent=2, sort_keys=True)
        logger.info(f"Processed data saved to {output_file}")
    except Exception as e:
        logger.error(f"Failed to save output file: {e}")
        sys.exit(1)
    
    # Print summary
    print(f"\n📊 Processing Summary:")
    print(f"   📦 Total packages: {stats['totalPackages']}")
    print(f"   🔗 Total dependency relations: {stats['totalDependencyRelations']}")
    print(f"   🎯 Average dependencies per package: {stats['averageDependenciesPerPackage']}")
    print(f"   📈 Max dependencies for one package: {stats['maxDependenciesPerPackage']}")
    print(f"   🏗️  Packages with no dependencies: {stats['packagesWithNoDependencies']}")
    
    if stats['topDependencies']:
        print(f"\n🔝 Top 5 most depended-upon packages:")
        for name, count in stats['topDependencies'][:5]:
            print(f"   • {name}: {count} packages depend on it")


def main():
    parser = argparse.ArgumentParser(
        description="Process raw NixGraph dependency data into clean JSON"
    )
    parser.add_argument(
        "input_file", 
        type=Path,
        help="Input JSON file from Nix evaluation"
    )
    parser.add_argument(
        "output_file",
        type=Path,
        help="Output processed JSON file"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Validate input file
    if not args.input_file.exists():
        logger.error(f"Input file does not exist: {args.input_file}")
        sys.exit(1)
    
    # Create output directory if it doesn't exist
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Process dependencies
    process_dependencies(args.input_file, args.output_file)


if __name__ == "__main__":
    main()

