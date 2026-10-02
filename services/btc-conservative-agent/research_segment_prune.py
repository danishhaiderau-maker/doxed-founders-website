"""Custody-gated pruning of the Fly segment store and shipped runtime rotations.

Runs inside the segment shipper process, strictly between cycles (never
between ``write_intent`` and ``complete_intent``). Nothing is deleted unless
ALL of these hold:

* ``RESEARCH_SEGMENTS_PRUNE_ENABLED=1`` (env master switch) and the persisted
  mode (``<state-dir>/prune-mode.json``; default ``dry_run``) is ``enforce``;
* at least ``REQUIRED_PROVEN_ACK_CYCLES`` advancing laptop ACK receipts;
* a laptop custody receipt (``<prefix>/acks/custody/<seq>.json``, write-once,
  validated by the server against the manifest chain and the recorded ACK)
  certifies, for ``through_seq``: laptop ACK, checkpoint parity GREEN (every
  sealed file's laptop sha256 equals this checkpoint's sha256) and that a
  completed analyzer generation whose findings are in the verified analysis
  archive consumed that seq.

The prune bound is ``min(custody.through_seq, latest ACK)``. Then:

* segment files ``seg/<seq>.tar.gz`` with ``seq <= bound`` older than
  ``SEGMENT_MIN_AGE_HOURS`` are deleted; manifests (the hash chain) stay and
  the server answers ``410 PRUNED`` for their bytes;
* runtime rotations the checkpoint records as shipped whole (class
  ``snapshot`` with sha256, ``shipped_seq <= bound``, never baseline) whose
  sha256 the custody receipt lists in ``verified_files`` (the laptop hashed
  its own copy, consumed by the analyzer), whose size/inode/mtime are
  unchanged, that ``data_retention_policy`` classifies as deletable on Fly,
  past their age and outside the newest-N per stream.

Every deletion is appended (fsynced) to ``runtime/retention/prune_ledger.jsonl``
which itself ships to the laptop. ``dry_run`` writes the same plan to
``<state-dir>/prune-plan-latest.json`` and deletes nothing.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import data_retention_policy as policy
import research_segment_format as fmt

PRUNE_ENABLED = True
REQUIRED_PROVEN_ACK_CYCLES = 2
CUSTODY_SCHEMA = "research_segment_custody_receipt_v1"
LEDGER_SCHEMA = "fly_prune_ledger_v1"
PLAN_SCHEMA = "fly_prune_plan_v1"
MODE_SCHEMA = "fly_prune_mode_v1"
MODES = ("off", "dry_run", "enforce")
DEFAULT_MODE = "dry_run"
SEGMENT_MIN_AGE_HOURS = 6.0
TIER_B_MIN_AGE_HOURS = 12.0
TIER_B_KEEP_LATEST = 2
OTHER_MIN_AGE_HOURS = 24.0
OTHER_KEEP_LATEST = 3
MAX_DELETE_BYTES_PER_PASS = 4 * 1024 ** 3
LEDGER_RELPATH = "retention/prune_ledger.jsonl"
MAX_VERIFIED_FILES = 20000


def custody_key(prefix: str, seq: int) -> str:
    return f"{fmt.validate_prefix(prefix)}/acks/custody/{fmt.seq_token(seq)}.json"


def custody_dir(store_root: Path, prefix: str) -> Path:
    return Path(store_root).joinpath(*fmt.validate_prefix(prefix).split("/"), "acks", "custody")


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def mode_path(state_dir: Path) -> Path:
    return Path(state_dir) / "prune-mode.json"


def read_mode(state_dir: Path, environ=None) -> tuple[str, str]:
    """Return (mode, why). The env switch is the master; the file chooses within it."""
    env = os.environ if environ is None else environ
    if not PRUNE_ENABLED:
        return "off", "PRUNE_DISABLED_IN_CODE"
    if (env.get("RESEARCH_SEGMENTS_PRUNE_ENABLED") or "0").strip() != "1":
        return "off", "PRUNE_DISABLED_BY_ENV"
    stored = _load(mode_path(state_dir)).get("mode")
    if stored in MODES:
        return stored, "PERSISTED_MODE"
    default = (env.get("RESEARCH_SEGMENTS_PRUNE_DEFAULT_MODE") or DEFAULT_MODE).strip()
    return (default if default in MODES else DEFAULT_MODE), "DEFAULT_MODE"


def write_mode(state_dir: Path, mode: str, *, clock=time.time, actor: str = "admin") -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    payload = {"schema": MODE_SCHEMA, "mode": mode, "set_at": _iso(clock()), "actor": actor}
    path = mode_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    return payload


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def validate_custody(receipt: dict, *, prefix: str, manifest_sha256_at, acked_seq: int) -> str | None:
    """Return an error code, or None when the receipt may be recorded."""
    if receipt.get("schema") != CUSTODY_SCHEMA or receipt.get("prefix") != prefix:
        return "CUSTODY_SCHEMA_OR_PREFIX"
    through = receipt.get("through_seq")
    if not isinstance(through, int) or through < 1:
        return "CUSTODY_SEQ_INVALID"
    if through > int(acked_seq or 0):
        return "CUSTODY_AHEAD_OF_ACK"
    expected = manifest_sha256_at(through)
    if not expected or expected != receipt.get("manifest_sha256"):
        return "CUSTODY_MANIFEST_MISMATCH"
    if receipt.get("parity_verdict") != "GREEN":
        return "CUSTODY_PARITY_NOT_GREEN"
    for field in ("parity_seq", "analyzer_consumed_seq", "acked_seq"):
        value = receipt.get(field)
        if not isinstance(value, int) or value < through:
            return f"CUSTODY_{field.upper()}_BELOW_THROUGH"
    if not receipt.get("analysis_snapshot_id") or not receipt.get("analysis_snapshot_receipt_sha256"):
        return "CUSTODY_NO_ANALYSIS_SNAPSHOT"
    verified = receipt.get("verified_files")
    if not isinstance(verified, dict) or len(verified) > MAX_VERIFIED_FILES:
        return "CUSTODY_VERIFIED_FILES_INVALID"
    for relpath, digest in verified.items():
        if (not isinstance(relpath, str) or "/" in relpath or not isinstance(digest, str)
                or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
            return "CUSTODY_VERIFIED_FILES_INVALID"
    return None


def latest_custody(store_root: Path, prefix: str) -> dict | None:
    directory = custody_dir(store_root, prefix)
    if not directory.is_dir():
        return None
    best = None
    with os.scandir(directory) as entries:
        for entry in entries:
            token = entry.name.removesuffix(".json")
            if entry.is_file() and token.isdigit() and (best is None or int(token) > best[0]):
                best = (int(token), entry.path)
    if best is None:
        return None
    receipt = _load(Path(best[1]))
    return receipt if receipt.get("through_seq") == best[0] else None


def ack_receipts(store_root: Path, prefix: str) -> list[dict]:
    directory = Path(store_root).joinpath(*fmt.validate_prefix(prefix).split("/"), "acks",
                                          "laptop-receipts")
    if not directory.is_dir():
        return []
    receipts = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".json") and not entry.name.startswith("."):
                receipt = _load(Path(entry.path))
                if isinstance(receipt.get("through_seq"), int):
                    receipts.append(receipt)
    return sorted(receipts, key=lambda item: item["through_seq"])


def _segment_candidates(store_root: Path, prefix: str, bound: int, now: float) -> list[dict]:
    seg_dir = Path(store_root).joinpath(*prefix.split("/"), "seg")
    out = []
    if not seg_dir.is_dir() or bound < 1:
        return out
    with os.scandir(seg_dir) as entries:
        for entry in entries:
            token = entry.name.removesuffix(".tar.gz")
            if not entry.is_file() or not token.isdigit() or int(token) > bound:
                continue
            stat = entry.stat()
            if now - stat.st_mtime < SEGMENT_MIN_AGE_HOURS * 3600:
                continue
            out.append({"kind": "segment", "seq": int(token), "path": entry.path,
                        "relpath": f"{prefix}/seg/{entry.name}", "size": int(stat.st_size)})
    return sorted(out, key=lambda item: item["seq"])


def _runtime_candidates(shipper_state: dict, universe: dict, bound: int, now: float,
                        verified: dict) -> tuple[list, dict]:
    by_base: dict[str, list[tuple[int, str, dict, tuple]]] = {}
    skipped: dict[str, int] = {}
    for relpath, tracked in (shipper_state.get("files") or {}).items():
        parts = policy.rotation_parts(relpath)
        if parts is None:
            continue
        current = universe.get(relpath)
        if current is None:
            continue
        by_base.setdefault(parts[0], []).append((parts[1], relpath, tracked, current))
    out = []
    for base, rows in by_base.items():
        tier_b = base in policy.TIER_B_BASES
        keep = TIER_B_KEEP_LATEST if tier_b else OTHER_KEEP_LATEST
        min_age = TIER_B_MIN_AGE_HOURS if tier_b else OTHER_MIN_AGE_HOURS
        newest_first = sorted(rows, key=lambda row: row[3][1].st_mtime, reverse=True)
        for index, (_gen, relpath, tracked, (path, stat)) in enumerate(newest_first):
            reason = None
            if not policy.deletable_rotation(relpath, fly=True):
                reason = "POLICY_PROTECTED"
            elif tracked.get("class") != "snapshot" or not tracked.get("sha256"):
                reason = "NOT_SHIPPED_WHOLE"
            elif tracked.get("baseline"):
                reason = "BASELINE_NEVER_SHIPPED"
            elif not int(tracked.get("shipped_seq") or 0) or int(tracked["shipped_seq"]) > bound:
                reason = "SHIPPED_AFTER_CUSTODY"
            elif verified.get(relpath) != tracked["sha256"]:
                # The laptop holds byte-identical content only when its own sha256
                # matches (streams baselined at genesis never do).
                reason = "NOT_LAPTOP_HASH_VERIFIED"
            elif (int(stat.st_size), int(stat.st_ino), int(stat.st_mtime_ns)) != (
                    tracked.get("size"), tracked.get("inode"), tracked.get("mtime_ns")):
                reason = "CHANGED_SINCE_SHIPPED"
            elif index < keep:
                reason = "NEWEST_KEPT"
            elif now - stat.st_mtime < min_age * 3600:
                reason = "TOO_YOUNG"
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            out.append({"kind": "runtime", "relpath": relpath, "path": str(path), "size": int(stat.st_size),
                        "sha256": tracked["sha256"], "shipped_seq": int(tracked["shipped_seq"]),
                        "tier": policy.classify(relpath), "identity": [int(stat.st_size), int(stat.st_ino),
                                                                        int(stat.st_mtime_ns)]})
    return sorted(out, key=lambda item: item["shipped_seq"]), skipped


def plan_prune(*, shipper_state: dict, universe: dict, store_root: Path, prefix: str,
               custody: dict | None = None, now: float | None = None, rules: dict | None = None,
               **_legacy) -> dict:
    """Return the gate verdict and candidates. Never modifies anything."""
    now = float(now if now is not None else time.time())
    receipts = ack_receipts(store_root, prefix)
    acked_seq = int(receipts[-1]["through_seq"]) if receipts else 0
    advancing = [r for i, r in enumerate(receipts) if i == 0 or r["through_seq"] > receipts[i - 1]["through_seq"]]
    reasons = []
    if not PRUNE_ENABLED:
        reasons.append("PRUNE_DISABLED_IN_CODE")
    if len(advancing) < REQUIRED_PROVEN_ACK_CYCLES:
        reasons.append("INSUFFICIENT_PROVEN_ACK_CYCLES")
    custody = custody or {}
    through = custody.get("through_seq") if isinstance(custody.get("through_seq"), int) else 0
    if not through:
        reasons.append("NO_LAPTOP_CUSTODY_RECEIPT")
    elif custody.get("parity_verdict") != "GREEN":
        reasons.append("CUSTODY_PARITY_NOT_GREEN")
    bound = min(through, acked_seq) if not reasons else 0
    segments = _segment_candidates(store_root, prefix, bound, now) if bound else []
    verified = custody.get("verified_files") if isinstance(custody.get("verified_files"), dict) else {}
    runtime, skipped = (_runtime_candidates(shipper_state, universe, bound, now, verified)
                        if bound else ([], {}))
    return {
        "schema": PLAN_SCHEMA, "planned_at": _iso(now), "allowed": not reasons, "deny_reasons": reasons,
        "acked_seq": acked_seq, "custody_through_seq": through or None, "bound_seq": bound,
        "proven_ack_cycles": len(advancing), "segments": segments, "runtime": runtime,
        "runtime_skipped": skipped,
        "segment_bytes": sum(item["size"] for item in segments),
        "runtime_bytes": sum(item["size"] for item in runtime),
        "candidates": segments + runtime,
        "candidate_bytes": sum(item["size"] for item in segments + runtime),
    }


def _append_ledger(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"schema": LEDGER_SCHEMA, **row}, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def execute(plan: dict, *, runtime_root: Path, clock=time.time,
            max_bytes: int = MAX_DELETE_BYTES_PER_PASS) -> dict:
    """Delete the plan's candidates (re-checking identity), ledgering each one."""
    ledger = Path(runtime_root).joinpath(*LEDGER_RELPATH.split("/"))
    result = {"deleted": 0, "deleted_bytes": 0, "segment_bytes": 0, "runtime_bytes": 0,
              "skipped_changed": 0, "max_segment_seq": 0}
    if not plan.get("allowed"):
        return result
    common = {"bound_seq": plan["bound_seq"], "custody_through_seq": plan["custody_through_seq"],
              "acked_seq": plan["acked_seq"]}
    for item in plan["segments"] + plan["runtime"]:
        if result["deleted_bytes"] >= max_bytes:
            break
        path = Path(item["path"])
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        if item["kind"] == "runtime" and [int(stat.st_size), int(stat.st_ino), int(stat.st_mtime_ns)] \
                != item["identity"]:
            result["skipped_changed"] += 1
            continue
        path.unlink()
        _append_ledger(ledger, {"deleted_at": _iso(clock()), "kind": item["kind"], "relpath": item["relpath"],
                                "bytes": item["size"], "sha256": item.get("sha256"), "seq": item.get("seq"),
                                "shipped_seq": item.get("shipped_seq"), "tier": item.get("tier"), **common})
        result["deleted"] += 1
        result["deleted_bytes"] += item["size"]
        result[f"{item['kind']}_bytes"] += item["size"]
        if item["kind"] == "segment":
            result["max_segment_seq"] = max(result["max_segment_seq"], int(item["seq"]))
    return result


def summarize(plan: dict, limit: int = 20) -> dict:
    return {k: plan.get(k) for k in ("schema", "planned_at", "allowed", "deny_reasons", "acked_seq",
                                     "custody_through_seq", "bound_seq", "proven_ack_cycles",
                                     "segment_bytes", "runtime_bytes", "candidate_bytes", "runtime_skipped")} | {
        "segment_count": len(plan.get("segments") or []), "runtime_count": len(plan.get("runtime") or []),
        "runtime_examples": [item["relpath"] for item in (plan.get("runtime") or [])[:limit]],
        "segment_seq_range": ([plan["segments"][0]["seq"], plan["segments"][-1]["seq"]]
                              if plan.get("segments") else None),
    }
