#!/usr/bin/env python3

"""
NixGraph: Extract dependencies from nixpkgs (Python port)

This is a Python equivalent of extract-dependencies.sh. It wraps the Nix
evaluation, handles output/logs, and optionally post-processes the results.
"""

from __future__ import annotations
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
import time
import tempfile

# Always require the processor module; no silent fallbacks
from process_deps import process_dependencies as _process_dependencies


# ------------------------- Logging utilities -------------------------

def log_info(msg: str) -> None:
    print(f"[INFO] {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)


def log_verbose(msg: str, verbose: bool) -> None:
    if verbose:
        print(f"[VERBOSE] {msg}", file=sys.stderr)


# ------------------------- Helpers -------------------------

def which_required(binaries: List[str]) -> None:
    missing = [b for b in binaries if shutil.which(b) is None]
    if missing:
        log_error(f"Missing required dependencies: {' '.join(missing)}")
        log_error("Please ensure Nix is properly installed and in PATH")
        sys.exit(1)


def current_system(verbose: bool) -> str:
    # Mirrors: nix-instantiate --eval --expr 'builtins.currentSystem' | tr -d '"'
    cmd = [
        "nix-instantiate",
        "--eval",
        "--expr",
        "builtins.currentSystem",
    ]
    log_verbose(f"Detecting current system via: {' '.join(cmd)}", verbose)
    try:
        res = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return res.stdout.strip().strip('"')
    except subprocess.CalledProcessError as e:
        log_error("Failed to detect current system")
        log_error(e.stderr.strip())
        sys.exit(1)


def timestamp_dir(base: Path) -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = base / f"nixgraph_{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def build_nix_cmd(
    nix_file: Path,
    nixpkgs_path: Optional[str],
    system: Optional[str],
    allow_unfree: bool,
    verbose: bool,
    shard: Optional[str] = None,
    max_depth: int = 10,
    allow_aliases: bool = False,
) -> List[str]:
    base = [
        "nix-instantiate",
        "--eval",
        "--json",
        "--strict",
    ]

    cmd: List[str] = list(base)

    # nixpkgs: pass as a Nix path expression. If the value is like <nixpkgs>,
    # pass it literally. Otherwise pass an absolute filesystem path literal.
    if nixpkgs_path:
        value = nixpkgs_path
        if value.startswith("<") and value.endswith(">"):
            # Let default parameter <nixpkgs> stand, or pass the literal path expr
            # Passing as an expression is fine (not a string)
            cmd += ["--arg", "nixpkgs", value]
        else:
            # Pass as a plain string so import "${nixpkgs}/lib" works without store context
            abs_path = str(Path(value).resolve())
            cmd += ["--argstr", "nixpkgs", abs_path]

    # system: use --argstr to avoid manual quoting
    if system:
        cmd += ["--argstr", "system", system]

    # allowUnfree: boolean nix value
    cmd += ["--arg", "allowUnfree", "true" if allow_unfree else "false"]
    
    # allowAliases: boolean nix value (false helps avoid alias cycles)
    cmd += ["--arg", "allowAliases", "true" if allow_aliases else "false"]
    
    # maxDepth: integer to limit recursion
    cmd += ["--arg", "maxDepth", str(max_depth)]
    
    # shard: specific shard to process (null for shard listing)
    if shard:
        cmd += ["--argstr", "shard", shard]
    else:
        cmd += ["--arg", "shard", "null"]

    # Add verbose trace for easier debugging
    if verbose:
        cmd.append("--show-trace")

    cmd.append(str(nix_file))

    log_verbose(f"Built nix command: {' '.join(cmd)}", verbose)
    return cmd


def setup_resource_limits(verbose: bool) -> None:
    """Setup system resource limits to prevent crashes"""
    import resource
    
    try:
        # Set stack size to 16MB (default is usually 8MB)
        stack_size = 16 * 1024 * 1024  # 16MB in bytes
        resource.setrlimit(resource.RLIMIT_STACK, (stack_size, stack_size))
        log_verbose(f"Set stack size limit to {stack_size // (1024*1024)}MB", verbose)
        
        # Get and log current limits
        current_stack = resource.getrlimit(resource.RLIMIT_STACK)
        log_verbose(f"Current stack limit: {current_stack[0] // (1024*1024)}MB", verbose)
        
    except Exception as e:
        log_verbose(f"Failed to set resource limits: {e}", verbose)

