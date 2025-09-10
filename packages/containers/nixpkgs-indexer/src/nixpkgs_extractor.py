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

    def extract_all_packages(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract both package metadata and dependency information using nix-eval-jobs.
        
        Returns:
            Tuple of (packages, dependencies) where:
            - packages: List of package metadata (for main LanceDB)
            - dependencies: List of dependency information (for separate LanceDB table)
        """
        # Setup nixpkgs repository
        self._setup_nixpkgs_repo()
        
        try:
            logger.info("Extracting packages using nix-eval-jobs...")
            raw_data = self._extract_with_nix_eval_jobs()
            
            logger.info("Processing package data...")
            packages = self._process_package_data(raw_data)
            dependencies = self._process_dependency_data(raw_data)
            
            logger.info(f"Successfully extracted {len(packages)} packages and {len(dependencies)} dependency entries")
            
            return packages, dependencies
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
            "--workers",
            "8",
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
                    timeout=3600,
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

    def _process_package_data(self, raw_packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process package data from nix-eval-jobs output."""
        processed: List[Dict[str, Any]] = []
        current_ts = datetime.now(timezone.utc).isoformat()

        for pkg_data in raw_packages:
            try:
                # Extract basic info from nix-eval-jobs output
                attr_path = ".".join(pkg_data.get("attrPath", []))
                name = pkg_data.get("name", "")
                
                # Extract package name and version from the name field
                package_name, version = self._parse_name_version(name)
                if not package_name or package_name == "unknown":
                    logger.debug("Skipping package with unknown name: %s", attr_path)
                    continue

                meta = pkg_data.get("meta", {})

                processed.append({
                    "packageName": package_name,
                    "version": version,
                    "attributePath": attr_path,
                    "description": self._sanitize_string(
                        meta.get("description", "")
                    ),
                    "longDescription": self._sanitize_string(
                        meta.get("longDescription", "")
                    ),
                    "homepage": self._sanitize_string(
                        meta.get("homepage", "")
                    ),
                    "license": self._extract_license_info(
                        meta.get("license")
                    ),
                    "platforms": self._extract_platforms(
                        meta.get("platforms")
                    ),
                    "maintainers": self._extract_maintainers(
                        meta.get("maintainers")
                    ),
                    "category": self._extract_category(attr_path, meta),
                    "broken": bool(meta.get("broken", False)),
                    "unfree": bool(meta.get("unfree", False)),
                    "available": meta.get("available") if "available" in meta else True,
                    "insecure": bool(meta.get("insecure", False)),
                    "unsupported": bool(meta.get("unsupported", False)),
                    "mainProgram": self._sanitize_string(meta.get("mainProgram", "")),
                    "position": self._sanitize_string(meta.get("position", "")),
                    "outputsToInstall": meta.get("outputsToInstall") if isinstance(meta.get("outputsToInstall"), list) else [],
                    "lastUpdated": current_ts,
                    "hasEmbedding": False,
                })

                if len(processed) % 1000 == 0:
                    logger.info("Processed %d packages...", len(processed))

            except Exception as e:  # keep processing
                logger.warning("Error processing package %s: %s", attr_path, e)
                continue

        logger.info("Successfully processed %d packages", len(processed))
        return processed

    def _process_dependency_data(self, raw_packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process dependency data from nix-eval-jobs output."""
        dependencies = []
        current_ts = datetime.now(timezone.utc).isoformat()
        
        logger.info("Processing %d packages for dependency information", len(raw_packages))
        
        for pkg_data in raw_packages:
            try:
                attr_path = ".".join(pkg_data.get("attrPath", []))
                name = pkg_data.get("name", "")
                package_name, version = self._parse_name_version(name)
                
                # Extract dependencies from inputDrvs
                input_drvs = pkg_data.get("inputDrvs", {})
                build_inputs = []
                propagated_inputs = []
                
                # Parse store paths to extract dependency names
                for drv_path in input_drvs.keys():
                    dep_name = self._extract_package_name_from_store_path(drv_path)
                    if dep_name:
                        # For simplicity, treat all inputDrvs as buildInputs
                        # In a more sophisticated implementation, we could distinguish
                        # between build and propagated inputs
                        build_inputs.append(dep_name)
                
                dep_entry = {
                    "packageId": f"{package_name}-{version}",
                    "pname": package_name,
                    "version": version,
                    "attributePath": attr_path,
                    "buildInputs": build_inputs,
                    "propagatedBuildInputs": propagated_inputs,
                    "totalDependencies": len(build_inputs) + len(propagated_inputs),
                    "lastUpdated": current_ts
                }
                
                dependencies.append(dep_entry)
                
            except Exception as e:
                logger.warning("Error processing dependency for %s: %s", 
                             pkg_data.get("name", "unknown"), e)
                continue
        
        logger.info("Successfully processed %d dependency entries", len(dependencies))
        return dependencies

    def _parse_name_version(self, name: str) -> Tuple[str, str]:
        """Parse package name and version from nix-eval-jobs name field."""
        if not name:
            return "unknown", "unknown"
        
        # Nix package names are typically in format "name-version"
        # We need to split carefully as names can contain dashes
        parts = name.split('-')
        if len(parts) < 2:
            return name, "unknown"
        
        # Try to find where version starts (usually a digit or 'v')
        for i, part in enumerate(parts):
            if part and (part[0].isdigit() or part.startswith('v')):
                package_name = '-'.join(parts[:i])
                version = '-'.join(parts[i:])
                return package_name if package_name else name, version
        
        # Fallback: treat last part as version
        return '-'.join(parts[:-1]), parts[-1]

    def _extract_package_name_from_store_path(self, store_path: str) -> Optional[str]:
        """Extract package name from a Nix store path."""
        # Store paths are like "/nix/store/hash-name-version"
        basename = Path(store_path).name
        if '-' in basename:
            # Remove hash prefix and extract name
            parts = basename.split('-', 1)
            if len(parts) > 1:
                name_version = parts[1]
                # Extract just the name part
                name, _ = self._parse_name_version(name_version)
                return name
        return None

    def _sanitize_string(self, s: Any) -> str:
        if not isinstance(s, str):
            return ""
        return (
            s.replace("\x00", "")
            .encode("utf-8", errors="ignore")
            .decode("utf-8")
            .strip()
        )[:2000]

    def _extract_license_info(self, license_obj: Any) -> Optional[Dict[str, Any]]:
        if not license_obj:
            return None

        if isinstance(license_obj, str):
            return {"type": "string", "value": self._sanitize_string(license_obj)}

        if isinstance(license_obj, list):
            return {
                "type": "array",
                "licenses": [
                    lic
                    for lic in (
                        self._extract_single_license(l) for l in license_obj
                    )
                    if lic is not None
                ],
            }

        if isinstance(license_obj, dict):
            return {"type": "object", **(self._extract_single_license(license_obj) or {})}

        return {"type": "string", "value": str(license_obj)[:500]}

    def _extract_single_license(self, license_item: Any) -> Optional[Dict[str, Any]]:
        if not license_item:
            return None
        if isinstance(license_item, str):
            return {
                "shortName": license_item,
                "fullName": "",
                "spdxId": "",
                "url": "",
                "free": None,
                "redistributable": None,
                "deprecated": None,
            }
        if isinstance(license_item, dict):
            return {
                "shortName": self._sanitize_string(license_item.get("shortName", "")),
                "fullName": self._sanitize_string(license_item.get("fullName", "")),
                "spdxId": self._sanitize_string(license_item.get("spdxId", "")),
                "url": self._sanitize_string(license_item.get("url", "")),
                "free": license_item.get("free") if isinstance(license_item.get("free"), bool) else None,
                "redistributable": license_item.get("redistributable") if isinstance(license_item.get("redistributable"), bool) else None,
                "deprecated": license_item.get("deprecated") if isinstance(license_item.get("deprecated"), bool) else None,
            }
        return {
            "shortName": str(license_item),
            "fullName": "",
            "spdxId": "",
            "url": "",
            "free": None,
            "redistributable": None,
            "deprecated": None,
        }

    def _extract_platforms(self, platforms: Any) -> List[Any]:
        if isinstance(platforms, list):
            return platforms[:20]
        return []

    def _extract_maintainers(self, maintainers: Any) -> List[Dict[str, Any]]:
        if not isinstance(maintainers, list):
            return []
        result: List[Dict[str, Any]] = []
        for m in maintainers:
            if isinstance(m, dict):
                entry = {
                    "name": self._sanitize_string(m.get("name", "")),
                    "email": self._sanitize_string(m.get("email", "")),
                    "github": self._sanitize_string(m.get("github", "")),
                    "githubId": m.get("githubId") if isinstance(m.get("githubId"), int) else None,
                }
                if entry["name"] or entry["email"] or entry["github"]:
                    result.append(entry)
            else:
                result.append({
                    "name": str(m),
                    "email": "",
                    "github": "",
                    "githubId": None,
                })
        return result[:10]

    def _extract_category(self, pkg_path: str, meta: Dict[str, Any]) -> str:
        """Extract and classify package category from metadata and attribute path."""
        # Check if meta.category exists first
        if meta.get("category"):
            category = meta["category"]
            return self._normalize_category(category)
        
        # Fallback to attribute path-based classification
        return self._classify_by_attribute_path(pkg_path)

    def _normalize_category(self, category: Any) -> str:
        """Normalize category from nixpkgs metadata to user-friendly name."""
        if not category:
            return "misc"
            
        category_str = str(category).lower()
        
        # Map common nixpkgs categories to user-friendly names
        category_map = {
            "applications.editors": "editors",
            "applications.graphics": "graphics", 
            "applications.networking": "networking",
            "applications.science": "science",
            "applications.system": "system",
            "applications.virtualization": "virtualization",
            "applications.audio": "audio",
            "applications.video": "video",
            "applications.office": "office",
            "applications.misc": "applications",
            "development.tools": "development",
            "development.libraries": "libraries",
            "development.compilers": "compilers",
            "development.interpreters": "interpreters",
            "development.haskell-modules": "haskell",
            "development.python-modules": "python",
            "development.node-packages": "javascript",
            "development.r-modules": "r",
            "development.ocaml-modules": "ocaml",
            "development.perl-modules": "perl",
            "development.ruby-modules": "ruby",
            "games": "games",
            "servers": "servers",
            "tools.system": "system-tools",
            "tools.networking": "networking-tools", 
            "tools.text": "text-tools",
            "tools.misc": "tools",
            "tools.security": "security",
            "tools.filesystems": "filesystems",
            "tools.backup": "backup",
            "data": "data",
            "fonts": "fonts",
            "themes": "themes"
        }
        
        return category_map.get(category_str, category_str)

    def _classify_by_attribute_path(self, pkg_path: str) -> str:
        """Classify package category based on attribute path patterns."""
        path_lower = pkg_path.lower()
        
        # Language-specific packages
        if any(x in path_lower for x in ["python", "python3packages", "python2packages"]):
            return "python"
        if any(x in path_lower for x in ["haskellpackages", "haskell.packages"]):
            return "haskell"
        if "nodepackages" in path_lower or "node_" in path_lower:
            return "javascript"
        if "rpackages" in path_lower:
            return "r"
        if any(x in path_lower for x in ["perlpackages", "perl5", "perl."]):
            return "perl"
        if any(x in path_lower for x in ["rubypackages", "rubygems"]):
            return "ruby"
        if any(x in path_lower for x in ["ocamlpackages", "ocaml-"]):
            return "ocaml"
        if any(x in path_lower for x in ["lua", "luapackages"]):
            return "lua"
        if any(x in path_lower for x in ["go-modules", "buildgomodule"]):
            return "go"
        if "rustpackages" in path_lower or "cargo" in path_lower:
            return "rust"
            
        # Application categories  
        if any(x in path_lower for x in ["editor", "vim", "emacs", "nano", "helix"]):
            return "editors"
        if any(x in path_lower for x in ["browser", "firefox", "chrome", "webkit"]):
            return "browsers"
        if any(x in path_lower for x in ["game", "steam", "lutris"]):
            return "games"
        if any(x in path_lower for x in ["server", "nginx", "apache", "httpd", "postgresql", "mysql"]):
            return "servers"
        if any(x in path_lower for x in ["font", "fonts", "ttf", "otf"]):
            return "fonts"
        if any(x in path_lower for x in ["theme", "gtk", "qt", "icon"]):
            return "themes"
        if any(x in path_lower for x in ["media", "video", "audio", "vlc", "ffmpeg"]):
            return "multimedia"
        if any(x in path_lower for x in ["office", "libreoffice", "document"]):
            return "office"
        if any(x in path_lower for x in ["science", "math", "research", "latex"]):
            return "science"
        if any(x in path_lower for x in ["graphic", "image", "gimp", "inkscape", "photo"]):
            return "graphics"
        if any(x in path_lower for x in ["network", "curl", "wget", "ssh", "tcp"]):
            return "networking"
        if any(x in path_lower for x in ["system", "systemd", "util", "coreutils"]):
            return "system"
        if any(x in path_lower for x in ["security", "crypto", "ssl", "gpg", "password"]):
            return "security"
        if any(x in path_lower for x in ["backup", "rsync", "sync"]):
            return "backup"
        if any(x in path_lower for x in ["filesystem", "fuse", "mount"]):
            return "filesystems"
        if any(x in path_lower for x in ["compiler", "gcc", "clang", "llvm"]):
            return "compilers"
        if any(x in path_lower for x in ["interpreter", "runtime"]):
            return "interpreters"
        if any(x in path_lower for x in ["lib", "library", "shared"]):
            return "libraries"
        if any(x in path_lower for x in ["tool", "util", "cli"]):
            return "tools"
        if any(x in path_lower for x in ["devel", "dev", "build", "make", "cmake"]):
            return "development"
        
        return "misc"
    
    def __del__(self):
        """Cleanup on object destruction."""
        self._cleanup_temp_dirs()
