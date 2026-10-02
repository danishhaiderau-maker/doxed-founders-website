"""Clean data epoch: identity, row classification and schema fingerprints.

Pure module (no bot.py, Flask or network). Shared by the Fly collector (stamp
``data_epoch_id`` on new rows), the laptop compatibility monitor
(``scripts/self_aware/data_compat.py``), the analyzer epoch guard
(``research/epoch_guard.py``) and ``clean_epoch_wipe.py``.

An epoch is declared by a manifest (``data_epoch.json`` in the runtime root,
mirrored to the laptop) written once when a bot boots with a new
``DATA_EPOCH_ID``. A row belongs to the epoch when it carries that
``data_epoch_id``; a row without a stamp belongs to it only when its own
timestamp is at or after ``started_at_ts`` and the stream is declared
unstampable (fixed-header CSV, hash-sealed V3 ledger rows) or epoch
independent (market tape). Everything else is PRE_EPOCH or FOREIGN and must
never enter a current-cohort analysis.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MANIFEST_SCHEMA = "data_epoch_manifest_v1"
CERTIFICATION_SCHEMA = "data_epoch_certification_v1"
MANIFEST_NAME = "data_epoch.json"
STAMP_FIELD = "data_epoch_id"
EPOCH_ID_RE = re.compile(r"^ce-\d{8}(?:T\d{6}Z)?-[a-z0-9][a-z0-9-]{0,39}$")
CERTIFICATION_WINDOW_SEC = 2 * 3600

# Rows carrying any of these keys are sealed by a content hash or signature;
# adding a field after the fact would break verification, so they are never
# stamped and are classified by their own timestamp instead.
INTEGRITY_KEYS = frozenset({
    "row_sha256", "record_sha256", "content_sha256", "payload_sha256", "event_sha256", "sha256", "signature",
    "hmac", "chain_sha256", "prev_sha256", "entry_sha256", "canonical_sha256", "envelope",
})

# Market data whose meaning does not depend on the bot's strategy version.
EPOCH_INDEPENDENT_BASES = frozenset({
    "market_microstructure_1s.jsonl", "cross_venue_tape_1m.jsonl", "market_context_1m.jsonl", "liquidations.jsonl",
})
# Streams whose writer cannot add a column/field; classified by timestamp.
UNSTAMPABLE_PREFIXES = ("v3/",)
UNSTAMPABLE_SUFFIXES = (".csv",)

# Version keys in priority order: the first present wins.
VERSION_KEYS = (
    STAMP_FIELD, "bot_version", "research_stack_version", "analyzer_sync_id", "policy_version", "dataset_epoch",
    "collection_epoch_id", "epoch_id", "collector_version", "schema_version",
)
_VERSION_RE = re.compile(rb'"(' + b"|".join(k.encode() for k in VERSION_KEYS) + rb')"\s*:\s*"([^"\\]{1,120})"')
# policy_version also carries free-form policy descriptions in V3 ledgers; only
# release-shaped values count as a data version.
_RELEASE_VALUE = re.compile(r"^(v\d|ce-|epoch-|collector_|cross_venue_|market_context_)")

TS_KEYS = (
    "bucket_ts", "minute_ts", "ts_epoch", "signal_ts", "decision_ts", "fill_ts", "close_ts", "submitted_ts",
    "observed_ts", "terminal_ts", "captured_at_ts", "recorded_at", "created_at", "ts", "timestamp", "time",
)
_TS_RE = re.compile(rb'"(' + b"|".join(k.encode() for k in TS_KEYS) + rb')"\s*:\s*("[^"]{8,40}"|-?[0-9][0-9.eE+]{8,24})')
MIN_VALID_TS = 1577836800.0  # 2020-01-01
MAX_VALID_TS = 4102444800.0  # 2100-01-01

CURRENT = "CURRENT"
CURRENT_UNSTAMPED = "CURRENT_UNSTAMPED"
UNSTAMPED_POST_EPOCH = "UNSTAMPED_POST_EPOCH"
PRE_EPOCH = "PRE_EPOCH"
FOREIGN = "FOREIGN_EPOCH"
INDEPENDENT = "EPOCH_INDEPENDENT"
UNDATED = "UNDATED"
LEGACY = "LEGACY"          # no epoch declared yet: every row predates the clean epoch
# The epoch starts at the boot of the first clean-epoch process (the previous process is stopped first), so a
# row timestamped after the start was written by clean-epoch code even when its writer does not stamp yet; it is
# admitted, and the compatibility monitor keeps it AMBER until the writer stamps.
COMPATIBLE_CLASSES = frozenset({CURRENT, CURRENT_UNSTAMPED, UNSTAMPED_POST_EPOCH, INDEPENDENT})


# ------------------------------------------------------------------ identity

def valid_epoch_id(epoch_id: Any) -> bool:
    return isinstance(epoch_id, str) and bool(EPOCH_ID_RE.match(epoch_id))


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_fingerprint(**parts: Any) -> dict:
    """Versions that define what an epoch's rows mean (bot, research stack, fill model, ...) plus their digest."""
    material = {str(k): str(v) for k, v in sorted(parts.items()) if v not in (None, "")}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return {**material, "sha256_16": digest}