def run_extraction(cmd: List[str], output_file: Path, log_file: Path, meta: dict, verbose: bool) -> bool:
    # Write header to log
    header = [
        "# NixGraph Dependency Extraction Log",
        f"# Started: {_dt.datetime.now().isoformat(timespec='seconds')}",
        f"# Command: {' '.join(cmd)}",
        f"# Nixpkgs: {meta.get('nixpkgs_path','')}",
        f"# System: {meta.get('system','')}",
        f"# Allow unfree: {meta.get('allow_unfree','')}",
        "",
    ]
    log_file.write_text("\n".join(header))

    log_verbose("Starting nix-instantiate evaluation...", verbose)
    
    # Set up resource limits before starting
    setup_resource_limits(verbose)
    
    # Capture stderr to both log file and stderr for container visibility
    try:
        with output_file.open("w") as out:
            proc = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, text=True)
            
            # Write stderr to log file
            if proc.stderr:
                with log_file.open("a") as err:
                    err.write("\n--- STDERR ---\n")
                    err.write(proc.stderr)
                    err.write("\n--- END STDERR ---\n")
                
                # Also output to stderr for CloudWatch/container logs
                log_error("nix-instantiate stderr output:")
                for line in proc.stderr.strip().split('\n'):
                    if line.strip():
                        log_error(f"  {line}")
            
    except Exception as e:
        log_error(f"Failed to run nix-instantiate: {e}")
        return False

    if proc.returncode == 0:
        log_info("Extraction completed successfully")
        log_info(f"Raw output saved to: {output_file}")
        # Try to show basic stats
        try:
            with output_file.open() as f:
                data = json.load(f)
            pkg_count = (
                (len(data.get("packages", [])))
                if isinstance(data, dict)
                else "unknown"
            )
        except Exception:
            pkg_count = "unknown"
        log_info(f"Extracted dependencies for {pkg_count} packages")
        return True
    else:
        log_error(f"Extraction failed with exit code {proc.returncode}")
        log_error(f"Check log file for details: {log_file}")
        if proc.stderr:
            log_error("Last stderr output:")
            for line in proc.stderr.strip().split('\n')[-10:]:  # Show last 10 lines
                if line.strip():
                    log_error(f"  {line}")
        return False


def discover_shards(nix_file: Path, nixpkgs_path: Optional[str], system: str, allow_unfree: bool, verbose: bool) -> List[str]:
    """Discover available shards by calling the sharded Nix expression with shard=null"""
    log_verbose("Discovering available shards...", verbose)
    
    cmd = build_nix_cmd(
        nix_file=nix_file,
        nixpkgs_path=nixpkgs_path,
        system=system,
        allow_unfree=allow_unfree,
        verbose=verbose,
        shard=None  # This will return the list of available shards
    )
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        shards = data.get('availableShards', [])
        log_info(f"Discovered {len(shards)} shards: {', '.join(shards[:5])}{'...' if len(shards) > 5 else ''}")
        return shards
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        log_error(f"Failed to discover shards: {e}")
        # Return a minimal set of known-safe shards
        return ['stdenv', 'coreutils', 'bash', 'gcc']

def is_problematic_shard(shard_name: str) -> bool:
    """Check if a shard is known to be problematic and should be processed with extra care"""
    problematic_shards = {
        'pythonPackages', 'python311Packages', 'python310Packages', 'python39Packages',
        'haskellPackages', 'haskell', 'nodePackages', 'nodePackages_latest',
        'rPackages', 'juliaPackages'
    }
    return shard_name in problematic_shards

