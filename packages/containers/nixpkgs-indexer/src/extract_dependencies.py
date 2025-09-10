#!/usr/bin/env python3

"""
NixGraph: Extract dependencies from nixpkgs (Python port)

This is a Python equivalent of extract-dependencies.sh. It wraps the Nix
evaluation, handles output/logs, and optionally post-processes the results.
"""

from __future__ import annotations
import datetime as _dt
import json
import logging
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

# Setup logger
logger = logging.getLogger("fdnix.extract-dependencies")


# ------------------------- Logging utilities -------------------------

def log_info(msg: str) -> None:
    logger.info(msg)


def log_error(msg: str) -> None:
    logger.error(msg)


def log_verbose(msg: str, verbose: bool) -> None:
    if verbose:
        logger.debug(msg)


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
        res = subprocess.run(cmd, check=True, capture_output=True)
        stdout_text = res.stdout.decode('utf-8', errors='replace')
        return stdout_text.strip().strip('"')
    except subprocess.CalledProcessError as e:
        log_error("Failed to detect current system")
        stderr_text = e.stderr.decode('utf-8', errors='replace')
        log_error(stderr_text.strip())
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
            # Pass the angle-bracket path expression directly (e.g. <nixpkgs>)
            cmd += ["--arg", "nixpkgs", value]
        else:
            # Pass as a Nix path value, not a string, so 'import nixpkgs' works
            abs_path = str(Path(value).resolve())
            cmd += ["--arg", "nixpkgs", abs_path]

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


def setup_resource_limits(verbose: bool, memory_intensive: bool = False) -> None:
    """Setup system resource limits to prevent crashes"""
    import resource
    
    try:
        # Set stack size - larger for memory intensive shards
        if memory_intensive:
            stack_size = 32 * 1024 * 1024  # 32MB for memory intensive shards
        else:
            stack_size = 16 * 1024 * 1024  # 16MB for normal shards
        
        resource.setrlimit(resource.RLIMIT_STACK, (stack_size, stack_size))
        log_verbose(f"Set stack size limit to {stack_size // (1024*1024)}MB (memory_intensive={memory_intensive})", verbose)
        
        # Try to increase virtual memory limit if possible
        try:
            current_vmem = resource.getrlimit(resource.RLIMIT_AS)
            if current_vmem[0] != resource.RLIM_INFINITY:
                # Increase virtual memory to 8GB for memory intensive shards
                if memory_intensive:
                    new_vmem = min(8 * 1024 * 1024 * 1024, current_vmem[1])  # 8GB or max allowed
                    resource.setrlimit(resource.RLIMIT_AS, (new_vmem, current_vmem[1]))
                    log_verbose(f"Set virtual memory limit to {new_vmem // (1024*1024*1024)}GB", verbose)
        except (OSError, ValueError) as e:
            log_verbose(f"Could not adjust virtual memory limit: {e}", verbose)
        
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
    
    # Write output directly to file, capture only stderr
    try:
        with output_file.open("wb") as out, log_file.open("a") as err_log:
            proc = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE)
            
            # Write stderr to log file if present
            if proc.stderr:
                stderr_text = proc.stderr.decode('utf-8', errors='replace')
                err_log.write("\n--- STDERR ---\n")
                err_log.write(stderr_text)
                err_log.write("\n--- END STDERR ---\n")
                
                # Also output to logger for CloudWatch/container logs
                if stderr_text.strip():
                    log_error("nix-instantiate stderr output:")
                    for line in stderr_text.strip().split('\n'):
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
            with output_file.open('rb') as f:
                raw_data = f.read()
            # Decode with error handling
            try:
                json_text = raw_data.decode('utf-8')
            except UnicodeDecodeError:
                json_text = raw_data.decode('utf-8', errors='replace')
            data = json.loads(json_text)
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
        result = subprocess.run(cmd, capture_output=True, check=True)
        # Decode stdout with error handling
        stdout_text = result.stdout.decode('utf-8', errors='replace')
        data = json.loads(stdout_text)
        shards = data.get('availableShards', [])
        # Show all shards without truncation as requested
        if len(shards) <= 10:
            log_info(f"Discovered {len(shards)} shards: {', '.join(shards)}")
        else:
            log_info(f"Discovered {len(shards)} shards:")
            for i, shard in enumerate(shards):
                log_info(f"  [{i+1:2d}] {shard}")
        return shards
    except subprocess.CalledProcessError as e:
        log_error("Failed to discover shards: nix-instantiate exited non-zero")
        if e.stderr:
            stderr_text = e.stderr.decode('utf-8', errors='replace')
            log_error("nix-instantiate stderr (discovery):")
            for line in stderr_text.strip().splitlines():
                if line.strip():
                    log_error(f"  {line}")
        if e.stdout:
            try:
                stdout_text = e.stdout.decode('utf-8', errors='replace')
                preview = stdout_text.strip()[:500]
                if preview:
                    log_verbose(f"stdout preview: {preview}", verbose)
            except Exception:
                pass
        # Fail hard if discovery fails - no fallback shards
        return []
    except json.JSONDecodeError as e:
        log_error(f"Failed to parse shard discovery JSON: {e}")
        return []

