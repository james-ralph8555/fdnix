#!/usr/bin/env python3

"""
NixGraph: Extract dependencies from nixpkgs (Python port)

This is a Python equivalent of extract-dependencies.sh. It wraps the Nix
evaluation, handles output/logs, and optionally post-processes the results.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


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
    test_mode: bool,
    verbose: bool,
) -> List[str]:
    base = [
        "nix-instantiate",
        "--eval",
        "--json",
        "--strict",
    ]

    # In test mode, the Nix file is self-contained (no args passed)
    if test_mode:
        cmd = [*base, str(nix_file)]
        log_verbose(f"Using test mode nix command: {' '.join(cmd)}", verbose)
        return cmd

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

    # Add verbose trace for easier debugging
    if verbose:
        cmd.append("--show-trace")

    cmd.append(str(nix_file))

    log_verbose(f"Built nix command: {' '.join(cmd)}", verbose)
    return cmd


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
    with output_file.open("w") as out, log_file.open("a") as err:
        proc = subprocess.run(cmd, stdout=out, stderr=err, text=True)

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
        log_error(f"Extraction failed. Check log file: {log_file}")
        return False


def process_output(script_dir: Path, out_dir: Path, verbose: bool) -> None:
    raw_file = out_dir / "dependencies_raw.json"
    processed_file = out_dir / "dependencies_processed.json"
    proc_script = script_dir / "scripts" / "process-deps.py"

    if proc_script.exists():
        log_info("Processing raw output...")
        cmd = [sys.executable or "python3", str(proc_script), str(raw_file), str(processed_file)]
        log_verbose(f"Running: {' '.join(cmd)}", verbose)
        try:
            subprocess.run(cmd, check=True)
            log_info(f"Processed output saved to: {processed_file}")
        except subprocess.CalledProcessError:
            log_error("Processing failed, but raw output is available")
    else:
        log_verbose("No processing script found, copying raw output", verbose)
        try:
            shutil.copy2(raw_file, processed_file)
        except Exception as e:
            log_error(f"Failed to copy raw output to processed: {e}")


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


# ------------------------- CLI -------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    # Defaults from environment (matching the bash script semantics)
    env_nixpkgs = os.environ.get("NIXPKGS_PATH", "<nixpkgs>")
    env_output = os.environ.get("OUTPUT_DIR", "./output")
    env_verbose = os.environ.get("VERBOSE", "false").lower() == "true"
    env_allow_unfree = os.environ.get("ALLOW_UNFREE", "false").lower() == "true"

    parser = argparse.ArgumentParser(
        prog="extract-dependencies.py",
        description=(
            "NixGraph Dependency Extractor (Python)\n\n"
            "Extract runtime dependency information from nixpkgs packages without building them."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "-p",
        "--nixpkgs",
        dest="nixpkgs",
        default=env_nixpkgs,
        help="Path to nixpkgs (default: <nixpkgs>)",
    )
    parser.add_argument(
        "-s",
        "--system",
        dest="system",
        default=os.environ.get("SYSTEM"),
        help="Target system (default: current system)",
    )
    parser.add_argument(
        "-u",
        "--allow-unfree",
        dest="allow_unfree",
        action="store_true",
        default=env_allow_unfree,
        help="Allow unfree packages",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_dir",
        default=env_output,
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="store_true",
        default=env_verbose,
        help="Verbose output",
    )
    parser.add_argument(
        "--raw",
        dest="raw_only",
        action="store_true",
        help="Output raw JSON without processing",
    )
    parser.add_argument(
        "--test",
        dest="test_mode",
        action="store_true",
        help="Run with a small subset for testing",
    )

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    script_dir = Path(__file__).resolve().parent

    # Check dependencies early
    which_required(["nix-instantiate", "nix"])  # matches bash check

    # Determine system default if not provided
    system_value = args.system or current_system(args.verbose)

    # Compose output dir
    base_out = Path(args.output_dir)
    out_dir = timestamp_dir(base_out)
    log_info(f"Created output directory: {out_dir}")

    nix_file = script_dir / ("test-working.nix" if args.test_mode else "extract-deps.nix")
    if not nix_file.exists():
        log_error(f"Nix expression not found: {nix_file}")
        return 1

    # nixpkgs path handling: for CLI -p we mirror bash behavior by realpathing
    nixpkgs_arg: Optional[str]
    if args.nixpkgs and not (args.nixpkgs.startswith("<") and args.nixpkgs.endswith(">")):
        nixpkgs_arg = str(Path(args.nixpkgs).resolve())
    else:
        nixpkgs_arg = args.nixpkgs

    log_verbose(f"Nixpkgs path: {nixpkgs_arg}", args.verbose)
    log_verbose(f"System: {system_value}", args.verbose)
    log_verbose(f"Allow unfree: {str(args.allow_unfree).lower()}", args.verbose)

    output_file = out_dir / "dependencies_raw.json"
    log_file = out_dir / "extraction.log"

    cmd = build_nix_cmd(
        nix_file=nix_file,
        nixpkgs_path=nixpkgs_arg,
        system=system_value,
        allow_unfree=args.allow_unfree,
        test_mode=args.test_mode,
        verbose=args.verbose,
    )

    meta = {
        "nixpkgs_path": nixpkgs_arg,
        "system": system_value,
        "allow_unfree": args.allow_unfree,
    }

    ok = run_extraction(cmd, output_file, log_file, meta, args.verbose)
    if not ok:
        return 1

    if not args.raw_only:
        process_output(script_dir, out_dir, args.verbose)

    create_summary(out_dir, nixpkgs_arg or "<nixpkgs>", system_value, args.allow_unfree)

    log_info("Extraction completed")
    log_info(f"All outputs saved to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