def new_manifest(epoch_id: str, *, started_at_ts: float, source_git_rev: str = "", bot_version: str = "",
                 segment_prefix: str = "", previous: dict | None = None, fingerprint: dict | None = None) -> dict:
    if not valid_epoch_id(epoch_id):
        raise ValueError(f"invalid epoch id {epoch_id!r}: expected ce-YYYYMMDD[THHMMSSZ]-<label>")
    return {
        "schema": MANIFEST_SCHEMA, "epoch_id": epoch_id, "started_at_ts": float(started_at_ts),
        "started_at_utc": utc_iso(started_at_ts), "source_git_rev": source_git_rev, "bot_version": bot_version,
        "segment_prefix": segment_prefix, "stamp_field": STAMP_FIELD, "fingerprint": fingerprint or None,
        "previous": previous or None, "status": "OPEN",
    }


def validate_manifest(doc: Any) -> list[str]:
    problems = []
    if not isinstance(doc, dict):
        return ["manifest is not an object"]
    if doc.get("schema") != MANIFEST_SCHEMA:
        problems.append(f"schema {doc.get('schema')!r} != {MANIFEST_SCHEMA}")
    if not valid_epoch_id(doc.get("epoch_id")):
        problems.append(f"invalid epoch_id {doc.get('epoch_id')!r}")
    try:
        ts = float(doc.get("started_at_ts"))
        if not MIN_VALID_TS <= ts < MAX_VALID_TS:
            problems.append("started_at_ts outside plausible window")
    except (TypeError, ValueError):
        problems.append("started_at_ts missing")
    return problems


def load_manifest(path: str | os.PathLike) -> dict | None:
    """Valid manifest at ``path`` (file or directory holding data_epoch.json), else None."""
    p = Path(path)
    if p.is_dir():
        p = p / MANIFEST_NAME
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if not validate_manifest(doc) else None


