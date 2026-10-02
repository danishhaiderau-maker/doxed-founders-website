"""Immutable, versioned analysis archive (laptop), plus long-horizon analysis.

After every completed analyzer generation, ``write_generation_snapshot`` saves
what the data showed - per-tile stats and Win %, rankings with corrected
verdicts, hypothesis/event-study results, family/regime trends, feature
summaries, stream health, the quarantine list - together with the source data
window, segment seq range and content hashes, so each analysis stays traceable
after the raw data it came from is pruned.

Daily and weekly rollups hold *sufficient statistics* (n, sum, sum of squares,
wins, min/max, per-bucket PnL) of closed trades, so multi-day statistics are
computed exactly from the archive without the raw rows.

Layout under ``DOXXED_ANALYSIS_ARCHIVE_DIR`` (default
``C:\\DoxxedCrypto\\analysis-archive``)::

    generations/<YYYY-MM-DD>/<stamp>-<rev12>-<gen12>/snapshot.json, reports/*.json.gz, receipt.json
    rollups/daily/<YYYY-MM-DD>.json      final (settled) days; written once, never replaced
    rollups/open/<YYYY-MM-DD>.json       unsettled days; replaced each generation
    rollups/weekly/<YYYY>-W<ww>.json     ISO weeks whose seven days are final
    index.jsonl                          append-only snapshot index
    legacy/                              schema-incompatible files moved aside

Snapshots and final rollups are create-new and never auto-deleted. Readers
never mix schema versions: a file whose ``schema_version`` is not supported
(and has no registered converter) is reported INCOMPATIBLE, excluded and moved
to ``legacy/``. Standalone: stdlib only (pandas DataFrames are accepted).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

DEFAULT_ROOT = r"C:\DoxxedCrypto\analysis-archive"
SNAPSHOT_SCHEMA = "analysis_archive_snapshot"
ROLLUP_SCHEMA = "analysis_archive_rollup"
LONG_HORIZON_SCHEMA = "long_horizon_report_v1"
SNAPSHOT_SCHEMA_VERSION = 1
ROLLUP_SCHEMA_VERSION = 1
# A day is final once this long past its UTC end (late closes have landed).
SETTLE_HOURS = 6
REPORT_COPY_MAX_BYTES = 2 * 1024 * 1024
LONG_HORIZON_REPORT_FILE = "long_horizon_report.json"

# Reports copied (gzip) into each snapshot when present and small enough.
SNAPSHOT_REPORTS = (
    "main_rankings_report.json", "strategy_lab_report.json", "stream_studies_report.json",
    "regime_leaderboard.json", "feature_importance_report.json", "tile_evidence_points_report.json",
    "tile_paired_comparison_report.json", "data_health_report.json", "preregistered_event_studies_report.json",
    "episode_execution_quarantine.json", "analyzer_integrity_report.json", "real_edge_summary.json",
    LONG_HORIZON_REPORT_FILE,
)

# schema name -> {supported versions}; converters upgrade an old version in place.
SUPPORTED_VERSIONS = {SNAPSHOT_SCHEMA: {1}, ROLLUP_SCHEMA: {1}}
CONVERTERS: dict[tuple[str, int], Callable[[dict], dict]] = {}

COMPATIBLE = "COMPATIBLE"
CONVERTED = "CONVERTED"
INCOMPATIBLE = "INCOMPATIBLE"


def archive_root(root: Optional[str] = None) -> Path:
    return Path(root or os.environ.get("DOXXED_ANALYSIS_ARCHIVE_DIR") or DEFAULT_ROOT)


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def _iso(ts: float) -> str:
    return _utc(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if not math.isfinite(v):
            return None
        return v / 1000.0 if v > 1e11 else v
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        try:
            return _parse_ts(float(value))
        except (TypeError, ValueError):
            return None


def _num(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str,
                      allow_nan=False).encode("utf-8")


def _clean(obj):
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            return _clean(obj.item())
        except (TypeError, ValueError):
            return str(obj)
    return obj


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_create_new(path: Path, raw: bytes, *, read_only: bool = True) -> None:
    """Write once; an existing file with other bytes is a hard error."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise FileExistsError(f"immutable archive file differs: {path}")
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise
    finally:
        os.unlink(temporary)
    if read_only:
        os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def _write_replace(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, path)


