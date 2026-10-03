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
import shutil
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
import storage_links

SCHEMA = "bot_data_retention_status_v1"
LEDGER_SCHEMA = "bot_data_prune_ledger_v1"
CUSTODY_SCHEMA = "research_segment_custody_receipt_v1"
COMPACT_SCHEMA = "tier_a_compact_partition_v1"
GIB = 1024 ** 3
GB = 1000 ** 3
LOCK_WAIT_SEC = 180.0
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
    "wof_max_bytes_per_run": 4 * GB,
    # Laptop-only derived artifacts (never mirror data, ledgers, recovery state or evidence objects).
    "session_archive_keep": 10,
    "session_archive_min_age_days": 3.0,
    "log_roots": (r"C:\DoxxedCrypto\laptop-chain\logs", r"C:\DoxxedCrypto\fly-mirror-segments\logs"),
    "log_retain_days": 14.0,
}
LOG_SUFFIXES = (".log", ".out", ".err")
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


def tree_bytes(root: Path, seen: Optional[set] = None) -> int:
    """Physical bytes under root: a hardlinked file counts once (across calls sharing ``seen``)."""
    total = 0
    if not root.is_dir():
        return 0
    seen = set() if seen is None else seen
    for directory, _dirs, files in os.walk(root):
        for name in files:
            try:
                st = os.stat(os.path.join(directory, name))
            except OSError:
                continue
            if st.st_ino:
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            total += st.st_size
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
UNDATED = "undated"           # staging token: rows awaiting a timestamp
CLOSED = "closed"             # staging token: rows proven to carry no timestamp
UNDATED_PARTITION = "undated"  # date=undated partition holding closed rows (readers skip it)
JOIN_INDEX_NAME = "tier-a-join-index.json"
JOIN_INDEX_SCHEMA = "tier_a_join_index_v1"
BACKFILL_SCHEMA = "tier_a_backfill_receipt_v1"
HEALTH_SCHEMA = "tier_a_health_v1"
ROW_GROUP_BYTES = 32 * 1024 * 1024
_DIGEST_MOD = 1 << 256


class TierAVerificationError(RuntimeError):
    """A compact artifact failed its row-count / content-hash check; inputs are kept."""


def _ts_of(row: dict, fields: tuple) -> Optional[float]:
    return policy.row_timestamp(row, fields)[0]


def _day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _row_dict(text) -> Optional[dict]:
    if not isinstance(text, str):
        return None
    try:
        row = json.loads(text)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def _canonical(ts, row) -> bytes:
    return json.dumps([ts, row], separators=(",", ":")).encode("utf-8", "surrogatepass")


def _row_digest(row) -> int:
    text = row if isinstance(row, str) else json.dumps(row, separators=(",", ":"))
    return int(hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(), 16)


def _staging_token(path: Path) -> str:
    """Day token of a staging file: ``<token>.jsonl`` or ``<token>__<tag>.seg``."""
    name = path.name
    for suffix in (".jsonl", ".seg"):
        if name.endswith(suffix):
            return name[:-len(suffix)].split("__", 1)[0]
    return ""


def _is_staging_file(path: Path) -> bool:
    return path.is_file() and not path.name.startswith(".") and path.name.endswith((".jsonl", ".seg"))


def _iter_staging(path: Path):
    """Staging items; a corrupt line becomes an undated item carrying the raw text."""
    with path.open(encoding="utf-8", errors="surrogateescape") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                item = None
            if not isinstance(item, dict) or "row" not in item:
                item = {"ts": None, "row": line.rstrip("\r\n"), "corrupt": True}
            yield item


