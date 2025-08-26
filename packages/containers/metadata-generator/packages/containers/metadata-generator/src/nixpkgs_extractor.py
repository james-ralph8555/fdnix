import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


class NixpkgsExtractor:
    def __init__(self) -> None:
        self.nixpkgs_path = "/tmp/nixpkgs"
        self.max_retries = 3
        self.retry_delay_seconds = 5

    def extract_all_packages(self) -> List[Dict[str, Any]]:
        logger.info("Cloning nixpkgs repository...")
        self._clone_nixpkgs()

        logger.info("Extracting package metadata using nix-env...")
        raw = self._extract_raw_package_data()

        logger.info("Processing and cleaning package data...")
        return self._process_package_data(raw)

    def _clone_nixpkgs(self) -> None:
        if os.path.exists(self.nixpkgs_path):
            shutil.rmtree(self.nixpkgs_path, ignore_errors=True)

        cmd = [
            "git",
            "clone",
            "--depth",
            "1",
            "https://github.com/NixOS/nixpkgs.git",
            self.nixpkgs_path,
        ]

        for attempt in range(1, self.max_retries + 1):
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
                logger.info("nixpkgs repository cloned successfully")
                return
            except subprocess.CalledProcessError as e:
                logger.warning("git clone failed (attempt %d): %s", attempt, e.stderr.decode(errors="ignore"))
            except subprocess.TimeoutExpired:
                logger.warning("git clone timed out (attempt %d)", attempt)

            if attempt < self.max_retries:
                time.sleep(self.retry_delay_seconds)
        raise RuntimeError("Failed to clone nixpkgs after retries")

    def _extract_raw_package_data(self) -> Dict[str, Any]:
        cmd = [
            "nix-env",
            "-f",
            self.nixpkgs_path,
            "-qaP",
            "--json",
            "--meta",
        ]

        try:
            proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"nix-env failed: {e.stderr.decode(errors='ignore')}" )
        except subprocess.TimeoutExpired:
            raise RuntimeError("nix-env timed out while extracting metadata")

        try:
            data = json.loads(proc.stdout.decode())
            logger.info("Successfully extracted %d packages", len(data))
            return data
        except Exception as e:
            raise RuntimeError(f"Failed to parse nix-env JSON output: {e}")

    def _process_package_data(self, raw_packages: Dict[str, Any]) -> List[Dict[str, Any]]:
        processed: List[Dict[str, Any]] = []
        current_ts = datetime.utcnow().isoformat()

        for attr_path, info in raw_packages.items():
            try:
                pname = info.get("pname")
                version = info.get("version") or "unknown"
                if not pname:
                    pname = self._extract_name_from_path(attr_path)
                if not pname or pname == "unknown":
                    logger.warning("Skipping package with unknown name: %s", attr_path)
                    continue

                meta = info.get("meta") or {}

                pkg = {
                    "packageName": pname,
                    "version": version,
                    "attributePath": attr_path,
                    "description": self._sanitize(meta.get("description") or info.get("description") or ""),
                    "longDescription": self._sanitize(meta.get("longDescription") or ""),
                    "homepage": self._sanitize(meta.get("homepage") or info.get("homepage") or ""),
                    "license": self._extract_license(meta.get("license") or info.get("license")),
                    "platforms": self._extract_platforms(meta.get("platforms") or info.get("platforms")),
                    "maintainers": self._extract_maintainers(meta.get("maintainers") or info.get("maintainers")),
                    "broken": bool(meta.get("broken") or info.get("broken") or False),
                    "unfree": bool(meta.get("unfree") or info.get("unfree") or False),
                    "available": meta.get("available") if meta.get("available") is not None else True,
                    "insecure": bool(meta.get("insecure") or False),
                    "unsupported": bool(meta.get("unsupported") or False),
                    "mainProgram": self._sanitize(meta.get("mainProgram") or ""),
                    "position": self._sanitize(meta.get("position") or ""),
                    "outputsToInstall": list(meta.get("outputsToInstall") or []),
                    "lastUpdated": current_ts,
                    "hasEmbedding": False,
                }

                processed.append(pkg)
                if len(processed) % 1000 == 0:
                    logger.info("Processed %d packages...", len(processed))

            except Exception as e:
                logger.warning("Error processing package %s: %s", attr_path, str(e))
                continue

        logger.info("Successfully processed %d packages", len(processed))
        return processed

    @staticmethod
    def _extract_name_from_path(attr_path: str) -> str:
        parts = attr_path.split(".")
        return parts[-1] if parts else attr_path

    @staticmethod
    def _sanitize(val: Any) -> str:
        if not isinstance(val, str):
            return ""
        # remove control chars and trim/limit length
        cleaned = "".join(ch for ch in val if 32 <= ord(ch) <= 126 or ch in ("\t", "\n", "\r"))
        return cleaned.strip()[:2000]

    def _extract_license(self, lic: Any) -> Optional[Dict[str, Any]]:
        if not lic:
            return None
        if isinstance(lic, str):
            return {"type": "string", "value": self._sanitize(lic)}
        if isinstance(lic, list):
            licenses = [self._single_license(l) for l in lic]
            licenses = [l for l in licenses if l]
            return {"type": "array", "licenses": licenses}
        if isinstance(lic, dict):
            base = self._single_license(lic) or {}
            base.update({"type": "object"})
            return base
        return {"type": "string", "value": str(lic)[:500]}

    def _single_license(self, lic: Any) -> Optional[Dict[str, Any]]:
        if not lic:
            return None
        if isinstance(lic, str):
            return {
                "shortName": lic,
                "fullName": "",
                "spdxId": "",
                "url": "",
                "free": None,
                "redistributable": None,
            }
        if isinstance(lic, dict):
            return {
                "shortName": self._sanitize(lic.get("shortName", "")),
                "fullName": self._sanitize(lic.get("fullName", "")),
                "spdxId": self._sanitize(lic.get("spdxId", "")),
                "url": self._sanitize(lic.get("url", "")),
                "free": lic.get("free") if isinstance(lic.get("free"), bool) else None,
                "redistributable": lic.get("redistributable") if isinstance(lic.get("redistributable"), bool) else None,
                "deprecated": lic.get("deprecated") if isinstance(lic.get("deprecated"), bool) else None,
            }
        return {
            "shortName": str(lic),
            "fullName": "",
            "spdxId": "",
            "url": "",
            "free": None,
            "redistributable": None,
        }

    @staticmethod
    def _extract_platforms(platforms: Any) -> List[str]:
        if isinstance(platforms, list):
            return [str(p) for p in platforms][:20]
        return []

    def _extract_maintainers(self, maintainers: Any) -> List[Dict[str, Any]]:
        if not isinstance(maintainers, list):
            return []
        out: List[Dict[str, Any]] = []
        for m in maintainers:
            if isinstance(m, dict):
                rec = {
                    "name": self._sanitize(m.get("name", "")),
                    "email": self._sanitize(m.get("email", "")),
                    "github": self._sanitize(m.get("github", "")),
                    "githubId": m.get("githubId") if isinstance(m.get("githubId"), int) else None,
                }
                if rec["name"] or rec["email"] or rec["github"]:
                    out.append(rec)
            else:
                out.append({"name": str(m), "email": "", "github": "", "githubId": None})
        return out[:10]