# ------------------------------------------------------------ sufficient statistics
def empty_stats() -> dict:
    return {"n": 0, "wins": 0, "losses": 0, "sum": 0.0, "sumsq": 0.0, "min": None, "max": None}


def add_value(stats: dict, value: float) -> dict:
    stats["n"] += 1
    stats["sum"] += value
    stats["sumsq"] += value * value
    stats["wins"] += 1 if value > 0 else 0
    stats["losses"] += 1 if value < 0 else 0
    stats["min"] = value if stats["min"] is None else min(stats["min"], value)
    stats["max"] = value if stats["max"] is None else max(stats["max"], value)
    return stats


def merge_stats(a: dict, b: dict) -> dict:
    out = empty_stats()
    for key in ("n", "wins", "losses"):
        out[key] = int(a.get(key) or 0) + int(b.get(key) or 0)
    out["sum"] = float(a.get("sum") or 0.0) + float(b.get("sum") or 0.0)
    out["sumsq"] = float(a.get("sumsq") or 0.0) + float(b.get("sumsq") or 0.0)
    mins = [v for v in (a.get("min"), b.get("min")) if v is not None]
    maxs = [v for v in (a.get("max"), b.get("max")) if v is not None]
    out["min"] = min(mins) if mins else None
    out["max"] = max(maxs) if maxs else None
    return out


def describe(stats: dict) -> dict:
    """Mean, sample std, Win %, t statistic and a normal-approximation 95% CI."""
    n = int(stats.get("n") or 0)
    total = float(stats.get("sum") or 0.0)
    out = {"n": n, "net_pnl_usd": round(total, 6), "mean_usd": None, "std_usd": None,
           "win_rate": None, "t_stat": None, "ci95_lo_usd": None, "ci95_hi_usd": None}
    if n == 0:
        return out
    mean = total / n
    out["mean_usd"] = round(mean, 6)
    out["win_rate"] = round(int(stats.get("wins") or 0) / n, 6)
    if n > 1:
        var = max(0.0, (float(stats.get("sumsq") or 0.0) - n * mean * mean) / (n - 1))
        std = math.sqrt(var)
        out["std_usd"] = round(std, 6)
        se = std / math.sqrt(n)
        if se > 0:
            out["t_stat"] = round(mean / se, 4)
            out["ci95_lo_usd"] = round(mean - 1.96 * se, 6)
            out["ci95_hi_usd"] = round(mean + 1.96 * se, 6)
    return out


def _records(trades) -> list[dict]:
    if trades is None:
        return []
    if hasattr(trades, "to_dict"):
        try:
            return list(trades.to_dict("records"))
        except TypeError:
            return []
    return [dict(row) for row in trades]


def _trade_rows(trades) -> list[dict]:
    """Closed trades with a finite PnL and close time, de-duplicated by trade_id."""
    seen: dict[str, dict] = {}
    anonymous: list[dict] = []
    for row in _records(trades):
        pnl = _num(row.get("net_pnl_usd"))
        ts = _parse_ts(row.get("close_ts")) or _parse_ts(row.get("ts"))
        if pnl is None or ts is None:
            continue
        lane = str(row.get("research_lane") or "").strip().upper() or "UNKNOWN"
        regime = str(row.get("regime") or row.get("context_regime") or "").strip() or "UNKNOWN"
        clean = {"trade_id": str(row.get("trade_id") or ""), "pnl": pnl, "ts": ts, "lane": lane,
                 "family": str(row.get("cfg_family") or lane).strip() or lane, "regime": regime,
                 "exit_reason": str(row.get("exit_reason") or "").strip() or "UNKNOWN",
                 "epoch": str(row.get("epoch_id") or row.get("policy_epoch_id") or "").strip() or "UNKNOWN",
                 "policy_signature": str(row.get("policy_signature") or "").strip()}
        if clean["trade_id"]:
            seen[clean["trade_id"]] = clean
        else:
            anonymous.append(clean)
    return list(seen.values()) + anonymous