def _count_lines(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(8 * 1024 * 1024)
            if not block:
                return count
            count += block.count(b"\n")


def _csv_complete_end(data: bytes) -> int:
    """Byte length of the complete CSV records at the start of ``data`` (quotes balanced)."""
    end = 0
    quotes = 0
    start = 0
    while True:
        newline = data.find(b"\n", start)
        if newline < 0:
            return end
        quotes += data.count(b'"', start, newline + 1)
        start = newline + 1
        if quotes % 2 == 0:
            end = start
            quotes = 0


def _csv_records(text: str) -> list[str]:
    """Split complete CSV text into records; quoted fields may span lines."""
    records, pending, quotes = [], [], 0
    for line in text.splitlines(keepends=True):
        pending.append(line)
        quotes += line.count('"')
        if quotes % 2 == 0:
            records.append("".join(pending).rstrip("\r\n"))
            pending, quotes = [], 0
    if pending:
        records.append("".join(pending).rstrip("\r\n"))
    return records


def _csv_row(record: str, header) -> dict:
    values = next(csv.reader(io.StringIO(record)), [])
    return dict(zip(header or [], values))


def _readback_digest(path: Path) -> tuple[int, str]:
    """(rows, content sha256) re-read from a written Parquet file."""
    import pyarrow.parquet as pq

    digest = hashlib.sha256()
    rows = 0
    with open(path, "rb") as handle:
        for batch in pq.ParquetFile(handle).iter_batches(batch_size=4096, columns=["ts", "row"]):
            for ts, row in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
                digest.update(_canonical(ts, row) + b"\n")
                rows += 1
    return rows, digest.hexdigest()


def _parquet_rows(path: Path) -> int:
    import pyarrow.parquet as pq

    with open(path, "rb") as handle:  # explicit close: Windows cannot move a dir holding an open file
        return pq.ParquetFile(handle).metadata.num_rows


def _parquet_column(path: Path, name: str) -> list:
    import pyarrow.parquet as pq

    with open(path, "rb") as handle:
        return pq.read_table(handle, columns=[name]).column(0).to_pylist()


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
    """Incremental Tier A compaction: staging per UTC day, verified zstd Parquet once settled.

    Staging files live in ``staging/<dataset>/v<N>/`` as ``<token>.jsonl`` (live
    appends) or ``<token>__<tag>.seg`` (re-bucketed / archive segments). Token
    ``undated`` holds rows still awaiting a timestamp, ``closed`` rows proven to
    carry none (promoted to ``date=undated``). Staging is removed only after the
    Parquet copy re-reads with the same row count and content sha256.
    """

    CHUNK_BYTES = 32 * 1024 * 1024

    def __init__(self, compact_root: Path, *, settle_hours: float, now: Optional[float] = None):
        self.root = compact_root
        self.staging = compact_root / "staging"
        self.state_path = compact_root / "compactor-state.json"
        self.join_path = compact_root / JOIN_INDEX_NAME
        self.settle_hours = settle_hours
        self.now = float(now if now is not None else _utc_now())
        self.state = _read_json(self.state_path) or {"schema": "tier_a_compactor_state_v1", "sources": {}}
        self.state.setdefault("sources", {})
        self._join: Optional[dict] = None
        self._join_dirty = False
        self._part_ok: dict[str, tuple] = {}

    # ----------------------------------------------------------- state
    @property
    def join(self) -> dict:
        if self._join is None:
            self._join = dict((_read_json(self.join_path) or {}).get("keys") or {})
        return self._join

    def save(self) -> None:
        _atomic_json(self.state_path, self.state)
        if self._join_dirty:
            _atomic_json(self.join_path, {"schema": JOIN_INDEX_SCHEMA, "updated_at": _iso(_utc_now()),
                                          "keys": self.join})
            self._join_dirty = False

    def _resolve(self, dataset: str, row: Optional[dict], fields: tuple, *, feed: bool) -> Optional[float]:
        if row is None:
            return None
        ts = _ts_of(row, fields)
        if ts is not None:
            if feed and dataset in policy.TIER_A_JOIN_FEEDERS:
                index = self.join
                for key in policy.join_keys(row, policy.TIER_A_JOIN_FEEDERS[dataset]):
                    if key not in index or ts < index[key]:
                        index[key] = ts
                        self._join_dirty = True
            return ts
        if dataset in policy.TIER_A_JOIN_RESOLVED:
            index = self.join
            found = [index[key] for key in policy.join_keys(row, policy.TIER_A_JOIN_KEYS) if key in index]
            if found:
                return min(found)
        return None

    # ---------------------------------------------------------- ingest
    def ingest(self, tree: Path, *, dry_run: bool) -> dict:
        result = {"rows": 0, "undated_rows": 0, "bytes": 0, "sources": 0, "errors": []}
        for relpath, path in sorted(_tier_a_sources(tree).items()):
            spec = policy.tier_a_dataset(relpath)
            if spec is None:
                continue
            dataset, version, ts_fields = spec
            identity = _file_identity(path)
            if identity is None:
                continue
            key = f"{dataset}:{identity}"
            entry = self.state["sources"].get(key) or {"offset": 0, "header": None, "days": []}
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
                day = self._ingest_document(path, dataset, version, ts_fields, result)
                if day:
                    entry["days"] = sorted(set(entry.get("days") or []) | {day})
                entry.update({"offset": size, "relpath": relpath, "size": size, "dataset": dataset,
                              "schema_version": version, "updated_at": _iso(_utc_now())})
                self.state["sources"][key] = entry
                self.save()
                continue
            consumed, header, days, undated = self._append_stream(
                path, int(entry["offset"]), entry.get("header"), dataset, version, ts_fields, result,
                tag=None)
            result["bytes"] += consumed - int(entry["offset"])
            if entry.get("days") is not None or int(entry["offset"]) == 0:
                entry["days"] = sorted(set(entry.get("days") or []) | days)
            entry["undated_rows"] = int(entry.get("undated_rows") or 0) + undated
            entry.update({"offset": consumed, "header": header, "relpath": relpath, "size": size,
                          "dataset": dataset, "schema_version": version, "updated_at": _iso(_utc_now())})
            self.state["sources"][key] = entry
            self.save()
        if not dry_run:
            self.save()
        return result

    def _append_stream(self, path: Path, offset: int, header, dataset: str, version: int, ts_fields: tuple,
                       result: dict, *, tag: Optional[str]) -> tuple[int, Optional[list], set, int]:
        """Stream rows after ``offset`` into staging; ``tag`` writes ``<token>__<tag>.seg`` files."""
        handles: dict[str, object] = {}
        days: set = set()
        undated = [0]
        try:
            def sink(token: str, lines: list[str]) -> None:
                handle = handles.get(token)
                if handle is None:
                    name = f"{token}.jsonl" if tag is None else f"{token}__{tag}.seg"
                    target = self.staging / dataset / f"v{version}" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    handle = handles[token] = target.open("a", encoding="utf-8", errors="surrogateescape")
                handle.write("\n".join(lines) + "\n")
                result["rows"] += len(lines)
                if token == UNDATED:
                    undated[0] += len(lines)
                    result["undated_rows"] = result.get("undated_rows", 0) + len(lines)
                else:
                    days.add(token)

            consumed, header = self._stream_new(path, offset, header, dataset, ts_fields, sink)
        finally:
            for handle in handles.values():
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
        return consumed, header, days, undated[0]

    def _ingest_document(self, path: Path, dataset: str, version: int, ts_fields: tuple,
                         result: dict) -> Optional[str]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result["errors"].append(f"{path.name}: {type(exc).__name__}")
            return None
        ts = _ts_of(doc, ts_fields) if isinstance(doc, dict) else None
        ts = ts or policy.parse_timestamp(path.stat().st_mtime)
        day = _day_of(ts) if ts is not None else UNDATED
        target = self.staging / dataset / f"v{version}" / f"{day}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        item = {"ts": ts, "row": text} if ts is not None else {"ts": None, "row": text, "seen": self.now}
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, separators=(",", ":")) + "\n")
        result["rows"] += 1
        return day if ts is not None else None

    def _stream_new(self, path: Path, offset: int, header, dataset: str, ts_fields: tuple,
                    sink) -> tuple[int, Optional[list]]:
        """Feed complete rows after ``offset`` to ``sink(token, lines)`` in bounded chunks.

        CSV records end only where quotes balance, so quoted multi-line fields
        stay one record; an unterminated trailing record is left for the next run.
        """
        is_csv = path.name.lower().endswith(".csv") or ".csv." in path.name.lower()
        position = offset
        with path.open("rb") as handle:
            handle.seek(offset)
            carry = b""
            while True:
                block = handle.read(self.CHUNK_BYTES)
                if not block:
                    break
                data = carry + block
                end = _csv_complete_end(data) if is_csv else data.rfind(b"\n") + 1
                if end <= 0:
                    carry = data
                    continue
                carry = data[end:]
                text = data[:end].decode("utf-8", errors="replace")
                records = _csv_records(text) if is_csv else text.splitlines()
                if is_csv and position == 0 and records:
                    header = next(csv.reader(io.StringIO(records[0])), [])
                    records = records[1:]
                for token, rows in self._bucket(records, header, dataset, ts_fields, is_csv).items():
                    sink(token, rows)
                position += end
        return position, header

    def _bucket(self, lines: list[str], header, dataset: str, ts_fields: tuple,
                is_csv: bool) -> dict[str, list[str]]:
        rows: dict[str, list[str]] = {}
        for line in lines:
            if not line.strip():
                continue
            if is_csv:
                row = _csv_row(line, header)
                text = json.dumps(row, sort_keys=True, separators=(",", ":"))
            else:
                row = _row_dict(line)
                text = line
            ts = self._resolve(dataset, row, ts_fields, feed=True)
            if ts is None:
                token, item = UNDATED, {"ts": None, "row": text, "seen": self.now}
            else:
                token, item = _day_of(ts), {"ts": ts, "row": text}
            rows.setdefault(token, []).append(json.dumps(item, separators=(",", ":")))
        return rows

    # -------------------------------------------------------- finalize
    def _version_dirs(self, base: Path):
        if not base.is_dir():
            return
        for dataset_dir in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for version_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir() and p.name.startswith("v")):
                yield dataset_dir.name, version_dir

    def staging_files(self, dataset: Optional[str] = None) -> list[Path]:
        out = []
        for name, version_dir in self._version_dirs(self.staging):
            if dataset is None or name == dataset:
                out.extend(sorted(p for p in version_dir.iterdir() if _is_staging_file(p)))
        return out

    @staticmethod
    def _needs_rebucket(token: str) -> bool:
        return token == UNDATED or (token != CLOSED and not policy.valid_day(token))

    def finalize(self, *, now: float, dry_run: bool, close_unresolved: bool = False) -> dict:
        """Re-date undated rows by content, then promote settled days to verified Parquet."""
        done = {"partitions": [], "rebucketed": [], "closed_rows": 0, "pending_undated_rows": 0,
                "errors": []}
        if not self.staging.is_dir():
            return done
        specs = policy.tier_a_specs()
        work = []
        for dataset, version_dir in self._version_dirs(self.staging):
            sources = [p for p in sorted(version_dir.iterdir())
                       if _is_staging_file(p) and self._needs_rebucket(_staging_token(p))]
            if sources:
                work.append((dataset, version_dir, sources, specs.get(dataset, (0, ()))[1]))
        # Pass 1: every directly dated row publishes its join keys before any join lookup.
        for dataset, _version_dir, sources, fields in work:
            if dataset in policy.TIER_A_JOIN_FEEDERS:
                for source in sources:
                    for item in _iter_staging(source):
                        self._resolve(dataset, _row_dict(item.get("row")), fields, feed=True)
        for dataset, version_dir, sources, fields in work:
            for source in sources:
                try:
                    report = self._rebucket(dataset, version_dir, source, fields, now=now, dry_run=dry_run,
                                            close_unresolved=close_unresolved)
                except TierAVerificationError as exc:
                    done["errors"].append(str(exc))
                    continue
                done["rebucketed"].append(report)
                done["closed_rows"] += report["closed"]
                done["pending_undated_rows"] += report["pending"]
        for dataset, version_dir in self._version_dirs(self.staging):
            groups: dict[str, list[Path]] = {}
            for path in sorted(version_dir.iterdir()):
                if _is_staging_file(path):
                    groups.setdefault(_staging_token(path), []).append(path)
            for token, files in sorted(groups.items()):
                if token == CLOSED:
                    day = UNDATED_PARTITION
                elif policy.valid_day(token):
                    end = datetime.fromisoformat(token).replace(tzinfo=timezone.utc) + timedelta(days=1)
                    if now < (end + timedelta(hours=self.settle_hours)).timestamp():
                        continue
                    day = token
                else:
                    continue
                if dry_run:
                    done["partitions"].append(f"{dataset}/{version_dir.name}/{day}")
                    continue
                try:
                    done["partitions"].append(self._write_partition(files, dataset, version_dir.name, day))
                except TierAVerificationError as exc:
                    done["errors"].append(str(exc))
        if not dry_run:
            self.save()
        return done

    def _rebucket(self, dataset: str, version_dir: Path, source: Path, fields: tuple, *, now: float,
                  dry_run: bool, close_unresolved: bool) -> dict:
        """Route one undated/invalid-day staging file by content; verified before the source is removed."""
        tag = f"rb-{_sha256_file(source)[:16]}"
        temps: dict[str, Path] = {}
        handles: dict[str, object] = {}
        counts: dict[str, int] = {}
        rows_in, digest_in = 0, 0
        grace = self.settle_hours * 3600.0
        try:
            for item in _iter_staging(source):
                rows_in += 1
                text = item.get("row")
                digest_in = (digest_in + _row_digest(text)) % _DIGEST_MOD
                ts = None if item.get("corrupt") else self._resolve(dataset, _row_dict(text), fields, feed=False)
                if ts is not None:
                    token, out = _day_of(ts), {"ts": ts, "row": text}
                else:
                    seen = item.get("seen")
                    old = isinstance(seen, (int, float)) and now - float(seen) >= grace
                    if close_unresolved or old or item.get("corrupt"):
                        token, out = CLOSED, {"ts": None, "row": text}
                    else:
                        token = UNDATED
                        out = {"ts": None, "row": text, "seen": seen if isinstance(seen, (int, float)) else now}
                counts[token] = counts.get(token, 0) + 1
                if dry_run:
                    continue
                handle = handles.get(token)
                if handle is None:
                    temps[token] = version_dir / f".{token}__{tag}.seg.tmp"
                    handle = handles[token] = temps[token].open("w", encoding="utf-8", errors="surrogateescape")
                handle.write(json.dumps(out, separators=(",", ":")) + "\n")
        finally:
            for handle in handles.values():
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
        report = {"dataset": dataset, "source": source.name, "rows_in": rows_in,
                  "dated": sum(v for k, v in counts.items() if k not in (UNDATED, CLOSED)),
                  "pending": counts.get(UNDATED, 0), "closed": counts.get(CLOSED, 0),
                  "days": sorted(k for k in counts if k not in (UNDATED, CLOSED)), "status": "PLANNED"}
        if dry_run:
            return report
        unchanged = set(counts) <= {UNDATED} and source.name.endswith(".seg")
        if unchanged:
            for temp in temps.values():
                _unlink(temp)
            report["status"] = "UNCHANGED_PENDING"
            return report
        rows_out, digest_out = 0, 0
        for temp in temps.values():
            for item in _iter_staging(temp):
                rows_out += 1
                digest_out = (digest_out + _row_digest(item.get("row"))) % _DIGEST_MOD
        if rows_out != rows_in or digest_out != digest_in:
            for temp in temps.values():
                _unlink(temp)
            raise TierAVerificationError(
                f"rebucket verification failed for {dataset}/{source.name}: rows {rows_in}->{rows_out}")
        for token, temp in temps.items():
            os.replace(temp, version_dir / f"{token}__{tag}.seg")
        _unlink(source)
        report.update({"status": "REBUCKETED", "rows_out": rows_out,
                       "row_multiset_sha256": f"{digest_in:064x}"})
        return report

    def _existing_ts(self, target_dir: Path) -> set:
        import pyarrow.parquet as pq

        out: set = set()
        for part in sorted(target_dir.glob("part-*.parquet")):
            out.update(v for v in _parquet_column(part, "ts") if v is not None)
        return out

    def _write_partition(self, files: list[Path], dataset: str, version: str, day: str) -> dict:
        import pyarrow as pa
        import pyarrow.parquet as pq

        sources = [{"name": p.name, "sha256": _sha256_file(p), "bytes": p.stat().st_size} for p in files]
        target_dir = self.root / "tierA" / dataset / version / f"date={day}"
        target_dir.mkdir(parents=True, exist_ok=True)
        rel = f"{dataset}/{version}/{day}"
        for manifest_path in sorted(target_dir.glob("part-*.manifest.json")):
            manifest = _read_json(manifest_path)
            if manifest.get("staging_sources") == sources and self.verified_part(target_dir / manifest["file"]):
                for path in files:
                    _unlink(path)
                return {"partition": f"{rel}/{manifest['file']}", "status": "ALREADY_PROMOTED",
                        "rows": manifest.get("rows"), "content_sha256": manifest.get("content_sha256")}
        dedupe = dataset in policy.TIER_A_DEDUPE_BY_TS
        seen_ts = self._existing_ts(target_dir) if dedupe else set()
        numbers = [int(p.stem.split("-")[1]) for p in target_dir.glob("part-*.parquet")
                   if p.stem.split("-")[1].isdigit()]
        target = target_dir / f"part-{(max(numbers) + 1) if numbers else 0:04d}.parquet"
        temporary = target_dir / f".{target.name}.tmp"
        meta = {b"dataset": dataset.encode(), b"schema_version": version.lstrip("v").encode(),
                b"compact_schema": COMPACT_SCHEMA.encode(), b"day": day.encode()}
        schema = pa.schema([("ts", pa.float64()), ("row", pa.string())]).with_metadata(meta)
        digest = hashlib.sha256()
        rows_in = rows = dropped = 0
        batch_ts: list = []
        batch_rows: list = []
        batch_bytes = 0
        writer = pq.ParquetWriter(temporary, schema, compression="zstd", compression_level=9)
        try:
            for path in files:
                for item in _iter_staging(path):
                    rows_in += 1
                    ts = item.get("ts")
                    ts = float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
                    row = item.get("row")
                    row = row if isinstance(row, str) else json.dumps(row, separators=(",", ":"))
                    row = row.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
                    if dedupe and ts is not None:
                        if ts in seen_ts:
                            dropped += 1
                            continue
                        seen_ts.add(ts)
                    digest.update(_canonical(ts, row) + b"\n")
                    rows += 1
                    batch_ts.append(ts)
                    batch_rows.append(row)
                    batch_bytes += len(row)
                    if batch_bytes >= ROW_GROUP_BYTES:
                        writer.write_table(pa.table({"ts": pa.array(batch_ts, pa.float64()),
                                                     "row": pa.array(batch_rows, pa.string())}, schema=schema))
                        batch_ts, batch_rows, batch_bytes = [], [], 0
            if batch_rows or rows == 0:
                writer.write_table(pa.table({"ts": pa.array(batch_ts, pa.float64()),
                                             "row": pa.array(batch_rows, pa.string())}, schema=schema))
        finally:
            writer.close()
        content = digest.hexdigest()
        if rows == 0:
            _unlink(temporary)
            for path in files:
                _unlink(path)
            return {"partition": rel, "status": "ALL_DUPLICATES", "rows": 0, "rows_in": rows_in,
                    "duplicates_dropped": dropped}
        back_rows, back_digest = _readback_digest(temporary)
        if back_rows != rows or back_digest != content:
            _unlink(temporary)
            raise TierAVerificationError(
                f"partition verification failed for {rel}: rows {rows}->{back_rows}, "
                f"sha256 {content[:12]}->{back_digest[:12]}; staging kept")
        os.replace(temporary, target)
        manifest = {"schema": COMPACT_SCHEMA, "dataset": dataset, "schema_version": int(version.lstrip("v")),
                    "day": day, "rows": rows, "rows_in": rows_in, "duplicates_dropped": dropped,
                    "content_sha256": content, "file": target.name, "sha256": _sha256_file(target),
                    "bytes": target.stat().st_size, "staging_sources": sources, "written_at": _iso(_utc_now())}
        _atomic_json(target.with_suffix(".manifest.json"), manifest)
        for path in files:
            _unlink(path)
        return {"partition": f"{rel}/{target.name}", "status": "PROMOTED", "rows": rows, "rows_in": rows_in,
                "duplicates_dropped": dropped, "content_sha256": content, "sha256": manifest["sha256"],
                "bytes": manifest["bytes"]}

    # ------------------------------------------------------ verification
    def verified_part(self, part: Path) -> bool:
        """Manifest present, file sha256 and row count match it."""
        try:
            st = part.stat()
        except OSError:
            return False
        key = (st.st_size, st.st_mtime_ns)
        cached = self._part_ok.get(str(part))
        if cached and cached[0] == key:
            return cached[1]
        manifest = _read_json(part.with_suffix(".manifest.json"))
        ok = bool(manifest) and manifest.get("sha256") == _sha256_file(part)
        if ok:
            try:
                ok = _parquet_rows(part) == int(manifest.get("rows") or -1)
            except (OSError, ValueError):
                ok = False
        self._part_ok[str(part)] = (key, ok)
        return ok

    def verified_day(self, dataset: str, version: int, day: str) -> bool:
        target_dir = self.root / "tierA" / dataset / f"v{version}" / f"date={day}"
        parts = sorted(target_dir.glob("part-*.parquet")) if target_dir.is_dir() else []
        return bool(parts) and all(self.verified_part(p) for p in parts)

    def covered(self, path: Path, dataset: str) -> bool:
        """True only when every row of ``path`` sits in a verified Parquet partition.

        Staging never counts: the source must be fully consumed, its days known,
        each day promoted and verified, and no staging remain that could still
        hold its rows (any undated row of the source blocks until staging is empty).
        """
        identity = _file_identity(path)
        entry = self.state["sources"].get(f"{dataset}:{identity}") if identity else None
        try:
            if not entry or int(entry["offset"]) < path.stat().st_size:
                return False
        except OSError:
            return False
        days = entry.get("days")
        version = entry.get("schema_version") or policy.tier_a_specs().get(dataset, (1, ()))[0]
        if days is None or not days:
            return False
        staging = self.staging_files(dataset)
        if int(entry.get("undated_rows") or 0) and staging:
            return False
        latest = max(days)
        for path_ in staging:
            token = _staging_token(path_)
            if self._needs_rebucket(token) or token == CLOSED or token <= latest:
                return False
        return all(self.verified_day(dataset, int(version), day) for day in days)

    def attribute_days(self, tree: Path) -> list[str]:
        """Record the UTC days of legacy state entries (ingested before day tracking)."""
        updated = []
        for relpath, path in sorted(_tier_a_sources(tree).items()):
            spec = policy.tier_a_dataset(relpath)
            if spec is None or path.suffix == ".json":
                continue
            identity = _file_identity(path)
            entry = self.state["sources"].get(f"{spec[0]}:{identity}") if identity else None
            if not entry or entry.get("days") is not None or "/" in relpath:
                continue
            days: set = set()
            undated = [0]

            def sink(token: str, lines: list[str]) -> None:
                if token == UNDATED:
                    undated[0] += len(lines)
                else:
                    days.add(token)

            consumed, _header = self._stream_new(path, 0, None, spec[0], spec[2], sink)
            if consumed < int(entry["offset"]):
                continue
            entry["days"] = sorted(days)
            entry["undated_rows"] = undated[0]
            updated.append(relpath)
        return updated

    # ----------------------------------------------------------- health
    def health(self, *, now: float, backfilled: bool) -> dict:
        """Per-dataset Tier A promotion health; level RED / AMBER / GREEN."""
        import pyarrow.parquet as pq

        specs = policy.tier_a_specs()
        names = set(specs)
        for base in (self.root / "tierA", self.staging):
            names.update(name for name, _ in self._version_dirs(base))
        settle = self.settle_hours * 3600.0
        rank = {"GREEN": 0, "AMBER": 1, "RED": 2}
        datasets = []
        overall = "GREEN"
        for dataset in sorted(names):
            fields = specs.get(dataset, (None, ()))[1]
            promoted, rows_1970, closed_rows, unverified = [], 0, 0, []
            for name, version_dir in self._version_dirs(self.root / "tierA"):
                if name != dataset:
                    continue
                for day_dir in sorted(p for p in version_dir.glob("date=*") if p.is_dir()):
                    day = day_dir.name.split("=", 1)[1]
                    parts = sorted(day_dir.glob("part-*.parquet"))
                    if not parts:
                        continue
                    rows = 0
                    for part in parts:
                        if not self.verified_part(part):
                            unverified.append(f"{version_dir.name}/{day_dir.name}/{part.name}")
                        try:
                            rows += _parquet_rows(part)
                        except (OSError, ValueError):
                            pass
                    if day == UNDATED_PARTITION:
                        closed_rows += rows
                    elif policy.valid_day(day):
                        promoted.append(day)
                    else:
                        rows_1970 += rows
            undated_rows = undated_bytes = 0
            staging_days, overdue, unpromoted = [], [], []
            for path in self.staging_files(dataset):
                token = _staging_token(path)
                if token == UNDATED:
                    undated_rows += _count_lines(path)
                    undated_bytes += path.stat().st_size
                elif token == CLOSED:
                    unpromoted.append(CLOSED)
                elif policy.valid_day(token):
                    staging_days.append(token)
                    end = datetime.fromisoformat(token).replace(tzinfo=timezone.utc).timestamp() + 86400
                    if now >= end + settle + 86400:
                        overdue.append(token)
                    elif now >= end:
                        unpromoted.append(token)
                else:
                    rows_1970 += _count_lines(path)
            reasons = []
            if rows_1970:
                reasons.append("INVALID_DAY_ROWS")
            if overdue:
                reasons.append("CLOSED_DAY_UNPROMOTED_PAST_SETTLE_PLUS_24H")
            if unverified:
                reasons.append("PARTITION_UNVERIFIED")
            if backfilled and fields and undated_bytes and dataset not in policy.TIER_A_JOIN_RESOLVED:
                reasons.append("UNDATED_AFTER_BACKFILL")
            level = "RED" if reasons else ("AMBER" if undated_rows or unpromoted else "GREEN")
            overall = level if rank[level] > rank[overall] else overall
            datasets.append({
                "dataset": dataset, "timestamp_field": fields[0] if fields else None,
                "timestamp_fields": list(fields), "promoted_days": len(promoted),
                "last_promoted_day": max(promoted) if promoted else None,
                "undated_rows": undated_rows, "undated_bytes": undated_bytes,
                "closed_undated_rows": closed_rows,
                "oldest_staging_day": min(staging_days) if staging_days else None,
                "unpromoted_closed_days": sorted(set(unpromoted + overdue)), "rows_1970": rows_1970,
                "unverified_partitions": unverified[:20], "level": level, "reasons": reasons,
            })
        return {"schema": HEALTH_SCHEMA, "generated_at": _iso(now), "level": overall, "backfilled": backfilled,
                "settle_hours": self.settle_hours, "datasets": datasets}

    # ----------------------------------------------------- schema / legacy
    def schema_status(self) -> list[dict]:
        """Per dataset/version partition counts and compatibility with the current policy."""
        current = {name: spec[0] for name, spec in policy.tier_a_specs().items()}
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

    def storage_dedupe_report(self, *, settle_sec: float = storage_links.DEFAULT_SETTLE_SEC) -> dict:
        """Hardlink savings and settled files still held as separate physical copies across the three layers."""
        layers = (("tree", self.tree), ("view", self.view), ("canonical", self.data_root))
        logical = physical = duplicate = files = wof_saved = 0
        seen: set = set()
        by_rel: dict[str, list] = {}
        for name, root in layers:
            if not root.is_dir():
                continue
            for directory, _dirs, names in os.walk(root):
                for leaf in names:
                    full = os.path.join(directory, leaf)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    files += 1
                    logical += st.st_size
                    key = (st.st_dev, st.st_ino)
                    if not st.st_ino or key not in seen:
                        physical += st.st_size
                        seen.add(key)
                        if st.st_size >= storage_links.COMPRESS_MIN_BYTES:
                            alloc = storage_links.allocated_bytes(full)
                            if alloc is not None and alloc < st.st_size:
                                wof_saved += st.st_size - alloc
                    rel = os.path.relpath(full, root).replace("\\", "/")
                    if storage_links.linkable(rel) and self.now - st.st_mtime >= settle_sec:
                        by_rel.setdefault(rel, []).append((key, st.st_size))
        for rel, copies in by_rel.items():
            distinct = {key: size for key, size in copies}
            if len(distinct) > 1 and len(set(distinct.values())) == 1:
                duplicate += (len(distinct) - 1) * next(iter(distinct.values()))
        alarms = 0
        alarm_log = self.state_dir / storage_links.DEFAULT_ALARM_LOG.name
        if alarm_log.is_file():
            cutoff = self.now - 86400
            for line in alarm_log.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = _parse_ts(row.get("at"))
                if row.get("code") == "LINK_FALLBACK_COPY" and ts and ts >= cutoff:
                    alarms += 1
        return {"layers_logical_gb": round(logical / GB, 3), "layers_physical_gb": round(physical / GB, 3),
                "hardlink_saved_gb": round((logical - physical) / GB, 3),
                "compression_saved_gb": round(wof_saved / GB, 3),
                "layers_on_disk_gb": round((physical - wof_saved) / GB, 3),
                "duplicate_physical_gb": round(duplicate / GB, 3), "files": files,
                "link_fallback_alarms_24h": alarms, "settle_hours": round(settle_sec / 3600, 2),
                "links_enabled": storage_links.links_enabled()}

    def _safe_dedupe_report(self) -> dict:
        try:
            return self.storage_dedupe_report()
        except OSError as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def compress_roots(self) -> list[Path]:
        roots = [self.tree, self.view, self.data_root,
                 analysis_archive.archive_root(self.cfg.get("archive_root"))]
        return roots + [Path(extra) for extra in self.cfg.get("historical_roots") or ()]

    def _safe_compress(self, *, dry: bool) -> dict:
        try:
            return storage_links.compress_settled(
                self.compress_roots(), now=self.now, dry_run=dry,
                max_bytes=int(self.cfg.get("wof_max_bytes_per_run", 4 * GB)))
        except OSError as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def session_archive_root(self) -> Path:
        return self.data_root / "analyzer" / "research_session_archives"

    def artifact_candidates(self) -> list[dict]:
        """Bounded generations for laptop derived artifacts.

        Session archives: the newest ``session_archive_keep`` generations and
        anything younger than ``session_archive_min_age_days`` stay. Evidence
        objects (``_evidence_objects``) and staging dirs are never candidates;
        a deleted session only drops its hardlink to an evidence object.
        Logs: rotated log files older than ``log_retain_days``. Junctions are
        never followed.
        """
        out = []
        root = self.session_archive_root()
        if root.is_dir() and not storage_links._is_reparse(str(root)):
            sessions = sorted((p for p in root.iterdir() if p.is_dir() and p.name.startswith("session_")
                               and not storage_links._is_reparse(str(p))),
                              key=lambda p: p.stat().st_mtime, reverse=True)
            keep = int(self.cfg.get("session_archive_keep", 10))
            min_age = float(self.cfg.get("session_archive_min_age_days", 3.0)) * 86400
            for path in sessions[keep:]:
                mtime = path.stat().st_mtime
                if self.now - mtime < min_age:
                    continue
                out.append({"kind": "SESSION_ARCHIVE", "path": path, "mtime": mtime,
                            "bytes": tree_bytes(path)})
        retain = float(self.cfg.get("log_retain_days", 14.0)) * 86400
        for log_root in self.cfg.get("log_roots") or ():
            log_root = Path(log_root)
            if not log_root.is_dir() or storage_links._is_reparse(str(log_root)):
                continue
            for path in sorted(log_root.iterdir()):
                if not path.is_file() or not path.name.lower().endswith(LOG_SUFFIXES):
                    continue
                st = path.stat()
                if self.now - st.st_mtime >= retain:
                    out.append({"kind": "LOG", "path": path, "mtime": st.st_mtime, "bytes": st.st_size})
        return out

    def prune_artifacts(self, *, mode: str) -> dict:
        ledger = self.state_dir / "artifact-prune-ledger.jsonl"
        result = {"candidates": 0, "deleted": 0, "bytes": 0, "errors": 0}
        for item in self.artifact_candidates():
            result["candidates"] += 1
            result["bytes"] += item["bytes"]
            if mode != MODE_ENFORCE:
                continue
            refuse_onedrive(item["path"])
            try:
                if item["kind"] == "SESSION_ARCHIVE":
                    shutil.rmtree(item["path"])
                else:
                    _unlink(item["path"])
            except OSError:
                result["errors"] += 1
                continue
            result["deleted"] += 1
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"schema": "laptop_artifact_prune_v1", "logged_at": _iso(_utc_now()),
                                         "kind": item["kind"], "path": str(item["path"]),
                                         "bytes": item["bytes"], "mtime": _iso(item["mtime"])}) + "\n")
        return result

    def _safe_prune_artifacts(self, *, mode: str) -> dict:
        try:
            return self.prune_artifacts(mode=mode)
        except OSError as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def sizes(self) -> dict[str, int]:
        # Roots are measured in order with one shared inode set, so the
        # promotion view and canonical store only count bytes they do not
        # share with the mirror tree and the sum is the physical footprint.
        seen: set = set()
        return {name: tree_bytes(path, seen) for name, path in self.roots().items()}

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
        released: set = set()
        for root_name, copy in self._copies(item["relpath"]):
            if not copy.is_file():
                continue
            st = copy.stat()
            size = st.st_size
            shared = bool(st.st_ino) and (st.st_dev, st.st_ino) in released
            if root_name != "tree" and not shared and (size != item["bytes"] or _sha256_file(copy) != digest):
                result["copies"].append({"root": root_name, "kept": "CONTENT_DIFFERS"})
                continue
            result["copies"].append({"root": root_name, "bytes": size, "hardlink_of_previous": shared})
            if not shared:
                result["bytes"] += size
            if st.st_ino:
                released.add((st.st_dev, st.st_ino))
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
        compactor = Compactor(self.compact_root, settle_hours=float(self.cfg["settle_hours"]), now=self.now)
        plan: list[dict] = []
        budget = int(self.cfg["max_delete_bytes_per_run"])
        reclaimed = 0
        # The Tier A backfill takes the same lock, so compaction and deletion never race it.
        lock = self.lock_factory(self.shadow / ".puller" / "run.lock") \
            if self.lock_factory is not None and not dry else None
        try:
            compaction = {"ingest": compactor.ingest(self.tree, dry_run=dry),
                          "finalize": compactor.finalize(now=self.now, dry_run=dry),
                          "legacy_moves": compactor.quarantine_incompatible(dry_run=dry)}
        except BaseException:
            if lock is not None:
                lock.release()
            raise
        if gates["allowed"]:
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
            if lock is not None:
                lock.release()
            cap = {"status": "GATES_DENIED", "deleted": []}
        after = self.sizes() if not dry else before
        total_after = sum(after.values()) if not dry else total_before - reclaimed
        cap_bytes = int(self.cfg["cap_bytes"])
        usage = total_after / cap_bytes if cap_bytes else 0.0
        level = "RED" if usage >= float(self.cfg["red_fraction"]) else (
            "AMBER" if usage >= float(self.cfg["amber_fraction"]) else "GREEN")
        custody = self._post_custody(gates, dry=dry)
        wof = self._safe_compress(dry=dry)
        artifacts = self._safe_prune_artifacts(mode=mode)
        status = {
            "schema": SCHEMA, "policy_version": policy.POLICY_VERSION, "mode": mode,
            "started_at": _iso(started), "finished_at": _iso(_utc_now()),
            "cap_bytes": cap_bytes, "bytes_before": total_before, "bytes_after": total_after,
            "sizes_basis": "physical_v1", "storage_dedupe": self._safe_dedupe_report(), "wof_compaction": wof,
            "laptop_artifacts": artifacts,
            "usage_fraction": round(usage, 4), "level": level, "sizes_before": before, "sizes_after": after,
            "gates": _gate_summary(gates), "deny_reasons": gates["deny_reasons"],
            "candidates": len(plan),
            "deleted_files": sum(1 for row in plan if row["bytes"]) if not dry else 0,
            "reclaimed_bytes": reclaimed if not dry else 0, "would_reclaim_bytes": reclaimed if dry else 0,
            "by_tier": _by_tier(plan), "kept": [row for row in plan if not row["bytes"]][:50],
            "cap": {k: v for k, v in cap.items() if k != "deleted"},
            "compaction": compaction, "tier_a_schema": compactor.schema_status(),
            "tier_a": compactor.health(now=self.now, backfilled=_backfill_receipt_present(self.state_dir)),
            "custody_post": custody, "ledger_rows": len(self.ledger.rows()),
            "archive": analysis_archive.archive_status(self.cfg.get("archive_root")),
        }
        target = "dry-run-latest.json" if dry else "status.json"
        _atomic_json(self.state_dir / target, {**status, "plan": [_plan_row(row) for row in plan]})
        if dry:
            _atomic_json(self.state_dir / "status-dry-run.json", status)
        _atomic_json(self.state_dir / "last-run.json", {k: status[k] for k in (
            "mode", "finished_at", "level", "bytes_after", "cap_bytes", "usage_fraction", "deny_reasons",
            "reclaimed_bytes", "would_reclaim_bytes", "ledger_rows")} | {"tier_a_level": status["tier_a"]["level"]})
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


