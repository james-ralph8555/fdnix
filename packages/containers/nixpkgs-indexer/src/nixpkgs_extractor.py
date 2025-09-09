import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import extract_dependencies


logger = logging.getLogger("fdnix.nixpkgs-extractor")


class NixpkgsExtractor:
    def __init__(self) -> None:
        self.nixpkgs_path = None
        self.temp_dir = None

    def extract_all_packages(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract both package metadata and dependency information.
        
        Returns:
            Tuple of (packages, dependencies) where:
            - packages: List of package metadata (for main LanceDB)
            - dependencies: List of dependency information (for separate LanceDB table)
        """
        # Setup nixpkgs repository
        self._setup_nixpkgs_repo()
        
        try:
            logger.info("Extracting package metadata and dependencies...")
            raw_deps = self._extract_dependencies_data()
            raw_packages = self._extract_nix_env_data()
            
            logger.info("Processing and merging package data...")
            packages = self._process_package_data(raw_packages)
            dependencies = self._process_dependency_data(raw_deps)
            
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
    
    def _extract_dependencies_data(self) -> Dict[str, Any]:
        """Extract dependency data using extract-dependencies.py module directly."""
        output_dir = self.temp_dir / "deps_output"
        
        # Create arguments for the extract-dependencies module
        args = [
            "--nixpkgs", str(self.nixpkgs_path),
            "--allow-unfree",
            "--output", str(output_dir),
            "--verbose"
        ]
        
        try:
            logger.info("Running dependency extraction directly...")
            result = extract_dependencies.main(args)
            
            if result != 0:
                raise RuntimeError(f"Dependency extraction failed with exit code {result}")
            
            # Find the output file (should be in a timestamped directory)
            output_files = list(output_dir.glob("*/dependencies_raw.json"))
            if not output_files:
                raise RuntimeError("No dependency output file found")
            
            with open(output_files[0]) as f:
                data = json.load(f)
                
            logger.info("Successfully extracted dependencies data")
            return data
            
        except Exception as e:
            logger.error("Dependency extraction failed: %s", str(e))
            raise RuntimeError(f"Dependency extraction failed: {str(e)}") from e
    
    def _extract_nix_env_data(self) -> Dict[str, Any]:
        """Extract package metadata using nix-env (for compatibility)."""
        cmd = [
            "nix-env",
            "-qaP",
            "--json",
            "--meta",
            "-f", str(self.nixpkgs_path)
        ]

        attempt = 0
        while True:
            attempt += 1
            try:
                logger.info("Running: %s", " ".join(cmd))
                proc = subprocess.run(
                    cmd, check=True, timeout=1800, capture_output=True, text=True
                )
                data = json.loads(proc.stdout)
                logger.info("Successfully extracted %d packages", len(data))
                return data
            except subprocess.CalledProcessError as e:
                # Log the actual error details for debugging
                error_details = f"Exit code: {e.returncode}"
                if e.stdout:
                    logger.error("nix-env stdout: %s", e.stdout.strip())
                if e.stderr:
                    stderr_str = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr)
                    logger.error("nix-env stderr: %s", stderr_str.strip())
                    error_details += f", Stderr: {stderr_str.strip()}"
                else:
                    error_details += ", No stderr output"
                
                if attempt < self.max_retries:
                    logger.warning(
                        "nix-env failed (attempt %d/%d): %s. Retrying in %ss...",
                        attempt, self.max_retries, error_details, self.retry_delay_sec
                    )
                    time.sleep(self.retry_delay_sec)
                    continue
                
                # For final attempt, provide comprehensive error info
                logger.error("Final nix-env attempt failed: %s", error_details)
                raise RuntimeError(f"nix-env failed after {self.max_retries} attempts: {error_details}") from e
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Failed to parse nix-env JSON output: {e}") from e

    def _process_package_data(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        processed: List[Dict[str, Any]] = []
        current_ts = datetime.now(timezone.utc).isoformat()

        for pkg_path, pkg_info in raw.items():
            try:
                package_name = (
                    pkg_info.get("pname") or self._extract_package_name_from_path(pkg_path)
                )
                version = pkg_info.get("version") or "unknown"
                if not package_name or package_name == "unknown":
                    logger.debug("Skipping package with unknown name: %s", pkg_path)
                    continue

                meta = pkg_info.get("meta") or {}

                processed.append(
                    {
                        "packageName": package_name,
                        "version": version,
                        "attributePath": pkg_path,
                        "description": self._sanitize_string(
                            meta.get("description") or pkg_info.get("description") or ""
                        ),
                        "longDescription": self._sanitize_string(
                            meta.get("longDescription") or ""
                        ),
                        "homepage": self._sanitize_string(
                            meta.get("homepage") or pkg_info.get("homepage") or ""
                        ),
                        "license": self._extract_license_info(
                            meta.get("license") or pkg_info.get("license")
                        ),
                        "platforms": self._extract_platforms(
                            meta.get("platforms") or pkg_info.get("platforms")
                        ),
                        "maintainers": self._extract_maintainers(
                            meta.get("maintainers") or pkg_info.get("maintainers")
                        ),
                        "category": self._extract_category(pkg_path, meta),
                        "broken": bool(meta.get("broken") or pkg_info.get("broken") or False),
                        "unfree": bool(meta.get("unfree") or pkg_info.get("unfree") or False),
                        "available": meta.get("available") if "available" in meta else True,
                        "insecure": bool(meta.get("insecure") or False),
                        "unsupported": bool(meta.get("unsupported") or False),
                        "mainProgram": self._sanitize_string(meta.get("mainProgram") or ""),
                        "position": self._sanitize_string(meta.get("position") or ""),
                        "outputsToInstall": meta.get("outputsToInstall") if isinstance(meta.get("outputsToInstall"), list) else [],
                        "lastUpdated": current_ts,
                        "hasEmbedding": False,
                    }
                )

                if len(processed) % 1000 == 0:
                    logger.info("Processed %d packages...", len(processed))

            except Exception as e:  # keep processing
                logger.warning("Error processing package %s: %s", pkg_path, e)
                continue

        logger.info("Successfully processed %d packages", len(processed))
        return processed

    def _extract_package_name_from_path(self, pkg_path: str) -> str:
        parts = pkg_path.split(".")
        return parts[-1] if parts else ""

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
                "shortName": self._sanitize_string(license_item.get("shortName") or ""),
                "fullName": self._sanitize_string(license_item.get("fullName") or ""),
                "spdxId": self._sanitize_string(license_item.get("spdxId") or ""),
                "url": self._sanitize_string(license_item.get("url") or ""),
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
                    "name": self._sanitize_string(m.get("name") or ""),
                    "email": self._sanitize_string(m.get("email") or ""),
                    "github": self._sanitize_string(m.get("github") or ""),
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
    
    def _process_dependency_data(self, raw_deps: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process dependency data from extract-dependencies.py output."""
        packages = raw_deps.get('packages', [])
        processed_deps = []
        
        logger.info("Processing %d dependency entries", len(packages))
        
        for pkg in packages:
            try:
                # Extract dependency information
                build_inputs = pkg.get('buildInputs', [])
                propagated_inputs = pkg.get('propagatedBuildInputs', [])
                
                dep_entry = {
                    "packageId": pkg.get('id', ''),
                    "pname": pkg.get('pname', ''),
                    "version": pkg.get('version', ''),
                    "attributePath": pkg.get('attrPath', ''),
                    "buildInputs": self._normalize_dep_list(build_inputs),
                    "propagatedBuildInputs": self._normalize_dep_list(propagated_inputs),
                    "totalDependencies": len(build_inputs) + len(propagated_inputs),
                    "lastUpdated": datetime.now(timezone.utc).isoformat()
                }
                
                processed_deps.append(dep_entry)
                
            except Exception as e:
                logger.warning("Error processing dependency for %s: %s", 
                             pkg.get('id', 'unknown'), e)
                continue
        
        logger.info("Successfully processed %d dependency entries", len(processed_deps))
        return processed_deps
    
    def _normalize_dep_list(self, deps: List[str]) -> List[str]:
        """Normalize dependency list to extract package names."""
        normalized = []
        for dep in deps:
            if isinstance(dep, str) and dep.strip():
                # Extract package name from store path
                # e.g., "/nix/store/abc123-hello-1.0" -> "hello"
                parts = dep.split('/')[-1]  # Get basename
                if '-' in parts:
                    # Remove hash prefix
                    name_version = '-'.join(parts.split('-')[1:])
                    # Try to extract just the name part
                    if '-' in name_version:
                        name = name_version.split('-')[0]
                    else:
                        name = name_version
                    normalized.append(name)
        return normalized
    
    def __del__(self):
        """Cleanup on object destruction."""
        self._cleanup_temp_dirs()