def day_stats(trades) -> dict:
    """{day: {epoch: {"by_tile", "by_tile_regime", "by_tile_exit", "by_tile_hour", "by_family"}}}"""
    out: dict = {}
    for row in _trade_rows(trades):
        when = _utc(row["ts"])
        cell = out.setdefault(when.strftime("%Y-%m-%d"), {}).setdefault(row["epoch"], {
            "by_tile": {}, "by_tile_regime": {}, "by_tile_exit": {}, "by_tile_hour": {}, "by_family": {},
            "policy_signatures": {}})
        lane = row["lane"]
        add_value(cell["by_tile"].setdefault(lane, empty_stats()), row["pnl"])
        add_value(cell["by_tile_regime"].setdefault(f"{lane}|{row['regime']}", empty_stats()), row["pnl"])
        add_value(cell["by_tile_exit"].setdefault(f"{lane}|{row['exit_reason']}", empty_stats()), row["pnl"])
        add_value(cell["by_tile_hour"].setdefault(f"{lane}|{when.hour:02d}", empty_stats()), row["pnl"])
        add_value(cell["by_family"].setdefault(row["family"], empty_stats()), row["pnl"])
        if row["policy_signature"]:
            cell["policy_signatures"].setdefault(lane, set()).add(row["policy_signature"])
    for epochs in out.values():
        for cell in epochs.values():
            cell["policy_signatures"] = {k: sorted(v) for k, v in cell["policy_signatures"].items()}
    return out


def _merge_groups(a: dict, b: dict) -> dict:
    out = {k: dict(v) for k, v in a.items()}
    for key, stats in b.items():
        out[key] = merge_stats(out.get(key) or empty_stats(), stats)
    return out


# --------------------------------------------------------------------- compat
def compat_status(payload: dict) -> tuple[str, dict]:
    """Return (status, payload-or-converted) for one archive document."""
    schema = str(payload.get("schema") or "")
    version = payload.get("schema_version")
    supported = SUPPORTED_VERSIONS.get(schema)
    if supported is None or not isinstance(version, int):
        return INCOMPATIBLE, payload
    if version in supported:
        return COMPATIBLE, payload
    converter = CONVERTERS.get((schema, version))
    if converter is not None:
        return CONVERTED, converter(dict(payload))
    return INCOMPATIBLE, payload