def retrying_lock_factory(wait_sec: float, *, sleep=time.sleep, clock=time.monotonic, lock_class=None):
    """Acquire the puller's shadow-root lock, retrying for ``wait_sec``.

    The pull loop holds this lock for a few seconds per pull; a single
    non-blocking attempt loses that race on most cycles.
    """
    from research_segment_puller import PullerError, _RunLock

    lock_class = lock_class or _RunLock

    def factory(path):
        deadline = clock() + max(wait_sec, 0.0)
        while True:
            try:
                return lock_class(path)
            except PullerError as exc:
                if clock() >= deadline:
                    raise RuntimeError(f"shadow-root lock busy for {wait_sec:.0f}s; retry next cycle") from exc
                sleep(1.0)

    return factory


class _NamedMutex:
    def __init__(self, kernel32, handle, name: str, abandoned: bool):
        self._kernel32, self._handle, self.name, self.abandoned = kernel32, handle, name, abandoned

    def release(self) -> None:
        if self._handle:
            self._kernel32.ReleaseMutex(self._handle)
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def cycle_mutex_factory(wait_sec: float, *, sleep=time.sleep, clock=time.monotonic):
    """Acquire the ``LaptopSegmentAnalyzerCycle`` named mutex the analyzer cycle holds.

    The cycle runs retention (and therefore staging appends) under this mutex,
    not under the shadow-root lock, so an out-of-cycle writer must hold both.
    """
    name = (os.environ.get("DOXXED_LAPTOP_CHAIN_MUTEX_PREFIX") or "Doxxed") + "LaptopSegmentAnalyzerCycle"

    def factory():
        if os.name != "nt":
            return None
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = None
        for namespace in ("Global", "Local"):
            handle = kernel32.CreateMutexW(None, False, f"{namespace}\\{name}")
            if handle:
                break
        if not handle:
            raise RuntimeError(f"MUTEX_UNAVAILABLE: {name}")
        deadline = clock() + max(wait_sec, 0.0)
        while True:
            result = kernel32.WaitForSingleObject(handle, 0)
            if result in (0x0, 0x80):
                return _NamedMutex(kernel32, handle, name, abandoned=result == 0x80)
            if clock() >= deadline:
                kernel32.CloseHandle(handle)
                raise RuntimeError(f"analyzer cycle mutex {name} busy for {wait_sec:.0f}s; retry later")
            sleep(2.0)

    return factory


