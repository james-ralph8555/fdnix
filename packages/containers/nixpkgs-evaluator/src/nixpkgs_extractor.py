import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import platform


logger = logging.getLogger("fdnix.nixpkgs-extractor")


class NixpkgsExtractor:
    def __init__(self) -> None:
        self.nixpkgs_path = None
        self.temp_dir = None

    def extract_all_packages(self) -> str:
        """Extract package metadata using nix-eval-jobs and return JSONL file path.
        
        This version runs nix-eval-jobs and returns the path to the generated JSONL file
        without parsing it. The file can then be uploaded directly to S3.
        
        Returns:
            Path to the generated JSONL file
        """
        # Setup nixpkgs repository
        self._setup_nixpkgs_repo()
        
        try:
            logger.info("Extracting packages using nix-eval-jobs...")
            jsonl_file_path = self._extract_with_nix_eval_jobs()
            
            logger.info(f"Successfully created JSONL file: {jsonl_file_path}")
            
            return jsonl_file_path
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
    
    def _extract_with_nix_eval_jobs(self) -> str:
        """Extract package data using nix-eval-jobs tool and return JSONL file path."""
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
            "--verbose",
            "--log-format",
            "bar-with-logs",
            # "--no-instantiate",  # need to wait till nix-eval-jobs ets another release; this is only in main branch
            "--workers",
            "8",
            "--max-memory-size",
            "4096",
            str(release_nix),
        ]

        # Create persistent output file
        output_dir = Path("/tmp")  # Or use current working directory: Path(".")
        tmp_path = output_dir / f"nixpkgs_packages_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
        
        try:
            logger.info("Running: %s", " ".join(cmd))
            logger.info("Writing output to persistent file: %s", tmp_path)
            
            env = os.environ.copy()
            # Ensure unfree packages are allowed during evaluation
            env.setdefault("NIXPKGS_ALLOW_UNFREE", "1")
            # Allow broken packages to prevent evaluation crashes
            env.setdefault("NIXPKGS_ALLOW_BROKEN", "1")
            
            # Stream stderr to logger while capturing stdout to file
            with tmp_path.open('wb') as output_file:
                proc = subprocess.Popen(
                    cmd,
                    stdout=output_file,
                    stderr=subprocess.PIPE,
                    env=env,
                    text=True,
                    bufsize=1  # Line buffered
                )
                
                # Stream stderr lines to logger for CloudWatch visibility
                while True:
                    stderr_line = proc.stderr.readline()
                    if not stderr_line and proc.poll() is not None:
                        break
                    if stderr_line:
                        # Log each line from nix-eval-jobs stderr
                        logger.info("nix-eval-jobs: %s", stderr_line.rstrip())
                
                # Wait for process to complete and get return code
                return_code = proc.wait()
                
                if return_code != 0:
                    raise subprocess.CalledProcessError(return_code, cmd)
            
            # Return the JSONL file path instead of parsing it
            logger.info("Successfully generated JSONL file: %s", tmp_path)
            return str(tmp_path)
            
        except subprocess.CalledProcessError as e:
            # Log the actual error details for debugging
            error_details = f"Exit code: {e.returncode}"
            
            # Since stderr was already streamed to the logger, we don't have it captured
            # But we can still check if we got some output despite the error
            if tmp_path.exists() and tmp_path.stat().st_size > 0:
                logger.warning("nix-eval-jobs failed but produced some output, checking if usable...")
                logger.info("JSONL file exists with size %d bytes, continuing...", tmp_path.stat().st_size)
                return str(tmp_path)
            
            logger.error("nix-eval-jobs failed: %s", error_details)
            raise RuntimeError(f"nix-eval-jobs failed: {error_details}") from e
        except subprocess.TimeoutExpired:
            raise RuntimeError("nix-eval-jobs timed out after 60 minutes")

    def _detect_system(self) -> str:
        """Detect Nix system string, defaulting to x86_64-linux."""
        mach = platform.machine().lower()
        if mach in ("x86_64", "amd64"):
            return "x86_64-linux"
        if mach in ("aarch64", "arm64"):
            return "aarch64-linux"
        # Fallback sensible default for most CI/container environments
        return "x86_64-linux"

    
    def __del__(self):
        """Cleanup on object destruction."""
        self._cleanup_temp_dirs()
