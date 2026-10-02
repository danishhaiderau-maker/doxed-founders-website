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
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Iterable, Optional

import pandas as pd

DEFAULT_ROOT = os.environ.get("DOXXED_ANALYZER_EXPORT_DIR") or r"C:\DoxxedCrypto\analyzer-exports"
DEFAULT_DASHBOARD = os.environ.get("DOXXED_ANALYZER_DASHBOARD_URL") or "http://127.0.0.1:9001"
SCHEMA = "analyzer_export_v1"
# Concurrent pyarrow parquet reads in one process (threaded :9001 /api/insights)
# crash natively with 0xC0000005 in arrow.dll on Windows; table reads are serialized.
_TABLE_READ_LOCK = threading.Lock()


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
            with _TABLE_READ_LOCK:
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


DEFAULT_ARCHIVE = os.environ.get("DOXXED_ANALYSIS_ARCHIVE_DIR") or r"C:\DoxxedCrypto\analysis-archive"
DEFAULT_COMPACT = os.environ.get("DOXXED_BOT_DATA_COMPACT_DIR") or r"C:\DoxxedCrypto\bot-data-compact"
ARCHIVE_SUPPORTED = {"analysis_archive_snapshot": {1}, "analysis_archive_rollup": {1}}
_ROLLUP_DIMENSIONS = ("by_tile", "by_family", "by_tile_exit", "by_tile_regime")


class AnalysisArchive(dict):
    """DataFrames ``snapshots``, ``daily``, ``weekly`` and ``compat`` plus ``root``."""

    def __init__(self, tables: dict, root: str, since: Optional[str]):
        super().__init__(tables)
        self.root = root
        self.since = since

    def __repr__(self) -> str:
        return (f"AnalysisArchive(root={self.root!r}, since={self.since!r}, "
                f"snapshots={len(self['snapshots'])}, daily_rows={len(self['daily'])}, "
                f"incompatible={int((self['compat']['status'] == 'INCOMPATIBLE').sum()) if len(self['compat']) else 0})")


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _snapshot_intact(root: str, entry: dict) -> bool:
    folder = os.path.join(root, str(entry.get("path") or ""))
    receipt_path = os.path.join(folder, "receipt.json")
    receipt = _read_json(receipt_path) or {}
    files = receipt.get("files") or {}
    if not files or "snapshot.json" not in files:
        return False
    try:
        if any(_sha256(os.path.join(folder, name)) != digest for name, digest in files.items()):
            return False
        return _sha256(receipt_path) == entry.get("receipt_sha256")
    except OSError:
        return False