def _quarantine_incompatible(root: Path, path: Path, reason: str) -> dict:
    """Move an incompatible archive file to legacy/ (deletable first under the cap)."""
    relative = path.relative_to(root)
    target = root / "legacy" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        shutil.move(str(path), str(target))
    except OSError as exc:
        return {"path": str(relative), "reason": reason, "moved": False, "error": str(exc)}
    receipt = {"path": str(relative), "reason": reason, "moved_to": str(target.relative_to(root)),
               "moved_at": _iso(datetime.now(timezone.utc).timestamp())}
    with (root / "legacy" / "moves.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, sort_keys=True) + "\n")
    return receipt


# ------------------------------------------------------------------- snapshot
def _source_info(data_dir: Path, previous: Optional[dict], heartbeat: Optional[dict]) -> dict:
    heartbeat = heartbeat if isinstance(heartbeat, dict) else {}
    state_path = data_dir / ".fly-sync-state.json"
    state = _read_json(state_path)
    seq = heartbeat.get("segmentAppliedSeq")
    prev_seq = ((previous or {}).get("source") or {}).get("segment_seq_through")
    hashes = {}
    try:
        import data_retention_policy as policy
    except ImportError:  # pragma: no cover - same directory on every host
        policy = None
    for relpath, record in sorted(state.items()):
        if not isinstance(record, dict) or not record.get("sha256"):
            continue
        rel = str(relpath).replace("\\", "/")
        if policy is None or policy.classify(rel) in (policy.TIER_A, policy.TIER_B):
            hashes[rel] = {"sha256": record.get("sha256"), "size": record.get("size")}
    return {
        "data_root": str(data_dir),
        "segment_prefix": heartbeat.get("segmentPrefix"),
        "segment_seq_from": (int(prev_seq) + 1) if isinstance(prev_seq, int) else None,
        "segment_seq_through": int(seq) if isinstance(seq, int) else None,
        "segment_manifest_sha256": heartbeat.get("segmentHeadManifestSha256"),
        "collection_epoch_id": heartbeat.get("collectionEpochId"),
        "deployed_revision": heartbeat.get("deployedRevision"),
        "synced_at": heartbeat.get("syncedAt"),
        "sync_state_sha256": _sha256_file(state_path) if state_path.is_file() else None,
        "input_file_hashes": hashes,
        "input_file_count": len(state),
    }


def _report_subset(report_dir: Path) -> dict:
    """Small, durable extracts of the reports that say what the data showed."""
    rankings = _read_json(report_dir / "main_rankings_report.json")
    lab = _read_json(report_dir / "strategy_lab_report.json")
    studies = _read_json(report_dir / "stream_studies_report.json")
    regimes = _read_json(report_dir / "regime_leaderboard.json")
    features = _read_json(report_dir / "feature_importance_report.json")
    quarantine = _read_json(report_dir / "trade_cohort_quarantine.json")
    episodes = _read_json(report_dir / "episode_execution_quarantine.json")
    hypotheses = lab.get("hypotheses") if isinstance(lab.get("hypotheses"), list) else []
    families = lab.get("families") if isinstance(lab.get("families"), list) else []
    rows = quarantine.get("rows_detail") or []
    return {
        "rankings": {"method": rankings.get("method"), "family_summaries": rankings.get("family_summaries"),
                     "tile_verdicts": rankings.get("tile_verdicts"), "status": rankings.get("status")},
        "hypotheses": [{k: h.get(k) for k in ("id", "hypothesis_id", "family", "label", "n", "n_closed",
                                                "mean_bp", "net_bp", "p_value", "p_holm", "verdict",
                                                "corrected_verdict", "status") if k in h}
                       for h in hypotheses if isinstance(h, dict)][:200],
        "strategy_families": [{k: f.get(k) for k in ("family", "label", "tested", "best", "verdict",
                                                     "null_basis", "n") if k in f}
                              for f in families if isinstance(f, dict)][:100],
        "event_studies": {k: studies.get(k) for k in ("status", "schema", "stream_health", "exit_regret",
                                                      "taker_counterfactual", "fill_markouts",
                                                      "research_events") if k in studies},
        "regime_trends": {k: regimes.get(k) for k in ("leaderboard", "regimes", "rows", "summary") if k in regimes},
        "feature_summary": {k: features.get(k) for k in ("features", "top_features", "summary", "method")
                            if k in features},
        "quarantine": {"trade_ids": sorted({str(r.get("trade_id")) for r in rows if isinstance(r, dict)
                                            and r.get("trade_id")}),
                       "reasons": sorted({str(r.get("reason")) for r in rows if isinstance(r, dict)
                                          and r.get("reason")}),
                       "episodes": episodes.get("quarantined") or episodes.get("rows") or []},
    }


def _tile_stats(trades, epoch: Optional[str]) -> dict:
    groups: dict[str, dict] = {}
    families: dict[str, dict] = {}
    regimes: dict[str, dict] = {}
    window = [None, None]
    for row in _trade_rows(trades):
        if epoch and row["epoch"] not in (epoch, "UNKNOWN"):
            continue
        add_value(groups.setdefault(row["lane"], empty_stats()), row["pnl"])
        add_value(families.setdefault(row["family"], empty_stats()), row["pnl"])
        add_value(regimes.setdefault(f"{row['lane']}|{row['regime']}", empty_stats()), row["pnl"])
        window[0] = row["ts"] if window[0] is None else min(window[0], row["ts"])
        window[1] = row["ts"] if window[1] is None else max(window[1], row["ts"])
    return {
        "tiles": {lane: {**describe(s), "stats": s} for lane, s in sorted(groups.items())},
        "families": {fam: {**describe(s), "stats": s} for fam, s in sorted(families.items())},
        "tile_regimes": {key: {**describe(s), "stats": s} for key, s in sorted(regimes.items())},
        "close_window": {"first": _iso(window[0]) if window[0] else None,
                         "last": _iso(window[1]) if window[1] else None},
    }


def latest_snapshot(root: Optional[str] = None) -> Optional[dict]:
    index = archive_root(root) / "index.jsonl"
    if not index.is_file():
        return None
    last = None
    with index.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("snapshot_id"):
                last = row
    return last


def verify_snapshot(entry: dict, root: Optional[str] = None) -> bool:
    """True when the snapshot's receipt hashes still match every file."""
    base = archive_root(root)
    path = base / str(entry.get("path") or "")
    receipt = _read_json(path / "receipt.json")
    files = receipt.get("files") or {}
    if not files or _sha256_file(path / "snapshot.json") != files.get("snapshot.json"):
        return False
    for name, digest in files.items():
        target = path / name
        if not target.is_file() or _sha256_file(target) != digest:
            return False
    return _sha256_file(path / "receipt.json") == entry.get("receipt_sha256")


def write_generation_snapshot(*, report_dir: str, data_dir: str, trades=None,
                              export_summary: Optional[dict] = None, now: Optional[float] = None,
                              root: Optional[str] = None, source_heartbeat: Optional[dict] = None) -> dict:
    """Write one immutable generation snapshot, refresh rollups, append the index.

    ``source_heartbeat`` is the promotion heartbeat captured when the generation
    started; without it the consumed seq is unknown and retention stays closed.
    """
    now = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    base = archive_root(root)
    report_path, data_path = Path(report_dir), Path(data_dir)
    manifest = _read_json(report_path / "report_manifest.json")
    previous = None
    last = latest_snapshot(root)
    if last:
        previous = _read_json(base / last["path"] / "snapshot.json")
    epoch = str(manifest.get("dataset_epoch") or "") or None
    rev = str(manifest.get("analyzer_revision") or manifest.get("source_revision") or "unknown")
    gen = str(manifest.get("generation_id") or manifest.get("generated_at") or "nogen")
    gen_token = hashlib.sha256(gen.encode("utf-8")).hexdigest()[:12] if not gen.isalnum() else gen[:12]
    stamp = _utc(now).strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"{stamp}-{rev[:12]}-{gen_token}"
    relative = Path("generations") / _utc(now).strftime("%Y-%m-%d") / snapshot_id
    target = base / relative
    try:
        import data_retention_policy as policy
        dataset_versions = {spec[0]: spec[1] for spec in policy.TIER_A_DATASETS.values()}
    except ImportError:  # pragma: no cover
        dataset_versions = {}
    snapshot = _clean({
        "schema": SNAPSHOT_SCHEMA, "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id, "written_at": _iso(now),
        "generation": {k: manifest.get(k) for k in (
            "generation_id", "generation_started_at", "generated_at", "analyzer_revision", "source_revision",
            "deployed_revision", "dataset_epoch", "dataset_checksum", "manifest_entry_hash",
            "tile_registry_signature", "active_tiles")},
        "export_id": (export_summary or {}).get("export_id"),
        "dataset_schema_versions": dataset_versions,
        "source": _source_info(data_path, previous, source_heartbeat),
        "current_epoch": _tile_stats(trades, epoch),
        "analysis": _report_subset(report_path),
        "export_tile_stats": ((export_summary or {}).get("tables") or {}).get("tile_stats"),
        "stream_health": ((export_summary or {}).get("strategy_lab") or {}).get("tape"),
    })
    files = {}
    raw = _canonical(snapshot)
    _write_create_new(target / "snapshot.json", raw)
    files["snapshot.json"] = _sha256_bytes(raw)
    for name in SNAPSHOT_REPORTS:
        source = report_path / name
        try:
            if not source.is_file() or source.stat().st_size > REPORT_COPY_MAX_BYTES:
                continue
            payload = gzip.compress(source.read_bytes(), compresslevel=9, mtime=0)
        except OSError:
            continue
        _write_create_new(target / "reports" / f"{name}.gz", payload)
        files[f"reports/{name}.gz"] = _sha256_bytes(payload)
    receipt = _canonical({"schema": "analysis_archive_receipt_v1", "snapshot_id": snapshot_id,
                          "files": files, "written_at": _iso(now)})
    _write_create_new(target / "receipt.json", receipt)
    rollups = update_rollups(trades, now=now, root=root)
    entry = {"snapshot_id": snapshot_id, "path": relative.as_posix(), "written_at": _iso(now),
             "schema_version": SNAPSHOT_SCHEMA_VERSION, "dataset_epoch": epoch,
             "generation_id": manifest.get("generation_id"),
             "analyzer_revision": manifest.get("analyzer_revision"),
             "segment_seq_through": snapshot["source"].get("segment_seq_through"),
             "close_window": snapshot["current_epoch"].get("close_window"),
             "snapshot_sha256": files["snapshot.json"], "receipt_sha256": _sha256_bytes(receipt)}
    base.mkdir(parents=True, exist_ok=True)
    with (base / "index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return {**entry, "rollups": rollups, "verified": verify_snapshot(entry, root)}


# -------------------------------------------------------------------- rollups
def _iso_week(day: str) -> str:
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


def update_rollups(trades, *, now: float, root: Optional[str] = None) -> dict:
    """Write final daily rollups for settled days, open ones for the rest, and weeks."""
    base = archive_root(root)
    stats = day_stats(trades)
    written = {"final": [], "open": [], "weekly": [], "kept_existing_final": []}
    open_dir = base / "rollups" / "open"
    pending = set(stats) | ({p.stem for p in open_dir.glob("*.json")} if open_dir.is_dir() else set())
    for day in sorted(pending):
        epochs = dict(stats.get(day) or {})
        # Cells of epochs no longer in the analyzed trades (epoch reset mid-day)
        # are carried over from the open rollup instead of being dropped.
        previous_open = _read_json(open_dir / f"{day}.json")
        if compat_status(previous_open)[0] != INCOMPATIBLE:
            for epoch, cell in (previous_open.get("epochs") or {}).items():
                epochs.setdefault(epoch, cell)
        try:
            day_end = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            continue
        if not epochs:
            continue
        settled = now >= (day_end + timedelta(hours=SETTLE_HOURS)).timestamp()
        payload = _clean({"schema": ROLLUP_SCHEMA, "schema_version": ROLLUP_SCHEMA_VERSION,
                          "period": "day", "day": day, "final": settled, "written_at": _iso(now),
                          "epochs": epochs})
        if settled:
            final_path = base / "rollups" / "daily" / f"{day}.json"
            if final_path.exists():
                written["kept_existing_final"].append(day)
            else:
                _write_create_new(final_path, _canonical(payload))
                written["final"].append(day)
            (base / "rollups" / "open" / f"{day}.json").unlink(missing_ok=True)
        else:
            _write_replace(base / "rollups" / "open" / f"{day}.json", _canonical(payload))
            written["open"].append(day)
    weeks: dict[str, list[str]] = {}
    for path in sorted((base / "rollups" / "daily").glob("*.json")):
        weeks.setdefault(_iso_week(path.stem), []).append(path.stem)
    for week, days in sorted(weeks.items()):
        target = base / "rollups" / "weekly" / f"{week}.json"
        year, num = week.split("-W")
        week_end = datetime.fromisocalendar(int(year), int(num), 7).replace(tzinfo=timezone.utc)
        complete = now >= (week_end + timedelta(days=1, hours=SETTLE_HOURS)).timestamp()
        if target.exists() or not complete:
            continue
        merged: dict = {}
        for day in days:
            doc = _read_json(base / "rollups" / "daily" / f"{day}.json")
            if compat_status(doc)[0] == INCOMPATIBLE:
                continue
            for epoch, cell in (doc.get("epochs") or {}).items():
                out = merged.setdefault(epoch, {})
                for group, values in cell.items():
                    if group == "policy_signatures":
                        continue
                    out[group] = _merge_groups(out.get(group) or {}, values or {})
        _write_create_new(target, _canonical(_clean({
            "schema": ROLLUP_SCHEMA, "schema_version": ROLLUP_SCHEMA_VERSION, "period": "week",
            "week": week, "days": days, "final": True, "written_at": _iso(now), "epochs": merged})))
        written["weekly"].append(week)
    return written


# -------------------------------------------------------------------- loading
def load_archive(since: Optional[str] = None, *, root: Optional[str] = None,
                 move_incompatible: bool = True) -> dict:
    """Load snapshots index, daily/weekly rollups and per-file compat status.

    ``since`` is an ISO date or timestamp; older snapshots/days are skipped.
    Incompatible documents are excluded (and moved to legacy/ by default).
    """
    base = archive_root(root)
    cutoff = _parse_ts(since) if since else None
    out = {"root": str(base), "snapshots": [], "daily": {}, "open_days": {}, "weekly": {},
           "compat": [], "legacy_moves": []}
    if not base.is_dir():
        return out
    index = base / "index.jsonl"
    if index.is_file():
        with index.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if cutoff is not None and (_parse_ts(row.get("written_at")) or 0) < cutoff:
                    continue
                if row.get("schema_version") not in SUPPORTED_VERSIONS[SNAPSHOT_SCHEMA]:
                    out["compat"].append({"dataset": "snapshot", "id": row.get("snapshot_id"),
                                          "schema_version": row.get("schema_version"),
                                          "status": INCOMPATIBLE})
                    continue
                out["snapshots"].append(row)
    for kind, key in (("daily", "daily"), ("open", "open_days"), ("weekly", "weekly")):
        for path in sorted((base / "rollups" / kind).glob("*.json")):
            if cutoff is not None and kind != "weekly":
                try:
                    if datetime.fromisoformat(path.stem).replace(tzinfo=timezone.utc).timestamp() + 86400 < cutoff:
                        continue
                except ValueError:
                    continue
            doc = _read_json(path)
            status, doc = compat_status(doc)
            out["compat"].append({"dataset": f"rollup_{kind}", "id": path.stem,
                                  "schema_version": doc.get("schema_version"), "status": status})
            if status == INCOMPATIBLE:
                if move_incompatible and kind != "open":
                    out["legacy_moves"].append(_quarantine_incompatible(base, path, "UNSUPPORTED_SCHEMA_VERSION"))
                continue
            out[key][path.stem] = doc
    return out


def compat_summary(archive: dict) -> dict:
    counts: dict[str, dict] = {}
    for row in archive.get("compat") or []:
        cell = counts.setdefault(row["dataset"], {COMPATIBLE: 0, CONVERTED: 0, INCOMPATIBLE: 0})
        cell[row["status"]] = cell.get(row["status"], 0) + 1
    return counts


# ------------------------------------------------------------ long-horizon analysis
def _trend(points: list[float]) -> Optional[dict]:
    """Least-squares slope over equally spaced points (per step)."""
    clean = [p for p in points if p is not None]
    n = len(clean)
    if n < 3:
        return None
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(clean) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, clean)) / sxx if sxx else 0.0
    return {"points": n, "slope_per_step": round(slope, 6),
            "direction": "RISING" if slope > 0 else ("FALLING" if slope < 0 else "FLAT")}