def process_shard(shard_name: str, nix_file: Path, nixpkgs_path: Optional[str], 
                 system: str, allow_unfree: bool, verbose: bool, 
                 max_depth: int, allow_aliases: bool, out_dir: Path) -> Optional[Dict[str, Any]]:
    """Process a single shard and return its data"""
    log_info(f"Processing shard: {shard_name}")
    
    # Adjust parameters for problematic shards
    if is_problematic_shard(shard_name):
        log_verbose(f"Shard {shard_name} is problematic, using conservative settings", verbose)
        max_depth = min(max_depth, 5)  # Reduce depth for problematic shards
        allow_aliases = False  # Never allow aliases for problematic shards
        timeout = 600  # 10 minute timeout for problematic shards
    else:
        timeout = 300  # 5 minute timeout for normal shards
    
    cmd = build_nix_cmd(
        nix_file=nix_file,
        nixpkgs_path=nixpkgs_path,
        system=system,
        allow_unfree=allow_unfree,
        verbose=verbose,
        shard=shard_name,
        max_depth=max_depth,
        allow_aliases=allow_aliases
    )
    
    shard_file = out_dir / f"shard_{shard_name}.json"
    shard_log = out_dir / f"shard_{shard_name}.log"
    
    # Set up logging for this shard
    header = [
        f"# Shard Processing Log: {shard_name}",
        f"# Started: {_dt.datetime.now().isoformat(timespec='seconds')}",
        f"# Command: {' '.join(cmd)}",
        ""
    ]
    shard_log.write_text("\n".join(header))
    
    start_time = time.time()
    try:
        with shard_file.open("w") as out:
            proc = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, 
                                text=True, timeout=timeout)
            
        if proc.stderr:
            with shard_log.open("a") as err:
                err.write("\n--- STDERR ---\n")
                err.write(proc.stderr)
                err.write("\n--- END STDERR ---\n")
        
        if proc.returncode == 0:
            # Load and return the shard data
            with shard_file.open() as f:
                shard_data = json.load(f)
            
            duration = time.time() - start_time
            pkg_count = len(shard_data.get('packages', []))
            log_info(f"Shard {shard_name} completed in {duration:.1f}s: {pkg_count} packages")
            return shard_data
        else:
            log_error(f"Shard {shard_name} failed with exit code {proc.returncode}")
            if proc.stderr:
                log_verbose(f"Shard {shard_name} stderr: {proc.stderr[-200:]}", verbose)
            return None
            
    except subprocess.TimeoutExpired:
        log_error(f"Shard {shard_name} timed out after {timeout//60} minutes")
        return None
    except Exception as e:
        log_error(f"Shard {shard_name} failed with exception: {e}")
        return None

def combine_shard_results(shard_results: List[Dict[str, Any]], out_dir: Path) -> Dict[str, Any]:
    """Combine results from multiple shards into a single dataset"""
    all_packages = []
    shard_metadata = {}
    
    for shard_data in shard_results:
        packages = shard_data.get('packages', [])
        metadata = shard_data.get('metadata', {})
        shard_name = metadata.get('shard_name', 'unknown')
        
        all_packages.extend(packages)
        shard_metadata[shard_name] = {
            'package_count': len(packages),
            'duration_seconds': metadata.get('shard_duration_seconds', 0),
            'extraction_timestamp': metadata.get('extraction_timestamp', '')
        }
    
    # Remove duplicates across shards
    seen_ids = set()
    unique_packages = []
    for pkg in all_packages:
        pkg_id = pkg.get('id')
        if pkg_id and pkg_id not in seen_ids:
            unique_packages.append(pkg)
            seen_ids.add(pkg_id)
    
    log_info(f"Combined {len(all_packages)} packages from shards into {len(unique_packages)} unique packages")
    
    # Use metadata from first shard as base, update with combined info
    base_metadata = shard_results[0].get('metadata', {}) if shard_results else {}
    combined_metadata = {
        **base_metadata,
        'extraction_method': 'sharded',
        'total_shards_processed': len(shard_results),
        'total_packages': len(unique_packages),
        'shard_details': shard_metadata,
        'extraction_timestamp': str(int(time.time()))
    }
    
    return {
        'metadata': combined_metadata,
        'packages': unique_packages
    }