DEFAULT_DATA_ROOT = r"C:\DoxxedCrypto\v2c\services\btc-conservative-agent\canonical-research-data"
BACKFILL_DIR = "tier-a-backfill"
ARCHIVE_DATASETS = frozenset({"bitfinex_l1_tape_1s"})
CSV_PARSER_VERSION = "csv_quote_balanced_records_v1"


def _backfill_receipt_present(state_dir: Path) -> bool:
    return _read_json(Path(state_dir) / BACKFILL_DIR / "latest.json").get("status") == "OK"


def _dataset_inventory(root: Path) -> dict:
    import pyarrow.parquet as pq

    out: dict[str, dict] = {}
    probe = Compactor(root, settle_hours=0.0)
    for path in probe.staging_files():
        dataset = path.parent.parent.name
        cell = out.setdefault(dataset, _empty_inventory())
        rows, size = _count_lines(path), path.stat().st_size
        cell["staging_rows"] += rows
        cell["staging_bytes"] += size
        if _staging_token(path) == UNDATED:
            cell["undated_rows"] += rows
            cell["undated_bytes"] += size
    for dataset, version_dir in probe._version_dirs(root / "tierA"):
        cell = out.setdefault(dataset, _empty_inventory())
        for part in version_dir.glob("date=*/part-*.parquet"):
            day = part.parent.name.split("=", 1)[1]
            cell["partitions"] += 1
            cell["partition_bytes"] += part.stat().st_size
            rows = _parquet_rows(part)
            cell["partition_rows"] += rows
            if day != UNDATED_PARTITION and not policy.valid_day(day):
                cell["rows_1970"] += rows
            elif day != UNDATED_PARTITION:
                cell["days"].add(day)
    for cell in out.values():
        cell["days"] = sorted(cell["days"])
    return dict(sorted(out.items()))