def _suggestion(desc: dict, min_n: int = 30) -> str:
    n = desc.get("n") or 0
    if n < min_n:
        return f"KEEP_COLLECTING: n={n} < {min_n} closed trades across the archive"
    lo, hi = desc.get("ci95_lo_usd"), desc.get("ci95_hi_usd")
    if lo is not None and lo > 0:
        return "EVIDENCE_POSITIVE: 95% CI above zero across days - candidate for pre-registered confirmation"
    if hi is not None and hi < 0:
        return "EVIDENCE_NEGATIVE: 95% CI below zero across days - candidate to revise or retire"
    return "INCONCLUSIVE: CI spans zero across days - keep collecting or tighten the hypothesis"


def long_horizon_report(trades, *, now: Optional[float] = None, root: Optional[str] = None,
                        lanes: Optional[Iterable[str]] = None, horizon_days: int = 90) -> dict:
    """Combine current raw closed trades with archived daily sufficient statistics.

    A (day, epoch) cell present in the current raw trades is taken from the raw
    data; every other final, schema-compatible cell comes from the archive, so
    a trade is never counted twice and pruned windows still contribute.
    """
    now = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    since = _iso(now - horizon_days * 86400)
    archive = load_archive(since, root=root)
    current = day_stats(trades)
    days: dict[str, dict] = {}
    sources: dict[str, str] = {}
    for day, doc in archive["daily"].items():
        days[day] = dict(doc.get("epochs") or {})
        sources[day] = "archive"
    for day, epochs in current.items():
        # Per (day, epoch): raw replaces the archived cell for the same epoch only,
        # so an older epoch's archived trades on the same day still count.
        days.setdefault(day, {}).update(epochs)
        sources[day] = "raw"
    by_tile: dict[str, dict] = {}
    by_family: dict[str, dict] = {}
    by_tile_regime: dict[str, dict] = {}
    series: dict[str, dict] = {}
    for day in sorted(days):
        for epoch, cell in days[day].items():
            by_tile = _merge_groups(by_tile, cell.get("by_tile") or {})
            by_family = _merge_groups(by_family, cell.get("by_family") or {})
            by_tile_regime = _merge_groups(by_tile_regime, cell.get("by_tile_regime") or {})
            for lane, stats in (cell.get("by_tile") or {}).items():
                day_cell = series.setdefault(lane, {})
                day_cell[day] = merge_stats(day_cell.get(day) or empty_stats(), stats)
    wanted = [str(l).upper() for l in lanes] if lanes else sorted(by_tile)
    tiles = {}
    for lane in wanted:
        stats = by_tile.get(lane) or empty_stats()
        desc = describe(stats)
        daily = series.get(lane) or {}
        means = [describe(daily[d])["mean_usd"] for d in sorted(daily)]
        tiles[lane] = {**desc, "stats": stats, "days": len(daily),
                       "daily_mean_trend": _trend(means), "suggestion": _suggestion(desc),
                       "daily": {d: describe(daily[d]) for d in sorted(daily)[-30:]}}
    family_trends = {}
    for entry in archive["snapshots"][-200:]:
        snap = _read_json(archive_root(root) / entry["path"] / "snapshot.json")
        for fam, desc in ((snap.get("current_epoch") or {}).get("families") or {}).items():
            family_trends.setdefault(fam, []).append(desc.get("mean_usd"))
    report = {
        "schema": LONG_HORIZON_SCHEMA, "schema_version": 1, "generated_at": _iso(now),
        "horizon_days": horizon_days,
        "method": ("Exact pooled statistics: per-day sufficient statistics (n, sum, sum of squares, wins) "
                   "from raw closed trades for days still on disk and from final archive rollups for older "
                   "days; no day is double counted; normal-approximation 95% CI; trends are least-squares "
                   "slopes of daily means (tiles) or per-generation means (families)."),
        "coverage": {"days_total": len(days), "days_from_raw": sum(1 for v in sources.values() if v == "raw"),
                     "days_from_archive": sum(1 for v in sources.values() if v == "archive"),
                     "first_day": min(days) if days else None, "last_day": max(days) if days else None,
                     "snapshots": len(archive["snapshots"])},
        "tiles": tiles,
        "families": {fam: {**describe(s), "stats": s} for fam, s in sorted(by_family.items())},
        "family_trends": {fam: _trend(points) for fam, points in sorted(family_trends.items())},
        "tile_regimes": {key: describe(s) for key, s in sorted(by_tile_regime.items())},
        "schema_compat": {"rows": archive["compat"][-500:], "summary": compat_summary(archive),
                          "legacy_moves": archive["legacy_moves"]},
    }
    return _clean(report)


