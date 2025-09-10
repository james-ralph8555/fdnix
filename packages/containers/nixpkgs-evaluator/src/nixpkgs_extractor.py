import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import platform


logger = logging.getLogger("fdnix.nixpkgs-extractor")


class NixpkgsExtractor:
    def __init__(self) -> None:
        self.nixpkgs_path = None
        self.temp_dir = None

    def extract_all_packages(self) -> List[Dict[str, Any]]:
        """Extract package metadata using nix-eval-jobs and return raw JSONL data.
        
        This is a simplified version that only extracts and returns the raw data
        without any processing. The processing will be done in Stage 2.
        
        Returns:
            List of raw package data from nix-eval-jobs
        """
        # Setup nixpkgs repository
        self._setup_nixpkgs_repo()
        
        try:
            logger.info("Extracting packages using nix-eval-jobs...")
            raw_data = self._extract_with_nix_eval_jobs()
            
            logger.info(f"Successfully extracted {len(raw_data)} raw package entries")
            
            return raw_data
        finally:
            self._cleanup_temp_dirs()

    def _setup_nixpkgs_repo(self) -> None:
        """Clone nixpkgs repository and checkout the target branch."""
        import tempfile
        
        # Create temporary directory for nixpkgs
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nixpkgs_"))
        self.nixpkgs_path = self.temp_dir / "nixpkgs"
        
        logger.info("Cloning nixpkgs repository to %s", self.nixpkgs_path)
        
        # Clone nixpkgs with depth 1 for faster cloning
        clone_cmd = [
            "git", "clone", 
            "--depth", "1",
            "--branch", "release-25.05",
            "https://github.com/NixOS/nixpkgs.git",
            str(self.nixpkgs_path)
        ]
        
        try:
            logger.info("Cloning nixpkgs repository with shallow depth (faster clone)...")
            subprocess.run(clone_cmd, check=True, timeout=1200, capture_output=True)
            logger.info("Successfully cloned nixpkgs release-25.05 branch")
            
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode() if e.stderr else str(e)
            raise RuntimeError(f"Failed to clone/update nixpkgs: {error_msg}") from e
        except subprocess.TimeoutExpired:
            raise RuntimeError("Nixpkgs clone timed out after 20 minutes")
    
    def _cleanup_temp_dirs(self) -> None:
        """Clean up temporary directories."""
        if self.temp_dir and self.temp_dir.exists():
            logger.info("Cleaning up temporary directory: %s", self.temp_dir)
            shutil.rmtree(self.temp_dir)
    
    def _extract_with_nix_eval_jobs(self) -> List[Dict[str, Any]]:
        """Extract package data using nix-eval-jobs tool."""
        # Determine target system for nixpkgs
        system = os.environ.get("NIX_SYSTEM") or self._detect_system()

        # Use the Hydra-style evaluation approach as recommended in nix-eval-jobs docs
        release_nix = self.nixpkgs_path / "pkgs" / "top-level" / "release.nix"

        cmd = [
            "nix-eval-jobs",
            "--meta",
            "--show-input-drvs",
            "--force-recurse",
            "--impure",
            "--no-instantiate",
            "--workers",
            "4",
            "--max-memory-size",
            "4096",
            str(release_nix),
        ]

        # Create temporary file for output
        with tempfile.NamedTemporaryFile(mode='w+b', suffix='.jsonl', delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        
        try:
            logger.info("Running: %s", " ".join(cmd))
            logger.info("Writing output to temporary file: %s", tmp_path)
            
            env = os.environ.copy()
            # Ensure unfree packages are allowed during evaluation
            env.setdefault("NIXPKGS_ALLOW_UNFREE", "1")
            # Allow broken packages to prevent evaluation crashes
            env.setdefault("NIXPKGS_ALLOW_BROKEN", "1")
            with tmp_path.open('wb') as output_file:
                proc = subprocess.run(
                    cmd,
                    check=True,
                    stdout=output_file,
                    stderr=subprocess.PIPE,
                    env=env,
                )
            
            # Parse JSONL from file
            packages = self._parse_nix_eval_jobs_output(tmp_path)
            logger.info("Successfully extracted %d packages", len(packages))
            return packages
            
        except subprocess.CalledProcessError as e:
            # Log the actual error details for debugging
            error_details = f"Exit code: {e.returncode}"
            if e.stderr:
                stderr_str = e.stderr.decode('utf-8', errors='replace')
                
                # Check for common recoverable errors
                if "marked as broken" in stderr_str or "refusing to evaluate" in stderr_str:
                    logger.warning("nix-eval-jobs encountered broken packages (this is expected): %s", 
                                 stderr_str.strip()[:500])
                    # Check if we have some output despite broken packages
                    if tmp_path.exists() and tmp_path.stat().st_size > 0:
                        logger.info("Some packages were extracted despite broken package warnings, continuing...")
                        packages = self._parse_nix_eval_jobs_output(tmp_path)
                        if packages:
                            logger.info("Successfully extracted %d packages despite broken package warnings", len(packages))
                            return packages
                
                if "double free or corruption" in stderr_str or "out of memory" in stderr_str:
                    logger.error("nix-eval-jobs crashed with memory corruption: %s", stderr_str.strip())
                    error_details += ", Memory corruption detected"
                else:
                    logger.error("nix-eval-jobs stderr: %s", stderr_str.strip())
                
                error_details += f", Stderr: {stderr_str.strip()[:1000]}"
            else:
                error_details += ", No stderr output"
            
            logger.error("nix-eval-jobs failed: %s", error_details)
            raise RuntimeError(f"nix-eval-jobs failed: {error_details}") from e
        except subprocess.TimeoutExpired:
            raise RuntimeError("nix-eval-jobs timed out after 60 minutes")
        finally:
            # Clean up temporary file
            if tmp_path.exists():
                tmp_path.unlink()

    def _detect_system(self) -> str:
        """Detect Nix system string, defaulting to x86_64-linux."""
        mach = platform.machine().lower()
        if mach in ("x86_64", "amd64"):
            return "x86_64-linux"
        if mach in ("aarch64", "arm64"):
            return "aarch64-linux"
        # Fallback sensible default for most CI/container environments
        return "x86_64-linux"

    def _parse_nix_eval_jobs_output(self, jsonl_file: Path) -> List[Dict[str, Any]]:
        """Parse nix-eval-jobs JSONL output from file with proper encoding handling."""
        packages = []
        
        try:
            # Read file in binary mode first to handle encoding issues
            with jsonl_file.open('rb') as f:
                raw_data = f.read()
            
            # Decode with error handling
            try:
                jsonl_text = raw_data.decode('utf-8')
            except UnicodeDecodeError as e:
                logger.warning("UTF-8 decoding failed at position %d, using error replacement", e.start)
                jsonl_text = raw_data.decode('utf-8', errors='replace')
            
            # Parse JSONL (one JSON object per line)
            for line_num, line in enumerate(jsonl_text.strip().split('\n'), 1):
                if not line.strip():
                    continue
                    
                try:
                    package_data = json.loads(line)
                    packages.append(package_data)
                except json.JSONDecodeError as e:
                    logger.warning("Failed to parse JSON at line %d: %s", line_num, str(e)[:100])
                    continue
            
            return packages
            
        except Exception as e:
            logger.error("Error reading JSONL file %s: %s", jsonl_file, str(e))
            raise RuntimeError(f"Failed to read nix-eval-jobs output file: {e}") from e
    
    def __del__(self):
        """Cleanup on object destruction."""
        self._cleanup_temp_dirs()