def process_output(script_dir: Path, out_dir: Path, verbose: bool) -> None:
    raw_file = out_dir / "dependencies_raw.json"
    processed_file = out_dir / "dependencies_processed.json"
    log_info("Processing raw output...")
    # Let exceptions propagate; do not silently copy or swallow errors
    _process_dependencies(raw_file, processed_file)
    log_info(f"Processed output saved to: {processed_file}")


def create_summary(out_dir: Path, nixpkgs_path: str, system: str, allow_unfree: bool) -> None:
    summary_file = out_dir / "summary.txt"
    raw_file = out_dir / "dependencies_raw.json"

    lines: List[str] = []
    lines.append("NixGraph Dependency Extraction Summary")
    lines.append("======================================")
    lines.append("")
    lines.append(f"Extraction Date: {_dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Nixpkgs Path: {nixpkgs_path}")
    lines.append(f"System: {system}")
    lines.append(f"Allow Unfree: {str(allow_unfree).lower()}")
    lines.append("")

    # Try to read JSON and surface metadata or basic stats
    try:
        with raw_file.open() as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("metadata"), dict):
            md = data["metadata"]
            lines.append("Results:")
            lines.append("--------")
            lines.append(f"Nixpkgs Version: {md.get('nixpkgs_version', 'unknown')}")
            lines.append(f"Total Packages: {md.get('total_packages', 'unknown')}")
            lines.append(f"Extraction Timestamp: {md.get('extraction_timestamp', 'unknown')}")
        else:
            count = len(data.get("packages", [])) if isinstance(data, dict) else "unknown"
            lines.append("Results:")
            lines.append("--------")
            lines.append(f"Package Count: {count}")
    except Exception:
        pass

    lines.append("")
    lines.append("Output Files:")
    lines.append("-------------")
    try:
        files = sorted(p.name for p in out_dir.glob("**/*") if p.is_file())
        lines.extend(files)
    except Exception:
        pass

    summary_file.write_text("\n".join(lines) + "\n")
    log_info(f"Summary saved to: {summary_file}")
    # Display summary
    try:
        print(summary_file.read_text())
    except Exception:
        pass


# ------------------------- In-memory orchestration API -------------------------


def prioritize_shards(shards: List[str]) -> List[str]:
    """Prioritize shards to process safe ones first, problematic ones last"""
    safe_shards = []
    normal_shards = []
    problematic_shards = []
    
    for shard in shards:
        if shard in {'stdenv', 'coreutils', 'bash', 'gcc'}:
            safe_shards.append(shard)
        elif is_problematic_shard(shard):
            problematic_shards.append(shard)
        else:
            normal_shards.append(shard)
    
    # Process in order: safe -> normal -> problematic
    return safe_shards + normal_shards + problematic_shards

