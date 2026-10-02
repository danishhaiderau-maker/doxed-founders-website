"""Load the latest laptop-analyzer export as pandas DataFrames.

Standalone (stdlib + pandas; pyarrow optional). Copied to the export root as
``analyzer_client.py`` after every analyzer run::

    import sys; sys.path.insert(0, r"C:\\DoxxedCrypto\\analyzer-exports")
    from analyzer_client import load_latest
    exp = load_latest()                    # raises StaleExportError if not current
    exp["hypotheses"]                      # DataFrame
    exp.summary["generation"]["dataset_epoch"]

Refusal rules (fail closed):
* ``summary.json`` older than ``max_age_min`` (default: the export's own
  freshness policy, 45 min);
* any table file whose sha256 differs from the summary (torn / mixed read);
* the analyzer's current ``report_manifest.json`` names a different
  generation than the export (the export missed a newer run);
* with ``check_live``: the :9001 dashboard reports a different completed
  generation or revision than the export (skipped if :9001 is unreachable,
  which is recorded in ``exp.checks``);
* ``require_revision`` given and the export's analyzer revision does not
  start with it.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import Iterable, Optional

import pandas as pd

DEFAULT_ROOT = os.environ.get("DOXXED_ANALYZER_EXPORT_DIR") or r"C:\DoxxedCrypto\analyzer-exports"
DEFAULT_DASHBOARD = os.environ.get("DOXXED_ANALYZER_DASHBOARD_URL") or "http://127.0.0.1:9001"
SCHEMA = "analyzer_export_v1"


class StaleExportError(RuntimeError):
    """The export is not provably the analyzer's current generation."""


class AnalyzerExport(dict):
    """``dict`` of table name -> DataFrame, plus ``summary`` and ``checks``."""

    def __init__(self, tables: dict, summary: dict, checks: dict, path: str):
        super().__init__(tables)
        self.summary = summary
        self.checks = checks
        self.path = path

    def __repr__(self) -> str:
        gen = (self.summary.get("generation") or {})
        return (f"AnalyzerExport({self.summary.get('export_id')}, epoch={gen.get('dataset_epoch')}, "
                f"tables={sorted(self)})")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ts(value) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _live_status(url: str, timeout: float) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/status", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _verify(path: str, *, max_age_min: Optional[float], check_live: bool, dashboard_url: str,
            require_revision: Optional[str], now: float, timeout: float) -> tuple:
    with open(os.path.join(path, "summary.json"), encoding="utf-8") as handle:
        summary = json.load(handle)
    checks = {}
    if summary.get("schema") != SCHEMA:
        raise StaleExportError(f"unexpected schema {summary.get('schema')!r}")
    limit = max_age_min if max_age_min is not None else (summary.get("freshness_policy") or {}).get("max_age_min", 45)
    age_min = (now - float(summary.get("generated_at_ts") or 0)) / 60.0
    checks["age_min"] = round(age_min, 2)
    if age_min > float(limit):
        raise StaleExportError(f"export is {age_min:.1f} min old (limit {limit} min)")
    for name, meta in (summary.get("tables") or {}).items():
        for fmt in ("parquet", "csv"):
            info = meta.get(fmt)
            if not info:
                continue
            fp = os.path.join(path, info["file"])
            if not os.path.isfile(fp) or _sha256(fp) != info["sha256"]:
                raise StaleExportError(f"table {name}.{fmt} hash mismatch (torn or mixed generation)")
    checks["hashes"] = "OK"
    gen = summary.get("generation") or {}
    manifest_path = gen.get("report_manifest_path")
    if manifest_path and os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError):
            manifest = None
        if manifest is not None:
            if manifest.get("generation_id") != gen.get("generation_id"):
                raise StaleExportError(
                    f"analyzer generation {manifest.get('generation_id')} != export {gen.get('generation_id')}")
            checks["manifest_parity"] = "MATCH"
    else:
        checks["manifest_parity"] = "MANIFEST_UNREADABLE"
    if check_live:
        status = _live_status(dashboard_url, timeout)
        if status is None:
            checks["live_parity"] = "DASHBOARD_UNREACHABLE"
        else:
            live_gen = _ts(status.get("generated_at"))
            exp_gen = _ts(gen.get("analyzer_completed_at"))
            if live_gen and exp_gen and abs(live_gen - exp_gen) > 1.0:
                raise StaleExportError(
                    f"dashboard generation {status.get('generated_at')} != export {gen.get('analyzer_completed_at')}")
            live_rev = str(status.get("generation_revision") or "")
            if live_rev and gen.get("analyzer_revision") and live_rev != gen.get("analyzer_revision"):
                raise StaleExportError(f"dashboard revision {live_rev[:12]} != export {gen['analyzer_revision'][:12]}")
            checks["live_parity"] = "MATCH"
    if require_revision:
        rev = str(gen.get("analyzer_revision") or "")
        if not rev.startswith(require_revision):
            raise StaleExportError(f"export revision {rev[:12]} does not match required {require_revision}")
    return summary, checks


def load_latest(root: Optional[str] = None, *, tables: Optional[Iterable[str]] = None,
                max_age_min: Optional[float] = None, check_live: bool = True,
                dashboard_url: str = DEFAULT_DASHBOARD, require_revision: Optional[str] = None,
                retries: int = 1, retry_wait_sec: float = 5.0, timeout: float = 5.0) -> AnalyzerExport:
    """Return the current export or raise ``StaleExportError``.

    One retry covers the few seconds between the analyzer publishing its
    manifest and finishing the export.
    """
    path = os.path.join(root or DEFAULT_ROOT, "latest")
    last_exc: Optional[Exception] = None
    for attempt in range(max(retries, 0) + 1):
        try:
            summary, checks = _verify(path, max_age_min=max_age_min, check_live=check_live,
                                      dashboard_url=dashboard_url, require_revision=require_revision,
                                      now=time.time(), timeout=timeout)
            wanted = list(tables) if tables else list(summary.get("tables") or {})
            out = {}
            for name in wanted:
                meta = (summary.get("tables") or {}).get(name)
                if meta is None:
                    raise KeyError(f"export has no table {name!r}")
                if meta.get("parquet"):
                    try:
                        out[name] = pd.read_parquet(os.path.join(path, meta["parquet"]["file"]))
                        continue
                    except Exception:
                        pass
                fp = os.path.join(path, meta["csv"]["file"])
                out[name] = pd.read_csv(fp) if meta.get("rows") else pd.DataFrame(columns=meta.get("columns") or [])
            again, _ = _verify(path, max_age_min=max_age_min, check_live=False, dashboard_url=dashboard_url,
                               require_revision=None, now=time.time(), timeout=timeout)
            if again.get("export_id") != summary.get("export_id"):
                raise StaleExportError("export replaced while reading")
            return AnalyzerExport(out, summary, checks, path)
        except (StaleExportError, OSError, ValueError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(retry_wait_sec)
    if isinstance(last_exc, StaleExportError):
        raise last_exc
    raise StaleExportError(f"export unreadable: {last_exc}")


if __name__ == "__main__":  # quick check: python analyzer_client.py
    exp = load_latest()
    print(repr(exp))
    print(json.dumps(exp.checks, indent=1))
    for k, v in exp.items():
        print(f"{k:20s} {v.shape}")