def _empty_inventory() -> dict:
    return {"staging_rows": 0, "staging_bytes": 0, "undated_rows": 0, "undated_bytes": 0, "partitions": 0,
            "partition_rows": 0, "partition_bytes": 0, "rows_1970": 0, "days": set()}


def _rebuild_csv(compactor: Compactor, tree: Path, superseded: Path, *, force: bool = False) -> list[dict]:
    """Re-ingest CSV datasets from raw with record-aware parsing; old artifacts are moved, not deleted.

    Line-split CSV ingestion broke quoted multi-line fields into fragment rows,
    so existing CSV staging/partitions are superseded only when the raw files
    still cover every day those artifacts held.
    """
    out = []
    sources = _tier_a_sources(tree)
    rebuilt = compactor.state.setdefault("csv_rebuilt", {})
    for base, (dataset, version, fields) in policy.TIER_A_DATASETS.items():
        if not base.endswith(".csv"):
            continue
        if not force and rebuilt.get(dataset, {}).get("parser") == CSV_PARSER_VERSION:
            out.append({"dataset": dataset, "status": "ALREADY_REBUILT", **rebuilt[dataset]})
            continue
        raw = [path for rel, path in sorted(sources.items()) if policy.base_of(rel) == base and "/" not in rel]
        if not raw:
            out.append({"dataset": dataset, "status": "SKIPPED_NO_RAW"})
            continue
        raw_days: set = set()
        counts = {"rows": 0, "undated": 0}

        def sink(token: str, lines: list[str]) -> None:
            counts["rows"] += len(lines)
            if token == UNDATED:
                counts["undated"] += len(lines)
            else:
                raw_days.add(token)

        for path in raw:
            compactor._stream_new(path, 0, None, dataset, fields, sink)
        old_days = set(_dataset_inventory_days(compactor, dataset))
        missing = sorted(old_days - raw_days)
        if missing:
            out.append({"dataset": dataset, "status": "SKIPPED_RAW_INCOMPLETE", "missing_days": missing})
            continue
        moved = []
        for kind in ("tierA", "staging"):
            source = compactor.root / kind / dataset
            if source.is_dir():
                target = superseded / kind / dataset
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                moved.append(str(target))
        for key in [k for k, v in compactor.state["sources"].items()
                    if k.startswith(f"{dataset}:") or v.get("dataset") == dataset]:
            del compactor.state["sources"][key]
        rebuilt[dataset] = {"parser": CSV_PARSER_VERSION, "rebuilt_at": _iso(_utc_now())}
        out.append({"dataset": dataset, "status": "REBUILT_FROM_RAW", "raw_files": [p.name for p in raw],
                    "raw_rows": counts["rows"], "raw_undated_rows": counts["undated"],
                    "raw_days": len(raw_days), "superseded": moved})
    compactor.save()
    return out