def is_problematic_shard(shard_name: str) -> bool:
    """Check if a shard is known to be problematic and should be processed with extra care"""
    # With by-name sharding, large individual prefixes are the main problematic shards
    large_prefix_shards = {
        'byname_large_li',  # 948 packages - largest shard
        'byname_large_co',  # 284 packages
        'byname_large_ca',  # 284 packages
    }
    return shard_name in large_prefix_shards

def is_memory_intensive_shard(shard_name: str) -> bool:
    """Check if a shard requires extra memory and processing time"""
    # All large prefix shards are memory intensive by definition
    large_prefix_shards = {
        'byname_large_li',  # 948 packages
        'byname_large_co',  # 284 packages  
        'byname_large_ca',  # 284 packages
        'byname_large_go',  # 263 packages
        'byname_large_ma',  # 221 packages
        'byname_large_op',  # 216 packages
        'byname_large_re',  # 214 packages
    }
    return shard_name in large_prefix_shards

def process_shard_with_fallback(shard_name: str, nix_file: Path, nixpkgs_path: Optional[str], 
                               system: str, allow_unfree: bool, verbose: bool, 
                               max_depth: int, allow_aliases: bool, out_dir: Path) -> Optional[Dict[str, Any]]:
    """Process a shard with fallback strategies if initial attempt fails."""
    
    # Strategy 1: Normal processing
    result = process_shard(shard_name, nix_file, nixpkgs_path, system, allow_unfree, 
                          verbose, max_depth, allow_aliases, out_dir)
    if result is not None:
        return result
    
    # Strategy 2: Conservative fallback for problematic shards
    if is_problematic_shard(shard_name) or is_memory_intensive_shard(shard_name):
        log_info(f"Attempting fallback strategy for {shard_name} with reduced parameters...")
        fallback_result = process_shard(shard_name, nix_file, nixpkgs_path, system, allow_unfree, 
                                       verbose, 3, False, out_dir)
        if fallback_result is not None:
            log_info(f"Fallback strategy succeeded for {shard_name}")
            return fallback_result
    
    # Strategy 3: Skip and log for manual investigation
    log_error(f"All strategies failed for shard {shard_name} - requires manual investigation")
    return None