def run_sharded_extraction(cmd: List[str], output_file: Path, log_file: Path, meta: dict, verbose: bool,
                          nix_file: Path, nixpkgs_path: Optional[str], system: str, 
                          allow_unfree: bool, out_dir: Path) -> bool:
    """Run sharded extraction instead of monolithic extraction"""
    log_info("Starting sharded extraction...")
    
    # Check if we should use sharded approach
    sharded_nix_file = nix_file.parent / "extract-deps-sharded.nix"
    if not sharded_nix_file.exists():
        log_error(f"Sharded Nix file not found: {sharded_nix_file}")
        return False
    
    # Discover available shards
    shards = discover_shards(
        nix_file=sharded_nix_file,
        nixpkgs_path=nixpkgs_path,
        system=system,
        allow_unfree=allow_unfree,
        verbose=verbose
    )
    
    if not shards:
        log_error("No shards discovered")
        return False
    
    # Prioritize shard processing order
    prioritized_shards = prioritize_shards(shards)
    log_info(f"Processing {len(prioritized_shards)} shards in priority order")
    
    # Set up resource limits
    setup_resource_limits(verbose)
    
    # Process each shard
    successful_shards = []
    failed_shards = []
    
    for i, shard_name in enumerate(prioritized_shards, 1):
        log_info(f"[{i}/{len(prioritized_shards)}] Processing shard: {shard_name}")
        shard_result = process_shard(
            shard_name=shard_name,
            nix_file=sharded_nix_file,
            nixpkgs_path=nixpkgs_path,
            system=system,
            allow_unfree=allow_unfree,
            verbose=verbose,
            max_depth=10,
            allow_aliases=False,  # Safer default
            out_dir=out_dir
        )
        
        if shard_result:
            successful_shards.append(shard_result)
        else:
            failed_shards.append(shard_name)
            log_error(f"Shard {shard_name} failed, continuing with others...")
            # If too many shards are failing, something might be wrong
            if len(failed_shards) > len(prioritized_shards) // 2:
                log_error(f"More than half of shards have failed ({len(failed_shards)}/{len(prioritized_shards)}), stopping")
                break
    
    if not successful_shards:
        log_error("All shards failed")
        return False
    
    log_info(f"Extraction completed: {len(successful_shards)} successful, {len(failed_shards)} failed")
    if failed_shards:
        log_info(f"Failed shards: {', '.join(failed_shards)}")
    
    # Combine results
    log_info(f"Combining results from {len(successful_shards)} successful shards...")
    combined_data = combine_shard_results(successful_shards, out_dir)
    
    # Save combined results
    try:
        with output_file.open("w") as f:
            json.dump(combined_data, f, indent=2)
        log_info(f"Combined results saved to: {output_file}")
        log_info(f"Total packages extracted: {len(combined_data.get('packages', []))}")
        return True
    except Exception as e:
        log_error(f"Failed to save combined results: {e}")
    return False

def extract_dependencies_data(
    *,
    nixpkgs: Optional[str] = None,
    system: Optional[str] = None,
    allow_unfree: bool = False,
    verbose: bool = False,
    max_depth: int = 10,
    allow_aliases: bool = False,
) -> Dict[str, Any]:
    """Library API: extract dependency data via sharded evaluation only.

    This avoids any CLI entrypoints and file-based contract for callers that
    import this module. Monolithic extraction is intentionally not supported.
    """
    script_dir = Path(__file__).resolve().parent

    setup_resource_limits(verbose)
    which_required(["nix-instantiate", "nix"])  # matches bash check

    system_value = system or current_system(verbose)

    nixpkgs_arg: Optional[str]
    if nixpkgs and not (nixpkgs.startswith("<") and nixpkgs.endswith(">")):
        nixpkgs_arg = str(Path(nixpkgs).resolve())
    else:
        nixpkgs_arg = nixpkgs or os.environ.get("NIXPKGS_PATH", "<nixpkgs>")

    # Use sharded nix file exclusively
    sharded_nix = script_dir / "extract-deps-sharded.nix"
    if not sharded_nix.exists():
        raise RuntimeError(f"Sharded Nix file not found: {sharded_nix}")

    # Discover and prioritize shards
    shards = discover_shards(
        nix_file=sharded_nix,
        nixpkgs_path=nixpkgs_arg,
        system=system_value,
        allow_unfree=allow_unfree,
        verbose=verbose,
    )
    prioritized = prioritize_shards(shards)

    out_dir = Path(tempfile.mkdtemp(prefix="nixgraph_shards_"))
    successful: List[Dict[str, Any]] = []
    failed: List[str] = []

    for shard in prioritized:
        shard_data = process_shard(
            shard,
            sharded_nix,
            nixpkgs_arg,
            system_value,
            allow_unfree,
            verbose,
            max_depth,
            allow_aliases,
            out_dir,
        )
        if shard_data:
            successful.append(shard_data)
        else:
            failed.append(shard)
            if len(failed) > len(prioritized) // 2:
                break

    if not successful:
        raise RuntimeError("Sharded extraction failed for all shards")

    return combine_shard_results(successful, out_dir)