def _dataset_inventory_days(compactor: Compactor, dataset: str) -> list[str]:
    days = set()
    for name, version_dir in compactor._version_dirs(compactor.root / "tierA"):
        if name == dataset:
            days.update(p.name.split("=", 1)[1] for p in version_dir.glob("date=*")
                        if policy.valid_day(p.name.split("=", 1)[1]))
    days.update(_staging_token(p) for p in compactor.staging_files(dataset) if policy.valid_day(_staging_token(p)))
    return sorted(days)


def _migrate_invalid_partitions(compactor: Compactor, superseded: Path) -> list[dict]:
    """Move rows of invalid-day partitions (e.g. date=1970-01-01) back to undated staging, verified."""
    import pyarrow.parquet as pq

    out = []
    for dataset, version_dir in list(compactor._version_dirs(compactor.root / "tierA")):
        for day_dir in sorted(p for p in version_dir.glob("date=*") if p.is_dir()):
            day = day_dir.name.split("=", 1)[1]
            if day == UNDATED_PARTITION or policy.valid_day(day):
                continue
            rows = []
            for part in sorted(day_dir.glob("part-*.parquet")):
                rows.extend(_parquet_column(part, "row"))
            tag = hashlib.sha256("\n".join(r or "" for r in rows).encode("utf-8", "surrogatepass")).hexdigest()[:16]
            target = compactor.staging / dataset / version_dir.name / f"{UNDATED}__mig-{tag}.seg"
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", errors="surrogateescape") as handle:
                for row in rows:
                    handle.write(json.dumps({"ts": None, "row": row}, separators=(",", ":")) + "\n")
            if _count_lines(target) != len(rows):
                _unlink(target)
                raise TierAVerificationError(f"invalid-day migration count mismatch: {day_dir}")
            moved = superseded / "tierA" / dataset / version_dir.name / day_dir.name
            moved.parent.mkdir(parents=True, exist_ok=True)
            os.replace(day_dir, moved)
            out.append({"dataset": dataset, "day": day, "rows": len(rows), "staging": target.name,
                        "superseded": str(moved)})
    return out