def process_shard(shard_name: str, nix_file: Path, nixpkgs_path: Optional[str], 
                 system: str, allow_unfree: bool, verbose: bool, 
                 max_depth: int, allow_aliases: bool, out_dir: Path) -> Optional[Dict[str, Any]]:
    """Process a single shard and return its data"""
    log_info(f"Processing shard: {shard_name}")
    
    # Check shard characteristics
    is_problematic = is_problematic_shard(shard_name)
    is_memory_intensive = is_memory_intensive_shard(shard_name)
    
    # Adjust parameters for problematic shards
    if is_problematic:
        log_verbose(f"Shard {shard_name} is problematic, using conservative settings", verbose)
        max_depth = min(max_depth, 5)  # Reduce depth for problematic shards
        allow_aliases = False  # Never allow aliases for problematic shards
        
    # Set timeout based on shard type - by-name shards should be much faster
    if shard_name.startswith('byname_large_li'):
        timeout = 900   # 15 minutes for the largest shard only
        log_verbose(f"Shard {shard_name} is the largest prefix, using extended timeout", verbose)
    elif is_memory_intensive:
        timeout = 600   # 10 minutes for other large prefix shards
        log_verbose(f"Shard {shard_name} is a large prefix, using moderate timeout", verbose)
    elif shard_name.startswith('byname_group_'):
        timeout = 180   # 3 minutes for grouped shards (should be fast)
        log_verbose(f"Shard {shard_name} is a grouped shard, using short timeout", verbose)
    else:
        timeout = 300   # 5 minute default timeout
    
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
    
    # Set up resource limits based on shard type
    setup_resource_limits(verbose, is_memory_intensive)
    
    # Set up logging for this shard
    header = [
        f"# Shard Processing Log: {shard_name}",
        f"# Started: {_dt.datetime.now().isoformat(timespec='seconds')}",
        f"# Memory Intensive: {is_memory_intensive}",
        f"# Problematic: {is_problematic}",
        f"# Timeout: {timeout}s",
        f"# Command: {' '.join(cmd)}",
        ""
    ]
    shard_log.write_text("\n".join(header))
    
    start_time = time.time()
    try:
        with shard_file.open("wb") as out:
            proc = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, 
                                timeout=timeout)
            
        stderr_text = ""
        if proc.stderr:
            stderr_text = proc.stderr.decode('utf-8', errors='replace')
            with shard_log.open("a") as err:
                err.write("\n--- STDERR ---\n")
                err.write(stderr_text)
                err.write("\n--- END STDERR ---\n")
        
        if proc.returncode == 0:
            # Load and return the shard data with proper encoding handling
            try:
                with shard_file.open('rb') as f:
                    raw_data = f.read()
                # Decode with error handling
                try:
                    json_text = raw_data.decode('utf-8')
                except UnicodeDecodeError:
                    json_text = raw_data.decode('utf-8', errors='replace')
                shard_data = json.loads(json_text)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                log_error(f"Shard {shard_name} produced invalid JSON: {str(e)[:100]}")
                return None
            
            duration = time.time() - start_time
            pkg_count = len(shard_data.get('packages', []))
            log_info(f"Shard {shard_name} completed in {duration:.1f}s: {pkg_count} packages")
            return shard_data
        else:
            failure_reason = _categorize_shard_failure(proc.returncode, stderr_text)
            log_error(f"Shard {shard_name} failed: {failure_reason}")
            if verbose and stderr_text:
                # Only show stderr details in verbose mode
                stderr_preview = stderr_text[-500:] if len(stderr_text) > 500 else stderr_text
                log_verbose(f"Shard {shard_name} stderr preview: {stderr_preview}", verbose)
            return None
            
    except subprocess.TimeoutExpired:
        duration_mins = timeout // 60
        log_error(f"Shard {shard_name} timed out after {duration_mins} minutes (memory-intensive: {is_memory_intensive})")
        return None
    except Exception as e:
        error_type = type(e).__name__
        log_error(f"Shard {shard_name} failed with {error_type}: {str(e)[:200]}")
        return None

def _categorize_shard_failure(exit_code: int, stderr: str) -> str:
    """Categorize shard failure based on exit code and stderr content."""
    if not stderr:
        return f"exit code {exit_code} (no error details)"
    
    stderr_lower = stderr.lower()
    
    # Common failure patterns
    if "stack overflow" in stderr_lower or "segmentation fault" in stderr_lower:
        return f"memory/stack overflow (exit {exit_code})"
    elif "killed" in stderr_lower or exit_code == -9:
        return f"process killed (likely OOM, exit {exit_code})"
    elif "out of memory" in stderr_lower or "cannot allocate memory" in stderr_lower:
        return f"out of memory (exit {exit_code})"
    elif "assertion" in stderr_lower:
        return f"assertion failure (exit {exit_code})"
    elif "infinite recursion" in stderr_lower:
        return f"infinite recursion (exit {exit_code})"
    elif "evaluation aborted" in stderr_lower:
        return f"evaluation aborted (exit {exit_code})"
    elif "error:" in stderr_lower:
        # Extract first error line
        error_lines = [line.strip() for line in stderr.split('\n') if 'error:' in line.lower()]
        if error_lines:
            first_error = error_lines[0][:100]  # Limit length
            return f"evaluation error: {first_error} (exit {exit_code})"
    
    return f"exit code {exit_code}"

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
        logger.info("NixGraph Dependency Extraction Summary:")
        for line in lines:
            if line.strip():
                logger.info(line)
    except Exception:
        pass


# ------------------------- In-memory orchestration API -------------------------


