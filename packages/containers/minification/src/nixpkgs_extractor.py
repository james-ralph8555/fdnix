import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


logger = logging.getLogger("fdnix.nixpkgs-extractor")


class NixpkgsExtractor:
    def __init__(self) -> None:
        self.max_retries = 3
        self.retry_delay_sec = 5

    def extract_all_packages(self) -> List[Dict[str, Any]]:
        logger.info("Extracting package metadata using nix-env...")
        raw = self._extract_raw_package_data()

        logger.info("Processing and cleaning package data...")
        return self._process_package_data(raw)

    def _extract_raw_package_data(self) -> Dict[str, Any]:
        cmd = [
            "nix-env",
            "-qaP",
            "--json",
            "--meta",
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
                if attempt < self.max_retries:
                    logger.warning(
                        "nix-env failed (attempt %d/%d). Retrying in %ss...",
                        attempt,
                        self.max_retries,
                        self.retry_delay_sec,
                    )
                    time.sleep(self.retry_delay_sec)
                    continue
                raise RuntimeError(f"nix-env failed: {e.stderr}") from e
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