def _ingest_archive(compactor: Compactor, root: Path) -> list[dict]:
    """Stage Tier A tape files of a manual archive (read-only) once per content hash."""
    tree = root / "tree" if (root / "tree").is_dir() else root
    done = compactor.state.setdefault("archives", {})
    out = []
    if not tree.is_dir():
        return [{"root": str(root), "status": "MISSING"}]
    for path in sorted(p for p in tree.iterdir() if p.is_file()):
        spec = policy.tier_a_dataset(path.name)
        if spec is None or spec[0] not in ARCHIVE_DATASETS:
            continue
        digest = _sha256_file(path)
        if digest in done:
            out.append({"file": str(path), "sha256": digest, "status": "ALREADY_INGESTED"})
            continue
        dataset, version, fields = spec
        result = {"rows": 0, "undated_rows": 0}
        consumed, _header, days, undated = compactor._append_stream(
            path, 0, None, dataset, version, fields, result, tag=f"ar-{digest[:16]}")
        done[digest] = {"file": str(path), "dataset": dataset, "rows": result["rows"], "undated_rows": undated,
                        "days": sorted(days), "bytes": consumed, "ingested_at": _iso(_utc_now())}
        compactor.save()
        out.append({"file": str(path), "sha256": digest, "status": "STAGED", **done[digest]})
    return out