def write_long_horizon_report(trades, report_dir: str, *, lanes=None, now: Optional[float] = None,
                              root: Optional[str] = None) -> dict:
    report = long_horizon_report(trades, now=now, root=root, lanes=lanes)
    path = Path(report_dir) / LONG_HORIZON_REPORT_FILE
    _write_replace(path, json.dumps(report, indent=2, sort_keys=True).encode("utf-8"))
    return report


def archive_status(root: Optional[str] = None, now: Optional[float] = None) -> dict:
    """Freshness and size of the archive for health checks and dashboards."""
    now = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    base = archive_root(root)
    last = latest_snapshot(root)
    size = 0
    count = 0
    if base.is_dir():
        for directory, _dirs, files in os.walk(base):
            for name in files:
                try:
                    size += os.path.getsize(os.path.join(directory, name))
                    count += 1
                except OSError:
                    continue
    written = _parse_ts((last or {}).get("written_at"))
    finals = sorted(p.stem for p in (base / "rollups" / "daily").glob("*.json")) if base.is_dir() else []
    return {"root": str(base), "bytes": size, "files": count,
            "last_snapshot": last, "last_snapshot_age_sec": round(now - written, 1) if written else None,
            "last_final_day": finals[-1] if finals else None, "final_days": len(finals)}
