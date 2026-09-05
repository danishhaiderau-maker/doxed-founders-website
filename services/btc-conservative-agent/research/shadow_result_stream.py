"""Bounded, generation-bound shadow transport. Verification precedes consumption.

The gzip is immutable output, not an ACK or a live-qualification receipt. SQLite
is an owned temporary working index; it never changes the canonical evidence.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from research.policy_evidence_schema import canonical_json

SCHEMA = "shadow_result_stream_v1"
RANGE = "shadow_unknown_candidate_range_v1"
MAX_LINE = 4 * 1024 * 1024
MAX_BYTES = 2 * 1024 * 1024 * 1024
# Discovery simultaneously owns verified, adapted, and grouped indexes.
DEFAULT_INDEX_BYTES = MAX_BYTES // 3
MAX_INDEX_KEY_BYTES = 64 * 1024
GROUP_FIRST_SEEN_SQL = ("select key from rows r where seq="
                       "(select min(seq) from rows s where s.key is r.key) order by seq")
GROUP_SORTED_SQL = "select distinct key from rows order by key"
GROUP_ROWS_SQL = "select payload from rows where key is ? order by seq"
GENERATION_FIELDS = ("manifest_entry_hash", "epoch_id", "source_revision", "deployed_revision",
                     "tile_config_signature", "analyzer_revision", "evaluator_version", "generation_key")


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _report_digest(report):
    return digest({k: v for k, v in report.items() if k != "result_stream"})


def _generation(generation):
    if not isinstance(generation, dict) or any(not isinstance(generation.get(k), str)
            or not generation[k].strip() for k in GENERATION_FIELDS):
        raise ValueError("SHADOW_STREAM_GENERATION_INVALID")
    return dict(generation)


class DiskRows:
    """Repeatable disk-backed rows; limit failure is explicit, never sampling."""
    def __init__(self, *, max_bytes=DEFAULT_INDEX_BYTES, temp_root=None):
        self.temp = tempfile.TemporaryDirectory(prefix="btc-shadow-index-", dir=temp_root)
        self.path = Path(self.temp.name) / "rows.sqlite"
        self.db = sqlite3.connect(str(self.path))
        try:
            self.max_bytes = int(max_bytes)
            self.db.execute("pragma page_size=4096")
            page_size = self.db.execute("pragma page_size").fetchone()[0]
            pages = self.max_bytes // page_size
            if pages < 4:
                raise ValueError("SHADOW_STREAM_INDEX_BUDGET_EXCEEDED")
            if self.db.execute(f"pragma max_page_count={pages}").fetchone()[0] != pages:
                raise ValueError("SHADOW_STREAM_INDEX_PAGE_LIMIT_UNAVAILABLE")
            # Disposable derived index only: never reused after failure/crash.
            # No rollback/WAL/sort files may bypass the main-file page ceiling.
            if self.db.execute("pragma journal_mode=OFF").fetchone()[0].lower() != "off":
                raise ValueError("SHADOW_STREAM_INDEX_JOURNAL_LIMIT_UNAVAILABLE")
            self.db.execute("pragma temp_store=MEMORY")
            self.db.execute("pragma cache_size=-2048")
            self.db.execute("create table rows (seq integer primary key, key text, prefix text, payload text)")
            self.db.execute("create index row_key on rows(key)")
            self.db.execute("create index row_prefix on rows(prefix)")
            self.size = self.count = 0
        except Exception:
            self.close()
            raise

    def append(self, row, key=None, prefix=None):
        payload = canonical_json(row)
        if (len(payload.encode()) > MAX_LINE or any(
                len(str(item or "").encode()) > MAX_INDEX_KEY_BYTES for item in (key, prefix))):
            raise ValueError("SHADOW_STREAM_INDEX_ROW_BUDGET_EXCEEDED")
        self.size += len(payload.encode()) + len(str(key or "")) + len(str(prefix or "")) + 256
        if self.size > self.max_bytes // 2:
            raise ValueError("SHADOW_STREAM_INDEX_BUDGET_EXCEEDED")
        try:
            self.db.execute("insert into rows(key,prefix,payload) values(?,?,?)",
                            (key, prefix, payload))
        except sqlite3.DatabaseError as exc:
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                raise ValueError("SHADOW_STREAM_INDEX_BUDGET_EXCEEDED") from exc
            raise
        self.count += 1

    def __iter__(self):
        for (payload,) in self.db.execute("select payload from rows order by seq"):
            yield json.loads(payload)

    def __len__(self):
        return self.count

    def groups(self, *, max_group_bytes=8 * 1024 * 1024, max_group_rows=10000, insertion_order=False):
        # GROUP BY key ORDER BY min(seq) creates an unbounded temporary sort.
        # This indexed first-seen lookup scans rowids without materializing it.
        query = GROUP_FIRST_SEEN_SQL if insertion_order else GROUP_SORTED_SQL
        for statement, bindings in ((query, ()), (GROUP_ROWS_SQL, (None,))):
            plans = self.db.execute("explain query plan " + statement, bindings)
            if any("TEMP B-TREE" in str(plan[-1]).upper() for plan in plans):
                raise ValueError("SHADOW_STREAM_UNBOUNDED_QUERY_PLAN")
        for (key,) in self.db.execute(query):
            rows, size = [], 0
            for (payload,) in self.db.execute(GROUP_ROWS_SQL, (key,)):
                size += len(payload.encode())
                if size > max_group_bytes or len(rows) >= max_group_rows:
                    raise ValueError("SHADOW_STREAM_GROUP_BUDGET_EXCEEDED")
                rows.append(json.loads(payload))
            yield key, rows

    def close(self):
        self.db.close()
        self.temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ShadowResultStreamWriter:
    def __init__(self, root, relative_path, generation, *, max_bytes=MAX_BYTES):
        self.root = Path(root).resolve()
        self.path = (self.root / relative_path).resolve()
        if not self.path.is_relative_to(self.root) or self.path == self.root:
            raise ValueError("SHADOW_STREAM_PATH_INVALID")
        self.generation = _generation(dict(generation))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.raw = self.path.open("xb")
        self.gz = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, mtime=0)
        self.sha = hashlib.sha256()
        self.rows = self.size = 0
        self.max_bytes = max_bytes
        self.closed = False

    def __call__(self, row):
        payload = (canonical_json(row) + "\n").encode()
        if self.closed or len(payload) > MAX_LINE or self.size + len(payload) > self.max_bytes:
            raise ValueError("SHADOW_STREAM_WRITE_BUDGET_OR_STATE_INVALID")
        self.gz.write(payload)
        self.sha.update(payload)
        self.rows += 1
        self.size += len(payload)

    def finalize(self, report):
        if self.closed:
            raise ValueError("SHADOW_STREAM_ALREADY_CLOSED")
        self.gz.close()
        self.raw.close()
        self.closed = True
        if report.get("generation") != self.generation:
            raise ValueError("SHADOW_STREAM_GENERATION_MISMATCH")
        sha = hashlib.sha256()
        with self.path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(chunk)
        receipt = {"schema": SCHEMA, "complete": True, "generation": self.generation,
                   "relative_path": self.path.relative_to(self.root).as_posix(),
                   "artifact_sha256": sha.hexdigest(), "compressed_bytes": self.path.stat().st_size,
                   "uncompressed_sha256": self.sha.hexdigest(), "uncompressed_bytes": self.size,
                   "record_count": self.rows, "report_sha256": _report_digest(report),
                   "candidate_artifact_sha256": report.get("candidate_artifact_sha256"),
                   "candidate_replay_count": report.get("candidate_replay_count"),
                   "complete_replay_count": report.get("complete_replay_count"),
                   "unknown_replay_count": report.get("unknown_replay_count"),
                   "range_semantics": "ALL_CANDIDATES_FOR_ONE_EPISODE_OPPORTUNITY_BASELINE",
                   "live_qualification": False}
        receipt["receipt_sha256"] = digest(receipt)
        return receipt

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if not self.closed:
            self.gz.close()
            self.raw.close()
            self.closed = True


def verify_result_stream(root, report, generation, *, max_bytes=MAX_BYTES,
                         index_max_bytes=DEFAULT_INDEX_BYTES):
    """Return a verified disk snapshot or raise; no rows escape before EOF/hash/count checks."""
    receipt = report.get("result_stream")
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA or receipt.get("complete") is not True:
        raise ValueError("SHADOW_STREAM_RECEIPT_MISSING_OR_INCOMPLETE")
    if receipt.get("receipt_sha256") != digest({k: v for k, v in receipt.items() if k != "receipt_sha256"}):
        raise ValueError("SHADOW_STREAM_RECEIPT_SHA_MISMATCH")
    if receipt.get("generation") != _generation(dict(generation)) or report.get("generation") != generation:
        raise ValueError("SHADOW_STREAM_GENERATION_MISMATCH")
    if receipt.get("report_sha256") != _report_digest(report):
        raise ValueError("SHADOW_STREAM_REPORT_BINDING_MISMATCH")
    if (any(type(receipt.get(k)) is not int or receipt[k] < 0 for k in (
            "record_count", "compressed_bytes", "uncompressed_bytes", "candidate_replay_count",
            "complete_replay_count", "unknown_replay_count"))
            or receipt.get("candidate_artifact_sha256") != report.get("candidate_artifact_sha256")
            or receipt.get("range_semantics") != "ALL_CANDIDATES_FOR_ONE_EPISODE_OPPORTUNITY_BASELINE"):
        raise ValueError("SHADOW_STREAM_RECEIPT_FIELDS_INVALID")
    root = Path(root).resolve()
    path = (root / str(receipt.get("relative_path") or "")).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("SHADOW_STREAM_FILE_MISSING_OR_UNSAFE")
    if path.stat().st_size != receipt.get("compressed_bytes") or path.stat().st_size > max_bytes:
        raise ValueError("SHADOW_STREAM_COMPRESSED_SIZE_INVALID")
    index = DiskRows(max_bytes=min(max_bytes, index_max_bytes))
    counts, reasons = Counter(), Counter()
    range_count = 0
    sha, total, records = hashlib.sha256(), 0, 0
    try:
        index.db.execute("create table ranges(prefix text primary key)")
        # Copy the verified decompressed records into the private index. A second
        # compressed hash read fences any source mutation during decompression.
        def file_sha():
            value = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    value.update(chunk)
            return value.hexdigest()
        if file_sha() != receipt.get("artifact_sha256"):
            raise ValueError("SHADOW_STREAM_ARTIFACT_SHA_MISMATCH")
        with gzip.open(path, "rb") as handle:
            while True:
                line = handle.readline(MAX_LINE + 1)
                if not line:
                    break
                total += len(line)
                if len(line) > MAX_LINE or total > max_bytes or not line.endswith(b"\n"):
                    raise ValueError("SHADOW_STREAM_LINE_OR_BYTE_LIMIT")
                sha.update(line)
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("SHADOW_STREAM_ROW_INVALID")
                prefix = tuple(row.get(k) for k in ("episode_id", "opportunity_id", "baseline_id"))
                if any(not isinstance(k, str) or not k for k in prefix):
                    raise ValueError("SHADOW_STREAM_ROW_IDENTITY_MISSING")
                encoded_prefix = canonical_json(prefix)
                if row.get("schema") == RANGE:
                    amount = row.get("candidate_count")
                    if (type(amount) is not int or amount <= 0 or amount != report.get("candidate_policy_count")
                            or row.get("status") != "UNKNOWN" or not row.get("blockers")
                            or row.get("candidate_artifact_sha256") != receipt.get("candidate_artifact_sha256")
                            or not receipt.get("candidate_artifact_sha256")
                            or index.db.execute("select 1 from ranges where prefix=?", (encoded_prefix,)).fetchone()
                            or index.db.execute("select 1 from rows where prefix=? limit 1", (encoded_prefix,)).fetchone()):
                        raise ValueError("SHADOW_STREAM_RANGE_INVALID_OR_OVERLAP")
                    index.size += len(encoded_prefix.encode()) * 2 + 256
                    if index.size > index.max_bytes // 2:
                        raise ValueError("SHADOW_STREAM_INDEX_BUDGET_EXCEEDED")
                    index.db.execute("insert into ranges values(?)", (encoded_prefix,))
                    range_count += 1
                else:
                    amount = 1
                    if (index.db.execute("select 1 from ranges where prefix=?", (encoded_prefix,)).fetchone()
                            or row.get("status") not in {"COMPLETE", "UNKNOWN"}):
                        raise ValueError("SHADOW_STREAM_ROW_STATUS_OR_RANGE_OVERLAP")
                    composite = str(row.get("composite_policy_signature") or row.get("policy_signature") or "")
                    index.append(row, canonical_json((*prefix, composite)), encoded_prefix)
                state = "complete_replay_count" if row["status"] == "COMPLETE" else "unknown_replay_count"
                counts[state] += amount
                counts["candidate_replay_count"] += amount
                if state == "unknown_replay_count":
                    blockers = row.get("blockers") or (row.get("terminal") or {}).get("blockers") or []
                    if not isinstance(blockers, list) or any(not isinstance(x, str) for x in blockers):
                        raise ValueError("SHADOW_STREAM_REASONS_INVALID")
                    reasons.update({reason: amount for reason in set(blockers)})
                records += 1
        if (file_sha() != receipt.get("artifact_sha256") or sha.hexdigest() != receipt.get("uncompressed_sha256")
                or total != receipt.get("uncompressed_bytes") or records != receipt.get("record_count")):
            raise ValueError("SHADOW_STREAM_EOF_OR_HASH_MISMATCH")
        if any(type(receipt.get(k)) is not int or counts[k] != receipt[k] or counts[k] != report.get(k)
               for k in ("candidate_replay_count", "complete_replay_count", "unknown_replay_count")):
            raise ValueError("SHADOW_STREAM_COUNT_MISMATCH")
        if dict(reasons) != report.get("reason_counts"):
            raise ValueError("SHADOW_STREAM_REASON_COUNT_MISMATCH")
        index.verified_summary = {**{key: counts[key] for key in (
            "candidate_replay_count", "complete_replay_count", "unknown_replay_count")},
                                  "range_record_count": range_count, "record_count": records,
                                  "reason_counts": dict(reasons), "verified": True}
        return index
    except Exception as exc:
        index.close()
        if isinstance(exc, sqlite3.DatabaseError) and getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            raise ValueError("SHADOW_STREAM_INDEX_BUDGET_EXCEEDED") from exc
        raise
