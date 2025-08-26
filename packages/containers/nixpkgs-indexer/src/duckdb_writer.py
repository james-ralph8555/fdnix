import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb

try:
    import boto3  # type: ignore
except Exception:  # pragma: no cover - boto3 may be absent in local-only runs
    boto3 = None  # type: ignore


logger = logging.getLogger("fdnix.duckdb-writer")


class DuckDBWriter:
    def __init__(
        self,
        output_path: str,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        self.output_path = Path(output_path)
        self.s3_bucket = s3_bucket
        self.s3_key = s3_key
        self.region = region

    def write_artifact(self, packages: List[Dict[str, Any]]) -> None:
        self._ensure_parent_dir()
        logger.info("Creating DuckDB at %s", self.output_path)

        with duckdb.connect(str(self.output_path)) as conn:
            self._init_schema(conn)
            self._insert_packages(conn, packages)
            self._build_fts(conn)
            conn.execute("CHECKPOINT")

        logger.info("DuckDB artifact written: %s", self.output_path)

        if self.s3_bucket and self.s3_key:
            self._upload_to_s3()

    def _ensure_parent_dir(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_schema(self, conn: duckdb.DuckDBPyConnection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS packages (
              package_id TEXT PRIMARY KEY,
              packageName TEXT,
              version TEXT,
              attributePath TEXT,
              description TEXT,
              longDescription TEXT,
              homepage TEXT,
              license TEXT,
              platforms TEXT,
              maintainers TEXT,
              broken BOOLEAN,
              unfree BOOLEAN,
              available BOOLEAN,
              insecure BOOLEAN,
              unsupported BOOLEAN,
              mainProgram TEXT,
              position TEXT,
              outputsToInstall TEXT,
              lastUpdated TEXT,
              hasEmbedding BOOLEAN
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS packages_fts_source (
              package_id TEXT,
              text TEXT
            );
            """
        )

    def _insert_packages(
        self, conn: duckdb.DuckDBPyConnection, packages: List[Dict[str, Any]]
    ) -> None:
        if not packages:
            logger.warning("No packages to insert.")
            return

        rows: List[Tuple[Any, ...]] = []
        fts_rows: List[Tuple[str, str]] = []

        for p in packages:
            pkg_id = self._package_id(p)
            rows.append(
                (
                    pkg_id,
                    p.get("packageName") or "",
                    p.get("version") or "",
                    p.get("attributePath") or "",
                    p.get("description") or "",
                    p.get("longDescription") or "",
                    p.get("homepage") or "",
                    json.dumps(p.get("license")) if p.get("license") is not None else None,
                    json.dumps(p.get("platforms")) if p.get("platforms") is not None else None,
                    json.dumps(p.get("maintainers")) if p.get("maintainers") is not None else None,
                    bool(p.get("broken", False)),
                    bool(p.get("unfree", False)),
                    bool(p.get("available", True)),
                    bool(p.get("insecure", False)),
                    bool(p.get("unsupported", False)),
                    p.get("mainProgram") or "",
                    p.get("position") or "",
                    json.dumps(p.get("outputsToInstall"))
                    if p.get("outputsToInstall") is not None
                    else None,
                    p.get("lastUpdated") or "",
                    bool(p.get("hasEmbedding", False)),
                )
            )

            fts_rows.append((pkg_id, self._fts_text(p)))

        logger.info("Inserting %d rows into packages...", len(rows))
        conn.executemany(
            """
            INSERT OR REPLACE INTO packages (
              package_id, packageName, version, attributePath, description, longDescription,
              homepage, license, platforms, maintainers, broken, unfree, available,
              insecure, unsupported, mainProgram, position, outputsToInstall,
              lastUpdated, hasEmbedding
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            rows,
        )

        logger.info("Refreshing FTS source with %d rows...", len(fts_rows))
        conn.execute("DELETE FROM packages_fts_source;")
        conn.executemany(
            "INSERT INTO packages_fts_source (package_id, text) VALUES (?, ?);",
            fts_rows,
        )

    def _build_fts(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Create and persist FTS index using PRAGMA create_fts_index.

        Environment overrides (optional):
        - FTS_STOPWORDS: stopwords language (e.g. 'english')
        - FTS_STEMMER: stemmer language (e.g. 'english' or empty to disable)
        """
        stopwords = os.environ.get("FTS_STOPWORDS", "english").strip() or "english"
        stemmer = os.environ.get("FTS_STEMMER", "english").strip()

        try:
            conn.execute("INSTALL fts;")
            conn.execute("LOAD fts;")
        except Exception as e:
            error_msg = f"Could not install/load DuckDB fts extension: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        # Build PRAGMA with options; PRAGMA create_fts_index persists within DB
        try:
            if stemmer:
                pragma = (
                    "PRAGMA create_fts_index('packages_fts_source', 'text', "
                    f"stopwords='{stopwords}', stemmer='{stemmer}', strip_accents=true);"
                )
            else:
                pragma = (
                    "PRAGMA create_fts_index('packages_fts_source', 'text', "
                    f"stopwords='{stopwords}', strip_accents=true);"
                )
            conn.execute(pragma)
            logger.info("FTS index created via PRAGMA (stopwords=%s, stemmer=%s)", stopwords, stemmer or "<none>")
        except Exception as e:
            error_msg = f"Failed to create FTS index via PRAGMA: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _package_id(self, p: Dict[str, Any]) -> str:
        # Prefer attributePath, fallback to name@version
        attr = (p.get("attributePath") or "").strip()
        if attr:
            return attr
        name = (p.get("packageName") or "").strip()
        ver = (p.get("version") or "").strip()
        return f"{name}@{ver}" if name or ver else "unknown"

    def _fts_text(self, p: Dict[str, Any]) -> str:
        license_txt = ""
        try:
            lic = p.get("license")
            if isinstance(lic, dict):
                if lic.get("type") == "object":
                    license_txt = " ".join(
                        str(lic.get(k) or "") for k in ("shortName", "fullName", "spdxId")
                    )
                elif lic.get("type") == "array":
                    parts: List[str] = []
                    for l in lic.get("licenses") or []:
                        parts.extend([str(l.get("shortName") or ""), str(l.get("fullName") or ""), str(l.get("spdxId") or "")])
                    license_txt = " ".join(parts)
                elif lic.get("type") == "string":
                    license_txt = str(lic.get("value") or "")
        except Exception:
            license_txt = ""

        maintainers_txt = ""
        try:
            mnts = p.get("maintainers") or []
            if isinstance(mnts, list):
                maintainers_txt = " ".join(
                    str(m.get("name") or m.get("github") or "") if isinstance(m, dict) else str(m)
                    for m in mnts
                )
        except Exception:
            maintainers_txt = ""

        fields = [
            p.get("packageName") or "",
            p.get("version") or "",
            p.get("attributePath") or "",
            p.get("mainProgram") or "",
            p.get("description") or "",
            p.get("longDescription") or "",
            p.get("homepage") or "",
            maintainers_txt,
            license_txt,
            " ".join(str(platform) for platform in (p.get("platforms") or [])),
        ]
        return " \n".join(str(x) for x in fields if x)

    def _upload_to_s3(self) -> None:
        if not (boto3 and self.region and self.s3_bucket and self.s3_key):
            logger.info("S3 upload not configured; skipping.")
            return
        logger.info(
            "Uploading artifact to s3://%s/%s (region=%s)",
            self.s3_bucket,
            self.s3_key,
            self.region,
        )
        s3 = boto3.client("s3", region_name=self.region)
        s3.upload_file(str(self.output_path), self.s3_bucket, self.s3_key)
        logger.info("Upload complete.")