def _utc_ts(value) -> Optional[float]:
    """ISO date/timestamp; a naive value is UTC (archive days are UTC days)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"unparseable timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _rollup_rows(doc: dict, label: str, key: str) -> list:
    rows = []
    for epoch, cells in (doc.get("epochs") or {}).items():
        for dimension in _ROLLUP_DIMENSIONS:
            for name, stat in ((cells or {}).get(dimension) or {}).items():
                n = int(stat.get("n") or 0)
                rows.append({key: label, "final": bool(doc.get("final", key == "week")), "epoch": epoch,
                             "dimension": dimension[3:], "key": name, "n": n,
                             "wins": stat.get("wins"), "losses": stat.get("losses"),
                             "net_pnl_usd": stat.get("sum"),
                             "mean_usd": (stat.get("sum") or 0.0) / n if n else None,
                             "min_usd": stat.get("min"), "max_usd": stat.get("max")})
    return rows


def load_archive(since: Optional[str] = None, *, root: Optional[str] = None,
                 verify: bool = True) -> AnalysisArchive:
    """Long-horizon analysis archive (immutable snapshots + daily/weekly rollups).

    ``since`` is an ISO date/timestamp. Documents with an unsupported
    ``schema_version`` are excluded and listed in ``compat`` as INCOMPATIBLE;
    with ``verify`` a snapshot whose receipt hashes no longer match is listed
    as TAMPERED and excluded. Retention never deletes this archive.
    """
    base = root or DEFAULT_ARCHIVE
    cutoff = _utc_ts(since)
    snapshots, compat, daily, weekly = [], [], [], []
    index = os.path.join(base, "index.jsonl")
    if os.path.isfile(index):
        with open(index, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if cutoff is not None and (_utc_ts(row.get("written_at")) or 0) < cutoff:
                    continue
                version = row.get("schema_version")
                status = "COMPATIBLE" if version in ARCHIVE_SUPPORTED["analysis_archive_snapshot"] else "INCOMPATIBLE"
                if status == "COMPATIBLE" and verify and not _snapshot_intact(base, row):
                    status = "TAMPERED"
                compat.append({"dataset": "snapshot", "id": row.get("snapshot_id"),
                               "schema_version": version, "status": status})
                if status == "COMPATIBLE":
                    snapshots.append(row)
    for kind, sink, key in (("daily", daily, "day"), ("open", daily, "day"), ("weekly", weekly, "week")):
        folder = os.path.join(base, "rollups", kind)
        names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        for name in names:
            if not name.endswith(".json"):
                continue
            stem = name[:-5]
            if cutoff is not None and key == "day":
                try:
                    if datetime.fromisoformat(stem).replace(tzinfo=timezone.utc).timestamp() + 86400 < cutoff:
                        continue
                except ValueError:
                    continue
            doc = _read_json(os.path.join(folder, name)) or {}
            version = doc.get("schema_version")
            status = "COMPATIBLE" if version in ARCHIVE_SUPPORTED["analysis_archive_rollup"] else "INCOMPATIBLE"
            compat.append({"dataset": f"rollup_{kind}", "id": stem, "schema_version": version, "status": status})
            if status == "COMPATIBLE":
                sink.extend(_rollup_rows(doc, stem, key))
    columns = ["final", "epoch", "dimension", "key", "n", "wins", "losses", "net_pnl_usd", "mean_usd",
               "min_usd", "max_usd"]
    tables = {
        "snapshots": pd.DataFrame(snapshots),
        "daily": pd.DataFrame(daily, columns=["day"] + columns),
        "weekly": pd.DataFrame(weekly, columns=["week"] + columns),
        "compat": pd.DataFrame(compat, columns=["dataset", "id", "schema_version", "status"]),
    }
    return AnalysisArchive(tables, base, since)


def load_tier_a(dataset: str, *, since: Optional[str] = None, until: Optional[str] = None,
                schema_version: Optional[int] = None, root: Optional[str] = None,
                parse: bool = False) -> pd.DataFrame:
    """Compacted Tier A rows (zstd Parquet, one partition set per UTC day).

    Uses the newest ``v<N>`` directory unless ``schema_version`` is given;
    partitions whose manifest hash does not match are refused. ``parse=True``
    expands the raw JSON ``row`` column into columns.
    """
    folder = os.path.join(root or DEFAULT_COMPACT, "tierA", dataset)
    versions = sorted((int(name[1:]) for name in (os.listdir(folder) if os.path.isdir(folder) else [])
                       if name.startswith("v") and name[1:].isdigit()))
    if not versions:
        return pd.DataFrame(columns=["ts", "row"])
    version = schema_version if schema_version is not None else versions[-1]
    if version not in versions:
        raise KeyError(f"{dataset} has no schema_version {version}; available {versions}")
    lo = _utc_ts(since)
    hi = _utc_ts(until)
    frames = []
    vdir = os.path.join(folder, f"v{version}")
    for part_dir in sorted(os.listdir(vdir)):
        if not part_dir.startswith("date="):
            continue
        day = part_dir[5:]
        try:
            start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if (lo is not None and start + 86400 <= lo) or (hi is not None and start > hi):
            continue
        for name in sorted(os.listdir(os.path.join(vdir, part_dir))):
            if not name.endswith(".parquet"):
                continue
            path = os.path.join(vdir, part_dir, name)
            manifest = _read_json(path[:-len(".parquet")] + ".manifest.json") or {}
            if manifest.get("sha256") != _sha256(path):
                raise StaleExportError(f"Tier A partition hash mismatch: {path}")
            frames.append(pd.read_parquet(path))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["ts", "row"])
    if lo is not None:
        out = out[out["ts"] >= lo]
    if hi is not None:
        out = out[out["ts"] <= hi]
    out = out.sort_values("ts", kind="stable").reset_index(drop=True)
    if parse and len(out):
        expanded = pd.json_normalize([json.loads(text) if isinstance(text, str) and text.startswith("{") else {"raw": text}
                                      for text in out["row"]])
        out = pd.concat([out[["ts"]], expanded], axis=1)
    out.attrs.update({"dataset": dataset, "schema_version": version})
    return out


if __name__ == "__main__":  # quick check: python analyzer_client.py
    exp = load_latest()
    print(repr(exp))
    print(json.dumps(exp.checks, indent=1))
    for k, v in exp.items():
        print(f"{k:20s} {v.shape}")