def write_json_atomic(path: str | os.PathLike, doc: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def ensure_runtime_manifest(runtime_root: str | os.PathLike, epoch_id: str | None, *, now: float | None = None,
                            source_git_rev: str = "", bot_version: str = "", segment_prefix: str = "",
                            fingerprint: dict | None = None) -> dict | None:
    """Bot boot hook: keep the manifest for ``epoch_id`` or open a new one.

    A restart with the same id keeps the original start time; a new id opens a
    new epoch and records the previous manifest. No id configured means no
    epoch is declared (rows stay unstamped, everything classifies LEGACY).
    A restart under the same id with a different fingerprint keeps the epoch
    but records the change, so certification can reject a mixed epoch.
    """
    if not epoch_id:
        return None
    path = Path(runtime_root) / MANIFEST_NAME
    current = load_manifest(path)
    if current and current["epoch_id"] == epoch_id:
        if fingerprint and current.get("fingerprint") and current["fingerprint"] != fingerprint:
            changes = list(current.get("fingerprint_changes") or [])
            if not changes or changes[-1].get("fingerprint") != fingerprint:
                changes.append({"at_utc": utc_iso(time.time() if now is None else now), "fingerprint": fingerprint})
                current = {**current, "fingerprint_changes": changes[-20:]}
                write_json_atomic(path, current)
        return current
    doc = new_manifest(epoch_id, started_at_ts=time.time() if now is None else now, source_git_rev=source_git_rev,
                       bot_version=bot_version, segment_prefix=segment_prefix, fingerprint=fingerprint,
                       previous={k: current.get(k) for k in ("epoch_id", "started_at_utc", "status")} if current else None)
    write_json_atomic(path, doc)
    return doc


# ------------------------------------------------------------------ process-wide active epoch

_ACTIVE: dict | None = None
_ENV_ROOT: Path | None = None
_ENV_NEXT_TRY = 0.0
ENV_RETRY_SEC = 60.0


def activate(manifest: dict | None) -> None:
    """Declare the epoch every writer in this process stamps (bot boot calls this once)."""
    global _ACTIVE, _ENV_ROOT
    _ACTIVE = dict(manifest) if manifest and not validate_manifest(manifest) else None
    _ENV_ROOT = None


def activate_from_env(runtime_root: str | os.PathLike) -> dict | None:
    """Sidecar processes (collectors): adopt the bot's manifest when it names ``DATA_EPOCH_ID``.

    The bot writes the manifest; a sidecar started before it keeps retrying
    (at most once a minute) and never opens an epoch itself.
    """
    global _ENV_ROOT, _ENV_NEXT_TRY
    _ENV_ROOT, _ENV_NEXT_TRY = Path(runtime_root), 0.0
    return active_manifest()


def _adopt_env_manifest() -> None:
    global _ACTIVE, _ENV_NEXT_TRY
    now = time.time()
    if _ENV_ROOT is None or now < _ENV_NEXT_TRY:
        return
    _ENV_NEXT_TRY = now + ENV_RETRY_SEC
    wanted = (os.getenv("DATA_EPOCH_ID") or "").strip()
    doc = load_manifest(_ENV_ROOT) if wanted else None
    if doc and doc["epoch_id"] == wanted:
        _ACTIVE = doc


def active_manifest() -> dict | None:
    if _ACTIVE is None:
        _adopt_env_manifest()
    return _ACTIVE


def active_epoch_id() -> str | None:
    manifest = active_manifest()
    return manifest["epoch_id"] if manifest else None


def stamp_active(row: Any) -> Any:
    """``row`` stamped with the active epoch; unchanged when no epoch is active or the row is not stampable."""
    return stamp(row, active_epoch_id()) if isinstance(row, dict) else row


# ------------------------------------------------------------------ stamping

def stampable(row: Any) -> bool:
    return isinstance(row, dict) and not (INTEGRITY_KEYS & row.keys())


def stamp(row: dict, epoch_id: str | None) -> dict:
    """``row`` plus ``data_epoch_id`` (copy); unchanged if sealed, already stamped or no epoch."""
    if not epoch_id or not stampable(row) or STAMP_FIELD in row:
        return row
    return {**row, STAMP_FIELD: epoch_id}


# ------------------------------------------------------------------ classification

def base_of(relpath: str) -> str:
    return re.sub(r"\.\d+(\.gz)?$", "", relpath.replace("\\", "/"))


def epoch_independent(relpath: str) -> bool:
    return base_of(relpath).rsplit("/", 1)[-1] in EPOCH_INDEPENDENT_BASES and "/" not in base_of(relpath)


def unstampable(relpath: str) -> bool:
    base = base_of(relpath)
    return base.startswith(UNSTAMPABLE_PREFIXES) or base.endswith(UNSTAMPABLE_SUFFIXES)


def parse_ts(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        text = value.strip().strip('"')
        try:
            return parse_ts(float(text))
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" UTC", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return parse_ts(dt.timestamp())
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    number = number / 1000.0 if number > 1e11 else number
    return number if MIN_VALID_TS <= number < MAX_VALID_TS else None


def line_version(line: bytes) -> str:
    """Data version of one raw JSONL line by key priority, or UNVERSIONED."""
    found: dict[str, str] = {}
    for m in _VERSION_RE.finditer(line):
        key = m.group(1).decode()
        if key not in found:
            value = m.group(2).decode("utf-8", "replace")
            if key == "policy_version" and not _RELEASE_VALUE.match(value):
                continue
            found[key] = value
    for key in VERSION_KEYS:
        if key in found:
            return f"{key}={found[key]}"
    return "UNVERSIONED"


def line_stamp(line: bytes) -> str | None:
    m = re.search(rb'"' + STAMP_FIELD.encode() + rb'"\s*:\s*"([^"]{1,80})"', line)
    return m.group(1).decode() if m else None


def line_ts(line: bytes) -> float | None:
    """First plausible timestamp in key-priority order."""
    found: dict[str, float] = {}
    for m in _TS_RE.finditer(line):
        key = m.group(1).decode()
        if key in found:
            continue
        ts = parse_ts(m.group(2))
        if ts is not None:
            found[key] = ts
    for key in TS_KEYS:
        if key in found:
            return found[key]
    return None


def classify(relpath: str, *, stamp_value: str | None, ts: float | None, manifest: dict | None) -> str:
    """Epoch class of one row of ``relpath`` (runtime-relative path)."""
    if epoch_independent(relpath):
        return INDEPENDENT
    if not manifest:
        return LEGACY
    if stamp_value:
        return CURRENT if stamp_value == manifest["epoch_id"] else FOREIGN
    if ts is None:
        return UNDATED
    if ts < float(manifest["started_at_ts"]):
        return PRE_EPOCH
    return CURRENT_UNSTAMPED if unstampable(relpath) else UNSTAMPED_POST_EPOCH


def classify_row(relpath: str, row: dict, manifest: dict | None, ts_fields: Iterable[str] = TS_KEYS) -> str:
    """Same as :func:`classify` for an already-parsed row."""
    ts = None
    for key in ts_fields:
        ts = parse_ts(row.get(key)) if isinstance(row, dict) else None
        if ts is not None:
            break
    stamp_value = row.get(STAMP_FIELD) if isinstance(row, dict) else None
    return classify(relpath, stamp_value=stamp_value if isinstance(stamp_value, str) else None, ts=ts, manifest=manifest)


# ------------------------------------------------------------------ schema fingerprints

def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def flatten(row: dict, depth: int = 2, prefix: str = "", out: dict | None = None) -> dict:
    out = {} if out is None else out
    for key, value in row.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value and depth > 1:
            flatten(value, depth - 1, name + ".", out)
        else:
            out[name] = value
    return out


def _populated(value: Any) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return False
    if isinstance(value, str) and value.strip().lower() in ("none", "null", "nan"):
        return False
    return True


def _zero(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return float(value) == 0.0
    if isinstance(value, str):
        try:
            return float(value) == 0.0
        except ValueError:
            return False
    return False


RECORD_KIND_KEYS = ("record_type", "event_type", "kind", "event", "type", "schema")


def record_kind(row: dict) -> str:
    for key in RECORD_KIND_KEYS:
        value = row.get(key)
        if isinstance(value, str) and 0 < len(value) <= 64:
            return f"{key}={value}"
    return "*"


def fingerprint(rows: list[dict], depth: int = 2) -> dict:
    """Field set, types and populated fraction of sampled rows of one (stream, version, kind)."""
    fields: dict[str, dict[str, Any]] = {}
    for row in rows:
        for name, value in flatten(row, depth).items():
            f = fields.setdefault(name, {"types": set(), "present": 0, "populated": 0, "zero": 0})
            f["present"] += 1
            if _populated(value):
                f["populated"] += 1
                f["types"].add(_type_name(value))
            if _zero(value):
                f["zero"] += 1
    n = len(rows)
    spec = {name: sorted(f["types"]) or ["null"] for name, f in sorted(fields.items())}
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    out = {}
    for name, f in sorted(fields.items()):
        out[name] = {"types": sorted(f["types"]) or ["null"],
                     "present_pct": round(100.0 * f["present"] / n, 1) if n else 0.0,
                     "populated_pct": round(100.0 * f["populated"] / n, 1) if n else 0.0,
                     "zero_pct": round(100.0 * f["zero"] / max(f["populated"], 1), 1)}
    return {"rows_sampled": n, "fingerprint": digest, "fields": out}


def dead_fields(fp: dict, min_rows: int = 20) -> list[dict]:
    """Fields present in a version but never populated (or numerically always zero)."""
    if fp.get("rows_sampled", 0) < min_rows:
        return []
    dead = []
    for name, f in fp["fields"].items():
        if f["present_pct"] >= 50 and f["populated_pct"] == 0:
            dead.append({"field": name, "status": "DEAD_NULL"})
        elif f["populated_pct"] >= 50 and f["types"] == ["number"] and f["zero_pct"] == 100.0:
            dead.append({"field": name, "status": "DEAD_ZERO"})
    return dead


def schema_drift(announced: dict[str, list[str]] | None, fp: dict) -> dict:
    """New fields or type changes versus an announced field->types map (missing fields are not drift)."""
    if announced is None:
        return {"status": "UNANNOUNCED", "new_fields": sorted(fp["fields"]), "type_changes": []}
    new = sorted(name for name in fp["fields"] if name not in announced)
    changed = []
    for name, f in fp["fields"].items():
        if name in announced:
            extra = sorted(set(f["types"]) - set(announced[name]) - {"null"})
            if extra:
                changed.append({"field": name, "announced": announced[name], "observed": f["types"]})
    return {"status": "DRIFT" if new or changed else "MATCH", "new_fields": new, "type_changes": changed}


# ------------------------------------------------------------------ certification

def certification_doc(manifest: dict, *, checks: list[dict], now: float) -> dict:
    """GREEN only when the epoch is >= 2 h old and every check is GREEN."""
    age = now - float(manifest["started_at_ts"])
    failing = [c for c in checks if c.get("severity") != "GREEN"]
    if age < CERTIFICATION_WINDOW_SEC:
        status = "PENDING"
    elif failing or not checks:
        status = "REJECTED"
    else:
        status = "CERTIFIED"
    return {"schema": CERTIFICATION_SCHEMA, "epoch_id": manifest["epoch_id"], "started_at_utc": manifest["started_at_utc"],
            "checked_at_utc": utc_iso(now), "epoch_age_sec": round(age, 1),
            "window_sec": CERTIFICATION_WINDOW_SEC, "status": status, "checks": checks,
            "failing": [c.get("id") for c in failing]}


def certified(cert: Any, epoch_id: str) -> bool:
    return (isinstance(cert, dict) and cert.get("schema") == CERTIFICATION_SCHEMA and cert.get("epoch_id") == epoch_id
            and cert.get("status") == "CERTIFIED")