def tier_a_backfill(cfg: dict, *, dry_run: bool, data_root=None, archive_roots=(), rebuild_csv: bool = True,
                    force_csv_rebuild: bool = False, now: Optional[float] = None, lock_factory=None,
                    mutex_factory=None) -> dict:
    """One-shot, idempotent Tier A repair: re-date all staging and promote verified Parquet.

    Holds the analyzer-cycle mutex and the shadow-root lock (fails closed when
    either is busy). A dry run performs the identical pipeline on a scratch copy
    of the compact root, verifies every partition, then deletes the scratch.
    Staging is removed only after its rows re-read from Parquet with the same
    count and content sha256. Raw mirror and archive files are only read.
    """
    import shutil

    now = float(now if now is not None else _utc_now())
    compact_root = refuse_onedrive(cfg["compact_root"])
    state_dir = refuse_onedrive(cfg["state_dir"])
    shadow = refuse_onedrive(cfg["shadow_root"])
    tree = shadow / "tree"
    archives = [refuse_onedrive(p) for p in archive_roots]
    mode = MODE_DRY_RUN if dry_run else MODE_ENFORCE
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt: dict = {"schema": BACKFILL_SCHEMA, "mode": mode, "started_at": _iso(_utc_now()),
                     "compact_root": str(compact_root), "archive_roots": [str(p) for p in archives],
                     "rebuild_csv": rebuild_csv, "policy_version": policy.POLICY_VERSION, "status": "STARTED"}
    scratch = compact_root / ".tier-a-backfill-dryrun"
    mutex = lock = None
    try:
        mutex = mutex_factory() if mutex_factory is not None else None
        lock = lock_factory(shadow / ".puller" / "run.lock") if lock_factory is not None else None
        sizes = Retention(cfg, Path(data_root or DEFAULT_DATA_ROOT)).sizes()
        managed = sum(sizes.values())
        staging_bytes = tree_bytes(compact_root / "staging")
        archive_bytes = sum(p.stat().st_size for root in archives
                            for p in ((root / "tree") if (root / "tree").is_dir() else root).glob("*")
                            if p.is_file() and (policy.tier_a_dataset(p.name) or ("",))[0] in ARCHIVE_DATASETS)
        projected = managed + staging_bytes * (2 if dry_run else 1) + archive_bytes
        cap = int(cfg["cap_bytes"])
        limit = int(cap * float(cfg["red_fraction"]))
        free = shutil.disk_usage(compact_root if compact_root.exists() else compact_root.parent).free
        receipt["disk"] = {"managed_bytes_before": managed, "sizes_before": sizes, "staging_bytes": staging_bytes,
                           "archive_tape_bytes": archive_bytes, "projected_peak_bytes": projected,
                           "cap_bytes": cap, "refuse_above_bytes": limit, "volume_free_bytes": free}
        if projected > limit or projected - managed > free - 5 * GB:
            receipt["status"] = "REFUSED_DISK"
            raise RuntimeError(f"tier A backfill refused: projected {projected} bytes exceeds {limit} "
                               f"or volume free space {free}")
        work_root = compact_root
        if dry_run:
            if scratch.exists():
                shutil.rmtree(scratch)
            scratch.mkdir(parents=True)
            for name in ("staging", "tierA"):
                if (compact_root / name).is_dir():
                    shutil.copytree(compact_root / name, scratch / name)
            for name in ("compactor-state.json", JOIN_INDEX_NAME):
                if (compact_root / name).is_file():
                    shutil.copy2(compact_root / name, scratch / name)
            work_root = scratch
        before = _dataset_inventory(work_root)
        compactor = Compactor(work_root, settle_hours=float(cfg["settle_hours"]), now=now)
        superseded = work_root / "superseded" / f"tier-a-backfill-{stamp}"
        steps: dict = {}
        steps["csv_rebuild"] = _rebuild_csv(compactor, tree, superseded, force=force_csv_rebuild) \
            if rebuild_csv else []
        steps["invalid_partitions"] = _migrate_invalid_partitions(compactor, superseded)
        steps["ingest"] = compactor.ingest(tree, dry_run=False)
        steps["archive"] = [row for root in archives for row in _ingest_archive(compactor, root)]
        steps["attributed_days"] = compactor.attribute_days(tree)
        compactor.save()
        steps["finalize"] = compactor.finalize(now=now, dry_run=False, close_unresolved=True)
        after = _dataset_inventory(work_root)
        health = compactor.health(now=now, backfilled=True)
        finalize = steps["finalize"]
        promoted = [p for p in finalize["partitions"] if isinstance(p, dict)]
        datasets = {}
        for name in sorted(set(before) | set(after)):
            b, a = before.get(name, _empty_inventory()), after.get(name, _empty_inventory())
            b = {k: v for k, v in b.items() if k != "days"} | {"days": len(b.get("days") or [])}
            a = {k: v for k, v in a.items() if k != "days"} | {"days": len(a.get("days") or [])}
            datasets[name] = {"before": b, "after": a,
                              "partitions_created": [p for p in promoted if p["partition"].startswith(f"{name}/")]}
        receipt.update({
            "steps": {k: v for k, v in steps.items() if k != "finalize"},
            "rebucketed": finalize["rebucketed"], "errors": finalize["errors"],
            "partitions_created": len([p for p in promoted if p.get("status") == "PROMOTED"]),
            "partition_rows_written": sum(int(p.get("rows") or 0) for p in promoted
                                          if p.get("status") == "PROMOTED"),
            "duplicates_dropped": sum(int(p.get("duplicates_dropped") or 0) for p in promoted),
            "hashes_verified": len([p for p in promoted if p.get("content_sha256")]),
            "closed_undated_rows": finalize["closed_rows"],
            "staging_bytes_before": sum(v["before"]["staging_bytes"] for v in datasets.values()),
            "staging_bytes_after": sum(v["after"]["staging_bytes"] for v in datasets.values()),
            "datasets": datasets, "tier_a": health,
        })
        receipt["status"] = "OK" if not finalize["errors"] else "VERIFICATION_FAILED"
    except (RuntimeError, OSError, TierAVerificationError) as exc:
        if receipt["status"] in ("STARTED",):
            receipt["status"] = "FAILED"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if dry_run and scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
        if lock is not None:
            lock.release()
        if mutex is not None:
            mutex.release()
    if not dry_run:
        receipt["disk_after"] = {"compact_root_bytes": tree_bytes(compact_root)}
    receipt["finished_at"] = _iso(_utc_now())
    out_dir = state_dir / BACKFILL_DIR
    _atomic_json(out_dir / f"receipt-{stamp}-{mode}.json", receipt)
    _atomic_json(out_dir / ("latest.json" if not dry_run else "dry-run-latest.json"), receipt)
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", help="canonical-research-data the analyzer reads")
    parser.add_argument("--tier-a-backfill", action="store_true",
                        help="one-shot: re-date all Tier A staging and promote verified Parquet, then exit")
    parser.add_argument("--dry-run", action="store_true", help="with --tier-a-backfill: verify on a scratch copy")
    parser.add_argument("--archive", action="append", default=[],
                        help="with --tier-a-backfill: read-only archive root whose 1 s tape is ingested")
    parser.add_argument("--no-rebuild-csv", action="store_true",
                        help="with --tier-a-backfill: keep existing CSV staging/partitions")
    parser.add_argument("--force-csv-rebuild", action="store_true",
                        help="with --tier-a-backfill: rebuild CSV datasets from raw even if already rebuilt")
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
    lock_factory = retrying_lock_factory(
        float(os.environ.get("BOT_DATA_RETENTION_LOCK_WAIT_SEC") or LOCK_WAIT_SEC))
    if args.tier_a_backfill:
        receipt = tier_a_backfill(
            cfg, dry_run=args.dry_run, data_root=args.data_root, archive_roots=args.archive,
            rebuild_csv=not args.no_rebuild_csv, force_csv_rebuild=args.force_csv_rebuild, lock_factory=lock_factory,
            mutex_factory=cycle_mutex_factory(float(os.environ.get("BOT_DATA_RETENTION_CYCLE_WAIT_SEC") or 900)))
        print(json.dumps({k: receipt.get(k) for k in (
            "status", "mode", "error", "partitions_created", "partition_rows_written", "hashes_verified",
            "duplicates_dropped", "closed_undated_rows", "staging_bytes_before", "staging_bytes_after")}
            | {"tier_a_level": (receipt.get("tier_a") or {}).get("level")}, sort_keys=True))
        return 0 if receipt["status"] == "OK" else 3
    if not args.data_root:
        parser.error("--data-root is required")

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