def prioritize_shards(shards: List[str]) -> List[str]:
    """Prioritize shards to process grouped shards first, then large shards last"""
    grouped_shards = []
    large_shards = []
    problematic_shards = []
    
    for shard in shards:
        if shard.startswith('byname_group_'):
            # Grouped shards are smaller and faster, process first
            grouped_shards.append(shard)
        elif shard.startswith('byname_large_'):
            if is_problematic_shard(shard):
                # Very large shards that need special handling
                problematic_shards.append(shard)
            else:
                # Regular large shards
                large_shards.append(shard)
        else:
            # Any remaining shards (shouldn't exist with by-name sharding)
            grouped_shards.append(shard)
    
    # Process in order: grouped (fast) -> large (medium) -> problematic (slow)
    # Sort within each category for consistent ordering
    return (sorted(grouped_shards) + 
            sorted(large_shards) + 
            sorted(problematic_shards))

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
    log_info(f"Processing {len(prioritized_shards)} by-name shards in priority order:")
    for i, shard in enumerate(prioritized_shards):
        if shard.startswith('byname_group_'):
            shard_type = "grouped"
        elif shard.startswith('byname_large_'):
            if is_problematic_shard(shard):
                shard_type = "large-problematic"
            else:
                shard_type = "large"
        else:
            shard_type = "unknown"
        log_info(f"  [{i+1:2d}/{len(prioritized_shards)}] {shard} ({shard_type})")
    
    # Set up resource limits
    setup_resource_limits(verbose)
    
    # Process each shard
    successful_shards = []
    failed_shards = []
    
    for i, shard_name in enumerate(prioritized_shards, 1):
        log_info(f"[{i}/{len(prioritized_shards)}] Processing shard: {shard_name}")
        shard_result = process_shard_with_fallback(
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
            # If too many shards are failing, something might be fundamentally wrong
            failure_rate = len(failed_shards) / (len(successful_shards) + len(failed_shards))
            if len(failed_shards) > len(prioritized_shards) // 2:
                log_error(f"High failure rate: {len(failed_shards)}/{len(prioritized_shards)} shards failed ({failure_rate:.1%})")
                log_error("Stopping early due to excessive failures - check system resources and Nix configuration")
                break
    
    if not successful_shards:
        log_error("All shards failed")
        return False
    
    # Generate extraction summary
    total_extracted_packages = sum(len(shard.get('packages', [])) for shard in successful_shards)
    log_info(f"Extraction summary:")
    log_info(f"  ✓ Successful shards: {len(successful_shards)}")
    log_info(f"  ✗ Failed shards: {len(failed_shards)}")
    log_info(f"  📦 Total packages extracted: {total_extracted_packages}")
    
    if failed_shards:
        log_info(f"Failed shards requiring investigation ({len(failed_shards)}):")
        for i, failed_shard in enumerate(failed_shards):
            if failed_shard.startswith('byname_group_'):
                shard_type = "grouped"
            elif failed_shard.startswith('byname_large_'):
                if is_problematic_shard(failed_shard):
                    shard_type = "large-problematic"
                else:
                    shard_type = "large"
            else:
                shard_type = "unknown"
            log_info(f"  [{i+1:2d}] {failed_shard} ({shard_type})")
    
    # Combine results
    log_info(f"Combining results from {len(successful_shards)} successful shards...")
    combined_data = combine_shard_results(successful_shards, out_dir)
    
    # Save combined results and perform validation
    try:
        with output_file.open("w") as f:
            json.dump(combined_data, f, indent=2)
        
        final_package_count = len(combined_data.get('packages', []))
        log_info(f"Combined results saved to: {output_file}")
        log_info(f"Total packages extracted: {final_package_count}")
        
        # Log discovery statistics for validation
        metadata = combined_data.get('metadata', {})
        if metadata.get('discovery_method') == 'dynamic':
            log_info(f"Dynamic shard discovery results:")
            log_info(f"  📊 Total shards discovered: {metadata.get('total_shards_processed', 0)}")
            log_info(f"  🎯 Packages per successful shard (avg): {final_package_count // len(successful_shards) if successful_shards else 0}")
            
            # Show breakdown by shard type if available
            shard_details = metadata.get('shard_details', {})
            if shard_details:
                log_info(f"  📋 Shard breakdown:")
                for shard_name, shard_info in sorted(shard_details.items()):
                    pkg_count = shard_info.get('package_count', 0)
                    duration = shard_info.get('duration_seconds', 0)
                    log_info(f"    • {shard_name}: {pkg_count} packages ({duration:.1f}s)")
        
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
        shard_data = process_shard_with_fallback(
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
