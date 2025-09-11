import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dependency_graph import DependencyGraphProcessor

logger = logging.getLogger("fdnix.data-processor")


class DataProcessor:
    """Processes raw JSONL data from Stage 1 into structured package and dependency data."""
    
    def __init__(self) -> None:
        self.graph_processor = DependencyGraphProcessor()

    def process_raw_packages(self, raw_packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process raw package data from nix-eval-jobs JSONL into structured format.
        
        Args:
            raw_packages: List of raw package dictionaries from nix-eval-jobs
            
        Returns:
            List of package metadata (for main LanceDB)
        """
        logger.info("Processing %d raw packages from Stage 1...", len(raw_packages))
        
        packages = self._process_package_data(raw_packages)
        
        logger.info("Successfully processed %d packages", len(packages))
        
        return packages
    
    def process_with_dependency_graph(self, raw_packages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Process raw packages and generate comprehensive dependency graph information.
        
        Args:
            raw_packages: List of raw package dictionaries from nix-eval-jobs
            
        Returns:
            Tuple of (packages, graph_data) where:
            - packages: List of package metadata (for LanceDB)
            - graph_data: Comprehensive dependency graph information (for individual node S3 files and stats)
        """
        logger.info("Processing %d raw packages with dependency graph...", len(raw_packages))
        
        # Process standard package data
        packages = self.process_raw_packages(raw_packages)
        
        # Generate comprehensive dependency graph information
        logger.info("Building dependency graph for node S3 files...")
        graph_data = self.graph_processor.process_packages(raw_packages)
        
        logger.info("Successfully processed packages with dependency graph")
        
        return packages, graph_data

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