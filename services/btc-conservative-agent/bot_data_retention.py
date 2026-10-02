"""Laptop bot-data retention: custody-gated pruning under a hard 50 GB cap.

Runs at the end of every successful segment analyzer cycle (inside the
``LaptopSegmentAnalyzerCycle`` mutex) and on demand::

    python bot_data_retention.py --data-root <canonical-research-data> [--mode dry-run|enforce|auto]

``auto`` reads ``<state-dir>/mode.json`` (default ``dry_run``). A dry run
deletes nothing and writes ``dry-run-latest.json`` with every candidate and
the bytes it would reclaim.

Nothing is deleted unless ALL of these hold for it:

* custody: the Fly checkpoint (``/files``) records the same sha256 for the file
  as the local bytes (hash match), the laptop has ACKed past it, the latest
  checkpoint parity report is GREEN, and the file was on the laptop before
  that parity run;
* analysis: the latest analysis-archive snapshot verifies (receipt hashes) and
  its promotion (``synced_at``) happened after the file arrived, i.e. a
  completed analyzer generation consumed it and its findings are archived;
* class: Tier B (reconstructible path blobs) as a sealed rotation older than
  ``tier_b_min_age_hours``; Tier A raw rotations and compact partitions only
  under cap pressure, after their compact copy / daily rollup is verified;
  PROTECTED paths (ledgers, relay/Bitfinex evidence, recovery state,
  quarantine, SQLite, research-event generations) never.

Tier A streams are compacted incrementally into zstd Parquet partitioned by
UTC day (``<compact-root>/tierA/<dataset>/v<schema>/date=YYYY-MM-DD/``).
Every deletion is appended (fsynced) to ``<state-dir>/prune-ledger.jsonl``.
After each run a custody receipt (ACK, parity and analyzer-consumed seq) is
POSTed to Fly, which prunes its own copies only up to that receipt.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import analysis_archive
import data_retention_policy as policy

SCHEMA = "bot_data_retention_status_v1"
LEDGER_SCHEMA = "bot_data_prune_ledger_v1"
CUSTODY_SCHEMA = "research_segment_custody_receipt_v1"
COMPACT_SCHEMA = "tier_a_compact_partition_v1"
GIB = 1024 ** 3
GB = 1000 ** 3
DEFAULTS = {
    "shadow_root": r"C:\DoxxedCrypto\fly-mirror-segments",
    "view_root": r"C:\DoxxedCrypto\segment-promotion-view",
    "segment_archive_root": r"C:\DoxxedCrypto\fly-segments",
    "compact_root": r"C:\DoxxedCrypto\bot-data-compact",
    "state_dir": r"C:\DoxxedCrypto\bot-data-retention",
    "historical_roots": (r"C:\DoxxedCrypto\archive",),
    "prefix": "v2",
    "base_url": "https://doxed-btc-bot.fly.dev",
    "cap_bytes": 50 * GB,
    "amber_fraction": 0.80,
    "red_fraction": 0.90,
    "tier_b_min_age_hours": 24.0,
    "segment_archive_days": 7.0,
    "settle_hours": 6.0,
    "max_delete_bytes_per_run": 20 * GB,
}
MODE_DRY_RUN = "dry_run"
MODE_ENFORCE = "enforce"


def _utc_now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value) -> Optional[float]:
    return analysis_archive._parse_ts(value)


def _read_json(path: Path) -> dict:
    return analysis_archive._read_json(path)


def _sha256_file(path: Path) -> str:
    return analysis_archive._sha256_file(path)


def refuse_onedrive(path) -> Path:
    resolved = Path(os.path.abspath(path))
    if "\\onedrive\\" in str(resolved).lower().replace("/", "\\") + "\\":
        raise RuntimeError(f"refusing OneDrive path: {resolved}")
    return resolved


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(temporary, path)


def tree_bytes(root: Path) -> int:
    total = 0
    if not root.is_dir():
        return 0
    for directory, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(directory, name))
            except OSError:
                continue
    return total


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()


# ------------------------------------------------------------------- ledger
class Ledger:
    def __init__(self, state_dir: Path):
        self.path = state_dir / "prune-ledger.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, row: dict) -> None:
        line = json.dumps({"schema": LEDGER_SCHEMA, **row}, sort_keys=True, default=str)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def rows(self) -> list[dict]:
        out = []
        if not self.path.is_file():
            return out
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
        return out


def pruned_index(state_dir: str | Path = DEFAULTS["state_dir"]) -> dict[str, str]:
    """relpath -> sha256 of every mirror-tree file this laptop pruned (for parity)."""
    out = {}
    for row in Ledger(Path(state_dir)).rows():
        if row.get("root") == "tree" and row.get("action") == "DELETE" and row.get("sha256"):
            out[str(row["relpath"])] = str(row["sha256"])
    return out


# ------------------------------------------------------------------ custody
def _fly_files(base_url: str, prefix: str, token: str, timeout: float = 120.0) -> dict:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/api/research-segments/{prefix}/files",
                                     headers={"X-Bot-Admin-Token": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def gather_gates(cfg: dict, data_root: Path, *, now: float, fetch_files=None) -> dict:
    """Collect every custody/analysis fact once; any missing fact disables deletion."""
    shadow = Path(cfg["shadow_root"])
    puller = _read_json(shadow / ".puller" / "state.json")
    parity = _read_json(shadow / "parity-latest.json")
    snapshot_entry = analysis_archive.latest_snapshot(cfg.get("archive_root"))
    snapshot = {}
    snapshot_ok = False
    if snapshot_entry:
        snapshot_ok = analysis_archive.verify_snapshot(snapshot_entry, cfg.get("archive_root"))
        snapshot = _read_json(analysis_archive.archive_root(cfg.get("archive_root"))
                              / snapshot_entry["path"] / "snapshot.json")
    source = snapshot.get("source") or {}
    gates = {
        "applied_seq": int(puller.get("applied_seq") or 0),
        "acked_seq": int(puller.get("acked_seq") or 0),
        "parity_verdict": parity.get("verdict"),
        "parity_seq": int(parity.get("seq") or 0),
        "parity_prefix": parity.get("prefix"),
        "parity_manifest_sha256": parity.get("manifest_sha256"),
        "parity_generated_at": parity.get("generated_at"),
        "parity_generated_ts": _parse_ts(parity.get("generated_at")),
        "snapshot_id": (snapshot_entry or {}).get("snapshot_id"),
        "snapshot_verified": snapshot_ok,
        "snapshot_receipt_sha256": (snapshot_entry or {}).get("receipt_sha256"),
        "analyzer_consumed_seq": source.get("segment_seq_through"),
        "analyzer_synced_at": source.get("synced_at"),
        "analyzer_synced_ts": _parse_ts(source.get("synced_at")),
        "analyzer_generation": (snapshot.get("generation") or {}).get("generation_id"),
        "analyzer_completed_at": (snapshot.get("generation") or {}).get("generated_at"),
        "fly_files": None, "fly_files_seq": None, "fly_files_error": None,
    }
    reasons = []
    if gates["parity_verdict"] != "GREEN":
        reasons.append("PARITY_NOT_GREEN")
    if gates["parity_prefix"] and gates["parity_prefix"] != cfg["prefix"]:
        reasons.append("PARITY_PREFIX_MISMATCH")
    if not gates["parity_seq"] or gates["parity_seq"] > gates["acked_seq"]:
        reasons.append("PARITY_SEQ_NOT_ACKED")
    if not snapshot_ok:
        reasons.append("ANALYSIS_SNAPSHOT_UNVERIFIED")
    if not isinstance(gates["analyzer_consumed_seq"], int) or not gates["analyzer_synced_ts"]:
        reasons.append("ANALYZER_CONSUMED_SEQ_UNKNOWN")
    token = os.environ.get("BOT_ADMIN_TOKEN") or ""
    if fetch_files is None and not token:
        reasons.append("FLY_CHECKPOINT_UNAVAILABLE: BOT_ADMIN_TOKEN not set")
    else:
        try:
            files = (fetch_files or (lambda: _fly_files(cfg["base_url"], cfg["prefix"], token)))()
            gates["fly_files"] = files.get("files") or {}
            gates["fly_files_seq"] = int(files.get("seq") or 0)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            gates["fly_files_error"] = f"{type(exc).__name__}: {exc}"
            reasons.append("FLY_CHECKPOINT_UNAVAILABLE")
    bounds = [gates["acked_seq"], gates["parity_seq"]]
    if isinstance(gates["analyzer_consumed_seq"], int):
        bounds.append(gates["analyzer_consumed_seq"])
    gates["custody_through_seq"] = min(bounds) if all(bounds) else 0
    gates["deny_reasons"] = reasons
    gates["allowed"] = not reasons
    return gates


def _custody_ok(path: Path, relpath: str, gates: dict, mtime: float) -> tuple[bool, str, Optional[str]]:
    """Per-file custody: Fly-recorded sha256 == local bytes, present at parity and promotion."""
    entry = (gates.get("fly_files") or {}).get(relpath)
    if not entry or entry.get("class") != "snapshot" or entry.get("baseline") or not entry.get("sha256"):
        return False, "NOT_A_SHIPPED_SEALED_FILE_ON_FLY_CHECKPOINT", None
    if gates.get("parity_generated_ts") is None or mtime >= gates["parity_generated_ts"]:
        return False, "ARRIVED_AFTER_LAST_GREEN_PARITY", None
    if gates.get("analyzer_synced_ts") is None or mtime >= gates["analyzer_synced_ts"]:
        return False, "NOT_YET_CONSUMED_BY_A_COMPLETED_GENERATION", None
    digest = _sha256_file(path)
    if digest != entry["sha256"]:
        return False, "LOCAL_SHA256_DIFFERS_FROM_FLY_CHECKPOINT", digest
    return True, "CUSTODY_VERIFIED", digest


# --------------------------------------------------------------- tier A compaction
def _ts_of(row: dict, fields: tuple) -> Optional[float]:
    for name in fields:
        value = row.get(name)
        if value not in (None, ""):
            parsed = _parse_ts(value)
            if parsed is not None:
                return parsed
    envelope = row.get("envelope")
    if isinstance(envelope, dict):
        return _ts_of(envelope, ("signal_ts", "ts", "timestamp"))
    return None


def _file_identity(path: Path) -> Optional[str]:
    """Hash of the first complete line: stable while the file grows and across rotation renames."""
    try:
        if path.suffix == ".json":
            return hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open("rb") as handle:
            head = handle.readline(1024 * 1024)
            if ".csv" in path.name.lower():
                # Every CSV rotation shares its header; include the first data row.
                head += handle.readline(1024 * 1024)
    except OSError:
        return None
    if not head.endswith(b"\n"):
        return None
    return hashlib.sha256(head).hexdigest()


class Compactor:
    """Incremental Tier A compaction: staging JSONL per day, Parquet once settled."""

    def __init__(self, compact_root: Path, *, settle_hours: float):
        self.root = compact_root
        self.staging = compact_root / "staging"
        self.state_path = compact_root / "compactor-state.json"
        self.settle_hours = settle_hours
        self.state = _read_json(self.state_path) or {"schema": "tier_a_compactor_state_v1", "sources": {}}

    def save(self) -> None:
        _atomic_json(self.state_path, self.state)

    def ingest(self, tree: Path, *, dry_run: bool) -> dict:
        result = {"rows": 0, "bytes": 0, "sources": 0, "errors": []}
        for relpath, path in sorted(_tier_a_sources(tree).items()):
            spec = policy.tier_a_dataset(relpath)
            if spec is None:
                continue
            dataset, version, ts_fields = spec
            identity = _file_identity(path)
            if identity is None:
                continue
            key = f"{dataset}:{identity}"
            entry = self.state["sources"].get(key) or {"offset": 0, "header": None}
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < int(entry["offset"]):
                result["errors"].append(f"{relpath}: shrank below compacted offset")
                continue
            if size == int(entry["offset"]):
                entry.update({"relpath": relpath, "size": size})
                self.state["sources"][key] = entry
                continue
            result["sources"] += 1
            if dry_run:
                result["bytes"] += size - int(entry["offset"])
                continue
            if path.suffix == ".json":
                self._ingest_document(path, dataset, version, ts_fields, result)
                entry.update({"offset": size, "relpath": relpath, "size": size, "dataset": dataset,
                              "schema_version": version, "updated_at": _iso(_utc_now())})
                self.state["sources"][key] = entry
                self.save()
                continue
            handles: dict[str, object] = {}
            try:
                def sink(day: str, lines: list[str]) -> None:
                    handle = handles.get(day)
                    if handle is None:
                        target = self.staging / dataset / f"v{version}" / f"{day}.jsonl"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        handle = handles[day] = target.open("a", encoding="utf-8")
                    handle.write("\n".join(lines) + "\n")
                    result["rows"] += len(lines)

                consumed, header = self._stream_new(path, int(entry["offset"]), entry.get("header"),
                                                    ts_fields, sink)
            finally:
                for handle in handles.values():
                    handle.flush()
                    os.fsync(handle.fileno())
                    handle.close()
            result["bytes"] += consumed - int(entry["offset"])
            entry.update({"offset": consumed, "header": header, "relpath": relpath, "size": size,
                          "dataset": dataset, "schema_version": version, "updated_at": _iso(_utc_now())})
            self.state["sources"][key] = entry
            self.save()
        if not dry_run:
            self.save()
        return result

    def _ingest_document(self, path: Path, dataset: str, version: int, ts_fields: tuple, result: dict) -> None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result["errors"].append(f"{path.name}: {type(exc).__name__}")
            return
        ts = _ts_of(doc, ts_fields) if isinstance(doc, dict) else None
        ts = ts or path.stat().st_mtime
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        target = self.staging / dataset / f"v{version}" / f"{day}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": ts, "row": text}, separators=(",", ":")) + "\n")
        result["rows"] += 1

    CHUNK_BYTES = 32 * 1024 * 1024

    @classmethod
    def _stream_new(cls, path: Path, offset: int, header, ts_fields: tuple, sink) -> tuple[int, Optional[list]]:
        """Feed complete rows after ``offset`` to ``sink(day, lines)`` in bounded chunks."""
        is_csv = path.name.lower().endswith(".csv") or ".csv." in path.name.lower()
        position = offset
        with path.open("rb") as handle:
            handle.seek(offset)
            carry = b""
            while True:
                block = handle.read(cls.CHUNK_BYTES)
                if not block:
                    break
                data = carry + block
                end = data.rfind(b"\n")
                if end < 0:
                    carry = data
                    continue
                carry = data[end + 1:]
                lines = data[:end + 1].decode("utf-8", errors="replace").splitlines()
                if is_csv and position == 0 and lines:
                    header = next(csv.reader([lines[0]]))
                    lines = lines[1:]
                for day, rows in cls._bucket(lines, header, ts_fields, is_csv).items():
                    sink(day, rows)
                position += end + 1
        return position, header

    @staticmethod
    def _bucket(lines: list[str], header, ts_fields: tuple, is_csv: bool) -> dict[str, list[str]]:
        rows: dict[str, list[str]] = {}
        for line in lines:
            if not line.strip():
                continue
            if is_csv:
                values = next(csv.reader(io.StringIO(line)), [])
                row = dict(zip(header or [], values))
                text = json.dumps(row, sort_keys=True, separators=(",", ":"))
            else:
                try:
                    row = json.loads(line)
                except ValueError:
                    row = {}
                text = line
                if not isinstance(row, dict):
                    row = {}
            ts = _ts_of(row, ts_fields)
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "undated"
            rows.setdefault(day, []).append(json.dumps({"ts": ts, "row": text}, separators=(",", ":")))
        return rows

    def finalize(self, *, now: float, dry_run: bool) -> dict:
        """Convert settled staging days into immutable zstd Parquet partitions."""
        done = {"partitions": [], "bytes": 0}
        if not self.staging.is_dir():
            return done
        for path in sorted(self.staging.glob("*/v*/*.jsonl")):
            day = path.stem
            if day != "undated":
                end = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=1)
                if now < (end + timedelta(hours=self.settle_hours)).timestamp():
                    continue
            elif now - path.stat().st_mtime < 86400:
                continue
            dataset, version = path.parent.parent.name, path.parent.name
            if dry_run:
                done["partitions"].append(f"{dataset}/{version}/{day}")
                continue
            done["partitions"].append(self._write_partition(path, dataset, version, day))
        return done

    def _write_partition(self, staging: Path, dataset: str, version: str, day: str) -> str:
        import pyarrow as pa
        import pyarrow.parquet as pq

        ts_values, rows = [], []
        with staging.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                ts_values.append(item.get("ts"))
                rows.append(item.get("row"))
        target_dir = self.root / "tierA" / dataset / version / f"date={day}"
        target_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(target_dir.glob("part-*.parquet"))
        target = target_dir / f"part-{len(existing):04d}.parquet"
        table = pa.table({"ts": pa.array(ts_values, type=pa.float64()), "row": pa.array(rows, type=pa.string())})
        meta = {b"dataset": dataset.encode(), b"schema_version": version.lstrip("v").encode(),
                b"compact_schema": COMPACT_SCHEMA.encode(), b"day": day.encode()}
        table = table.replace_schema_metadata(meta)
        temporary = target.with_name(f".{target.name}.tmp")
        pq.write_table(table, temporary, compression="zstd", compression_level=9)
        os.replace(temporary, target)
        check = pq.read_table(target)
        if check.num_rows != len(rows):
            raise RuntimeError(f"compact partition row count mismatch: {target}")
        manifest = {"schema": COMPACT_SCHEMA, "dataset": dataset, "schema_version": int(version.lstrip("v")),
                    "day": day, "rows": len(rows), "file": target.name, "sha256": _sha256_file(target),
                    "bytes": target.stat().st_size, "written_at": _iso(_utc_now())}
        _atomic_json(target.with_suffix(".manifest.json"), manifest)
        _unlink(staging)
        return f"{dataset}/{version}/{day}/{target.name}"

    def covered(self, path: Path, dataset: str) -> bool:
        """True when every complete row of ``path`` is already in staging or Parquet."""
        identity = _file_identity(path)
        entry = self.state["sources"].get(f"{dataset}:{identity}") if identity else None
        try:
            return bool(entry) and int(entry["offset"]) >= path.stat().st_size
        except OSError:
            return False

    def schema_status(self) -> list[dict]:
        """Per dataset/version partition counts and compatibility with the current policy."""
        current = {spec[0]: spec[1] for spec in list(policy.TIER_A_DATASETS.values()) + list(policy.TIER_A_GLOBS.values())}
        out = []
        base = self.root / "tierA"
        if not base.is_dir():
            return out
        for dataset_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            for version_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
                version = int(version_dir.name.lstrip("v") or 0)
                partitions = list(version_dir.glob("date=*/part-*.parquet"))
                expected = current.get(dataset_dir.name)
                status = analysis_archive.COMPATIBLE if expected == version else analysis_archive.INCOMPATIBLE
                out.append({"dataset": dataset_dir.name, "schema_version": version,
                            "current_schema_version": expected, "status": status,
                            "partitions": len(partitions),
                            "bytes": sum(p.stat().st_size for p in partitions)})
        return out

    def quarantine_incompatible(self, *, dry_run: bool) -> list[dict]:
        moves = []
        for row in self.schema_status():
            if row["status"] != analysis_archive.INCOMPATIBLE:
                continue
            source = self.root / "tierA" / row["dataset"] / f"v{row['schema_version']}"
            target = self.root / "legacy" / row["dataset"] / f"v{row['schema_version']}"
            moves.append({**row, "moved_to": str(target)})
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
        return moves


def _tier_a_sources(tree: Path) -> dict[str, Path]:
    out = {}
    if not tree.is_dir():
        return out
    for directory, dirnames, files in os.walk(tree):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in files:
            path = Path(directory) / name
            relpath = path.relative_to(tree).as_posix()
            if policy.tier_a_dataset(relpath) is not None and not name.startswith("."):
                out[relpath] = path
    return out


# ------------------------------------------------------------------- runner
class Retention:
    def __init__(self, cfg: dict, data_root: Path, *, now: Optional[float] = None, fetch_files=None,
                 post_custody=None, lock_factory=None):
        self.cfg = cfg
        self.now = float(now if now is not None else _utc_now())
        self.data_root = refuse_onedrive(data_root)
        self.shadow = refuse_onedrive(cfg["shadow_root"])
        self.tree = self.shadow / "tree"
        self.view = refuse_onedrive(cfg["view_root"])
        self.segments = refuse_onedrive(cfg["segment_archive_root"]) / cfg["prefix"]
        self.compact_root = refuse_onedrive(cfg["compact_root"])
        self.state_dir = refuse_onedrive(cfg["state_dir"])
        self.ledger = Ledger(self.state_dir)
        self.fetch_files = fetch_files
        self.post_custody = post_custody
        self.lock_factory = lock_factory

    # ---------------------------------------------------------------- sizes
    def roots(self) -> dict[str, Path]:
        roots = {"mirror_tree": self.tree, "promotion_view": self.view, "canonical": self.data_root,
                 "segment_archive": self.segments.parent, "tier_a_compact": self.compact_root,
                 "analysis_archive": analysis_archive.archive_root(self.cfg.get("archive_root")),
                 "retention_state": self.state_dir}
        for index, extra in enumerate(self.cfg.get("historical_roots") or ()):
            roots[f"historical_{index}"] = Path(extra)
        return roots

    def sizes(self) -> dict[str, int]:
        return {name: tree_bytes(path) for name, path in self.roots().items()}

    def mode(self, requested: str) -> str:
        if requested in (MODE_DRY_RUN, MODE_ENFORCE):
            return requested
        configured = _read_json(self.state_dir / "mode.json").get("mode")
        return configured if configured in (MODE_DRY_RUN, MODE_ENFORCE) else MODE_DRY_RUN

    # ----------------------------------------------------------- candidates
    def _copies(self, relpath: str) -> list[tuple[str, Path]]:
        parts = relpath.split("/")
        return [(name, root.joinpath(*parts)) for name, root in
                (("tree", self.tree), ("view", self.view), ("canonical", self.data_root))]

    def tier_b_candidates(self, gates: dict, *, min_age_hours: float) -> list[dict]:
        out = []
        if not self.tree.is_dir():
            return out
        for path in sorted(self.tree.iterdir()):
            relpath = path.name
            if not path.is_file() or policy.classify(relpath) != policy.TIER_B \
                    or not policy.deletable_rotation(relpath):
                continue
            mtime = path.stat().st_mtime
            if self.now - mtime < min_age_hours * 3600:
                continue
            out.append({"relpath": relpath, "tier": policy.TIER_B, "path": path, "mtime": mtime,
                        "bytes": path.stat().st_size})
        return out

    def tier_a_raw_candidates(self, gates: dict, compactor: Compactor) -> list[dict]:
        out = []
        if not self.tree.is_dir():
            return out
        for path in sorted(self.tree.iterdir()):
            relpath = path.name
            spec = policy.tier_a_dataset(relpath)
            if spec is None or not path.is_file() or not policy.deletable_rotation(relpath):
                continue
            if not compactor.covered(path, spec[0]):
                continue
            out.append({"relpath": relpath, "tier": policy.TIER_A, "path": path, "mtime": path.stat().st_mtime,
                        "bytes": path.stat().st_size})
        return out

    def segment_candidates(self, gates: dict, *, min_age_days: float) -> list[dict]:
        out = []
        seg_dir = self.segments / "seg"
        bound = int(gates.get("custody_through_seq") or 0)
        if not seg_dir.is_dir() or not bound:
            return out
        for path in sorted(seg_dir.glob("*.tar.gz")):
            token = path.name.split(".", 1)[0]
            if not token.isdigit() or int(token) > bound:
                continue
            mtime = path.stat().st_mtime
            if self.now - mtime < min_age_days * 86400:
                continue
            out.append({"relpath": f"{self.cfg['prefix']}/seg/{path.name}", "tier": "SEGMENT_ARCHIVE",
                        "path": path, "mtime": mtime, "bytes": path.stat().st_size, "seq": int(token)})
        return out

    # --------------------------------------------------------------- delete
    def _delete_mirror_file(self, item: dict, gates: dict, *, mode: str, reason: str) -> dict:
        ok, why, digest = _custody_ok(item["path"], item["relpath"], gates, item["mtime"])
        result = {"relpath": item["relpath"], "tier": item["tier"], "custody": why, "bytes": 0,
                  "copies": [], "sha256": digest}
        if not ok:
            return result
        for root_name, copy in self._copies(item["relpath"]):
            if not copy.is_file():
                continue
            size = copy.stat().st_size
            if root_name != "tree" and (size != item["bytes"] or _sha256_file(copy) != digest):
                result["copies"].append({"root": root_name, "kept": "CONTENT_DIFFERS"})
                continue
            result["copies"].append({"root": root_name, "bytes": size})
            result["bytes"] += size
            if mode == MODE_ENFORCE:
                _unlink(copy)
                self.ledger.append({"logged_at": _iso(_utc_now()), "action": "DELETE", "root": root_name,
                                    "relpath": item["relpath"], "bytes": size, "sha256": digest,
                                    "tier": item["tier"], "reason": reason, "gates": _gate_summary(gates)})
        return result

    def _delete_plain(self, item: dict, gates: dict, *, mode: str, reason: str, root_name: str) -> dict:
        size = item["bytes"]
        digest = _sha256_file(item["path"])
        if mode == MODE_ENFORCE:
            _unlink(item["path"])
            self.ledger.append({"logged_at": _iso(_utc_now()), "action": "DELETE", "root": root_name,
                                "relpath": item["relpath"], "bytes": size, "sha256": digest,
                                "tier": item["tier"], "reason": reason, "gates": _gate_summary(gates)})
        return {"relpath": item["relpath"], "tier": item["tier"], "bytes": size, "sha256": digest,
                "custody": "CUSTODY_VERIFIED", "copies": [{"root": root_name, "bytes": size}]}

    def _compact_partition_candidates(self) -> list[dict]:
        """Oldest Tier A partitions whose day has a final rollup (cap pressure only)."""
        base = analysis_archive.archive_root(self.cfg.get("archive_root")) / "rollups" / "daily"
        out = []
        for path in sorted((self.compact_root / "tierA").glob("*/v*/date=*/part-*.parquet")):
            day = path.parent.name.split("=", 1)[1]
            if not (base / f"{day}.json").is_file():
                continue
            out.append({"relpath": path.relative_to(self.compact_root).as_posix(), "tier": "TIER_A_COMPACT",
                        "path": path, "mtime": path.stat().st_mtime, "bytes": path.stat().st_size, "day": day})
        return sorted(out, key=lambda item: (item["day"], item["relpath"]))

    # ------------------------------------------------------------------ run
    def run(self, requested_mode: str = "auto") -> dict:
        mode = self.mode(requested_mode)
        dry = mode != MODE_ENFORCE
        started = _utc_now()
        before = self.sizes()
        total_before = sum(before.values())
        gates = gather_gates(self.cfg, self.data_root, now=self.now, fetch_files=self.fetch_files)
        compactor = Compactor(self.compact_root, settle_hours=float(self.cfg["settle_hours"]))
        compaction = {"ingest": compactor.ingest(self.tree, dry_run=dry),
                      "finalize": compactor.finalize(now=self.now, dry_run=dry),
                      "legacy_moves": compactor.quarantine_incompatible(dry_run=dry)}
        plan: list[dict] = []
        budget = int(self.cfg["max_delete_bytes_per_run"])
        reclaimed = 0
        lock = None
        if gates["allowed"]:
            if self.lock_factory is not None and not dry:
                lock = self.lock_factory(self.shadow / ".puller" / "run.lock")
            try:
                for item in self.tier_b_candidates(gates, min_age_hours=float(self.cfg["tier_b_min_age_hours"])):
                    if reclaimed >= budget:
                        break
                    row = self._delete_mirror_file(item, gates, mode=mode, reason="TIER_B_ANALYZED_AND_ARCHIVED")
                    plan.append(row)
                    reclaimed += row["bytes"]
                for item in self.segment_candidates(gates, min_age_days=float(self.cfg["segment_archive_days"])):
                    if reclaimed >= budget:
                        break
                    row = self._delete_plain(item, gates, mode=mode, reason="SEGMENT_ARCHIVE_RETENTION",
                                             root_name="segment_archive")
                    plan.append(row)
                    reclaimed += row["bytes"]
                cap = self._enforce_cap(gates, compactor, mode=mode, total=total_before - reclaimed)
                plan.extend(cap["deleted"])
                reclaimed += sum(row["bytes"] for row in cap["deleted"])
            finally:
                if lock is not None:
                    lock.release()
        else:
            cap = {"status": "GATES_DENIED", "deleted": []}
        after = self.sizes() if not dry else before
        total_after = sum(after.values()) if not dry else total_before - reclaimed
        cap_bytes = int(self.cfg["cap_bytes"])
        usage = total_after / cap_bytes if cap_bytes else 0.0
        level = "RED" if usage >= float(self.cfg["red_fraction"]) else (
            "AMBER" if usage >= float(self.cfg["amber_fraction"]) else "GREEN")
        custody = self._post_custody(gates, dry=dry)
        status = {
            "schema": SCHEMA, "policy_version": policy.POLICY_VERSION, "mode": mode,
            "started_at": _iso(started), "finished_at": _iso(_utc_now()),
            "cap_bytes": cap_bytes, "bytes_before": total_before, "bytes_after": total_after,
            "usage_fraction": round(usage, 4), "level": level, "sizes_before": before, "sizes_after": after,
            "gates": _gate_summary(gates), "deny_reasons": gates["deny_reasons"],
            "candidates": len(plan),
            "deleted_files": sum(1 for row in plan if row["bytes"]) if not dry else 0,
            "reclaimed_bytes": reclaimed if not dry else 0, "would_reclaim_bytes": reclaimed if dry else 0,
            "by_tier": _by_tier(plan), "kept": [row for row in plan if not row["bytes"]][:50],
            "cap": {k: v for k, v in cap.items() if k != "deleted"},
            "compaction": compaction, "tier_a_schema": compactor.schema_status(),
            "custody_post": custody, "ledger_rows": len(self.ledger.rows()),
            "archive": analysis_archive.archive_status(self.cfg.get("archive_root")),
        }
        target = "dry-run-latest.json" if dry else "status.json"
        _atomic_json(self.state_dir / target, {**status, "plan": [_plan_row(row) for row in plan]})
        if dry:
            _atomic_json(self.state_dir / "status-dry-run.json", status)
        _atomic_json(self.state_dir / "last-run.json", {k: status[k] for k in (
            "mode", "finished_at", "level", "bytes_after", "cap_bytes", "usage_fraction", "deny_reasons",
            "reclaimed_bytes", "would_reclaim_bytes", "ledger_rows")})
        return status

    def _enforce_cap(self, gates: dict, compactor: Compactor, *, mode: str, total: int) -> dict:
        cap_bytes = int(self.cfg["cap_bytes"])
        if total <= cap_bytes:
            return {"status": "WITHIN_CAP", "deleted": []}
        deleted: list[dict] = []
        legacy = [p for p in (self.compact_root / "legacy").rglob("*") if p.is_file()] \
            + [p for p in (analysis_archive.archive_root(self.cfg.get("archive_root")) / "legacy").rglob("*")
               if p.is_file()]
        for path in sorted(legacy, key=lambda p: p.stat().st_mtime):
            if total <= cap_bytes:
                break
            item = {"relpath": str(path), "tier": "LEGACY_INCOMPATIBLE", "path": path,
                    "mtime": path.stat().st_mtime, "bytes": path.stat().st_size}
            row = self._delete_plain(item, gates, mode=mode, reason="CAP_LEGACY_FIRST", root_name="legacy")
            deleted.append(row)
            total -= row["bytes"]
        stages = (
            ("tier_b_any_age", lambda: self.tier_b_candidates(gates, min_age_hours=0.0), "mirror"),
            ("segment_archive_any_age", lambda: self.segment_candidates(gates, min_age_days=0.0), "segment_archive"),
            ("tier_a_raw_compacted", lambda: self.tier_a_raw_candidates(gates, compactor), "mirror"),
            ("tier_a_compact_with_rollup", self._compact_partition_candidates, "tier_a_compact"),
        )
        for stage, produce, kind in stages:
            for item in sorted(produce(), key=lambda it: it["mtime"]):
                if total <= cap_bytes:
                    break
                if kind == "mirror":
                    row = self._delete_mirror_file(item, gates, mode=mode, reason=f"CAP_{stage.upper()}")
                else:
                    row = self._delete_plain(item, gates, mode=mode, reason=f"CAP_{stage.upper()}", root_name=kind)
                deleted.append(row)
                total -= row["bytes"]
        return {"status": "WITHIN_CAP" if total <= cap_bytes else "CAP_EXCEEDED_NOTHING_ELIGIBLE",
                "deleted": deleted, "projected_bytes": total}

    def verified_sealed_files(self, gates: dict) -> dict[str, str]:
        """relpath -> sha256 of top-level rotations whose laptop bytes equal Fly's and were analyzed.

        Fly deletes a rotation only when it is listed here; files this laptop
        already pruned stay listed with the sha256 verified at deletion.
        """
        fly = gates.get("fly_files") or {}
        cache_path = self.state_dir / "sha-cache.json"
        cache = _read_json(cache_path)
        fresh: dict[str, dict] = {}
        out: dict[str, str] = {}
        if self.tree.is_dir():
            for path in self.tree.iterdir():
                relpath = path.name
                entry = fly.get(relpath)
                if (not path.is_file() or policy.rotation_parts(relpath) is None or not entry
                        or entry.get("class") != "snapshot" or entry.get("baseline") or not entry.get("sha256")):
                    continue
                st = path.stat()
                if st.st_mtime >= (gates.get("parity_generated_ts") or 0) \
                        or st.st_mtime >= (gates.get("analyzer_synced_ts") or 0):
                    continue
                key = f"{st.st_size}:{st.st_mtime_ns}"
                cached = cache.get(relpath) or {}
                digest = cached.get("sha256") if cached.get("key") == key else _sha256_file(path)
                fresh[relpath] = {"key": key, "sha256": digest}
                if digest == entry["sha256"]:
                    out[relpath] = digest
        for relpath, digest in pruned_index(self.state_dir).items():
            if "/" not in relpath and (fly.get(relpath) or {}).get("sha256") == digest:
                out.setdefault(relpath, digest)
        _atomic_json(cache_path, fresh)
        return dict(sorted(out.items()))

    def _post_custody(self, gates: dict, *, dry: bool) -> dict:
        through = int(gates.get("custody_through_seq") or 0)
        if not gates["allowed"] or not through:
            return {"posted": False, "reason": "GATES_DENIED", "deny_reasons": gates["deny_reasons"]}
        manifest = self.segments / "man" / f"{through:012d}.json"
        if not manifest.is_file():
            return {"posted": False, "reason": "ARCHIVED_MANIFEST_MISSING", "through_seq": through}
        receipt = {
            "schema": CUSTODY_SCHEMA, "prefix": self.cfg["prefix"], "through_seq": through,
            "manifest_sha256": _sha256_file(manifest),
            "acked_seq": gates["acked_seq"], "parity_seq": gates["parity_seq"],
            "parity_verdict": gates["parity_verdict"], "parity_generated_at": gates["parity_generated_at"],
            "analyzer_consumed_seq": gates["analyzer_consumed_seq"],
            "analyzer_generation_id": gates["analyzer_generation"],
            "analysis_snapshot_id": gates["snapshot_id"],
            "analysis_snapshot_receipt_sha256": gates["snapshot_receipt_sha256"],
            "issued_at": _iso(self.now), "laptop_mode": MODE_DRY_RUN if dry else MODE_ENFORCE,
            "verified_files": self.verified_sealed_files(gates),
        }
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("ascii")
        poster = self.post_custody or (lambda body: _http_post_custody(self.cfg, body))
        try:
            status, payload = poster(raw)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return {"posted": False, "through_seq": through, "error": f"{type(exc).__name__}: {exc}"}
        return {"posted": status in (200, 201), "http_status": status, "through_seq": through,
                "verified_files": len(receipt["verified_files"]), "response": payload}


def _http_post_custody(cfg: dict, body: bytes) -> tuple[int, dict]:
    token = os.environ.get("BOT_ADMIN_TOKEN") or ""
    if not token:
        raise ValueError("BOT_ADMIN_TOKEN not set")
    request = urllib.request.Request(
        f"{cfg['base_url'].rstrip('/')}/api/research-segments/{cfg['prefix']}/custody", data=body, method="POST",
        headers={"X-Bot-Admin-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        return exc.code, payload


def _gate_summary(gates: dict) -> dict:
    return {k: gates.get(k) for k in (
        "applied_seq", "acked_seq", "parity_verdict", "parity_seq", "parity_generated_at",
        "snapshot_id", "snapshot_verified", "analyzer_consumed_seq", "analyzer_synced_at",
        "analyzer_generation", "custody_through_seq", "fly_files_seq", "fly_files_error", "allowed")}


def _plan_row(row: dict) -> dict:
    return {k: row.get(k) for k in ("relpath", "tier", "bytes", "sha256", "custody", "copies")}


def _by_tier(plan: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for row in plan:
        cell = out.setdefault(row["tier"], {"files": 0, "bytes": 0, "kept": 0})
        if row["bytes"]:
            cell["files"] += 1
            cell["bytes"] += row["bytes"]
        else:
            cell["kept"] += 1
    return out


def load_config(args) -> dict:
    cfg = dict(DEFAULTS)
    for key in ("shadow_root", "view_root", "segment_archive_root", "compact_root", "state_dir", "prefix",
                "base_url", "archive_root"):
        value = getattr(args, key, None)
        if value:
            cfg[key] = value
    if getattr(args, "cap_gb", None):
        cfg["cap_bytes"] = int(float(args.cap_gb) * GB)
    return cfg


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", required=True, help="canonical-research-data the analyzer reads")
    parser.add_argument("--mode", default="auto", choices=("auto", "dry-run", "enforce"))
    parser.add_argument("--shadow-root")
    parser.add_argument("--view-root")
    parser.add_argument("--segment-archive-root")
    parser.add_argument("--compact-root")
    parser.add_argument("--state-dir")
    parser.add_argument("--archive-root")
    parser.add_argument("--prefix")
    parser.add_argument("--base-url")
    parser.add_argument("--cap-gb", type=float)
    parser.add_argument("--set-mode", choices=("dry-run", "enforce"),
                        help="persist the mode used by --mode auto, then exit")
    args = parser.parse_args(argv)
    cfg = load_config(args)
    state_dir = refuse_onedrive(cfg["state_dir"])
    if args.set_mode:
        mode = MODE_ENFORCE if args.set_mode == "enforce" else MODE_DRY_RUN
        _atomic_json(state_dir / "mode.json", {"mode": mode, "set_at": _iso(_utc_now())})
        print(json.dumps({"ok": True, "mode": mode}))
        return 0
    from research_segment_puller import PullerError, _RunLock

    def lock_factory(path):
        try:
            return _RunLock(path)
        except PullerError as exc:
            raise RuntimeError("shadow-root lock busy; retry next cycle") from exc

    requested = {"dry-run": MODE_DRY_RUN, "enforce": MODE_ENFORCE}.get(args.mode, "auto")
    try:
        status = Retention(cfg, Path(args.data_root), lock_factory=lock_factory).run(requested)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 3
    summary = {k: status[k] for k in ("mode", "level", "bytes_before", "bytes_after", "reclaimed_bytes",
                                      "would_reclaim_bytes", "deny_reasons", "by_tier")}
    print(json.dumps({"ok": True, **summary}, sort_keys=True))
    return 0 if status["level"] != "RED" else 1


if __name__ == "__main__":
    sys.exit(main())
