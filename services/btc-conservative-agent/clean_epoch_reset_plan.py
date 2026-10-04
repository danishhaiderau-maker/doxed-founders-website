"""Read-only dry run and verification for the guarded Fresh Collection reset at the clean-epoch paper boundary.

The reset itself is ``POST /api/wipe_fly_only`` (``perform_fresh_collection_reset(send_local_signal=False)``):
it admits only a paused, disarmed, force-paper, flat book with an empty WAL and a clear recovery audit, then
deletes retired research payloads and every V3 ledger, retires empty generation authority and publishes a new
collector epoch. This module never mutates anything; it answers two questions for the runbook/workflow.

    python /app/clean_epoch_reset_plan.py plan   --runtime-root /app/data/runtime
    python /app/clean_epoch_reset_plan.py verify --runtime-root /app/data/runtime --epoch <DATA_EPOCH_ID>

``plan`` lists what the reset would delete (by category and bytes, from ``plan_research_reset`` without a
proof) and fails when any protected file would be a candidate: relay/Bitfinex evidence, paper lifecycle and
restart-recovery state, generation authority, the data-epoch manifest/boundary receipts, or the 1 s tape.
``verify`` runs after the reset and before paper resume: every V3 head is absent, empty or starts inside the
epoch, no generation pointer survived (absent pointer = generation 0, so appends stay valid across deploys),
and every protected file that existed before is still present.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

import data_epoch
from epoch_boundary_rotation import V3_LEDGERS, head_decision
from research_reset_inventory import ESSENTIAL_NAMES, EPOCH_INDEPENDENT_MARKET_NAMES, plan_research_reset

SCHEMA = "clean_epoch_reset_plan_v1"
CANDIDATE_REASON = "EPOCH_RECOVERY_BOUNDARY_PROOF_REQUIRED"
PHYSICAL_SCOPES = ("research", "research_accumulator", "research_archive")
RELAY_EVIDENCE_NAMES = frozenset({"paper_lifecycle_v1.json", "relay_lifecycle_evidence_v1.json"})
PROTECTED_NAMES = ESSENTIAL_NAMES | RELAY_EVIDENCE_NAMES | {data_epoch.MANIFEST_NAME}
PROTECTED_PARTS = frozenset({"ledger_generations_v1", "append_heads", "recovery", "data_epoch_boundary",
                             "research_reset_receipts"})
GENERATION_POINTERS = "v3/receipts/ledger_generations_v1"


def _base(name: str) -> str:
    return re.sub(r"\.\d+(?:\.gz)?$", "", name)


def protected(relative: str) -> bool:
    path = PurePosixPath(relative)
    return (path.name in PROTECTED_NAMES or _base(path.name) in EPOCH_INDEPENDENT_MARKET_NAMES
            or any(part in PROTECTED_PARTS for part in path.parts[:-1]))


def _scopes(root: Path) -> list[str | None]:
    return [None, *(name for name in PHYSICAL_SCOPES if (root / name).is_symlink())]


def _candidate_sample(root: Path) -> dict:
    rows = {}
    for name in _scopes(root):
        result = plan_research_reset(str(root), proof=None, allow_fly_runtime_aliases=True, scope_name=name)
        for row in result.get("retained", []):
            if row.get("reason") == CANDIDATE_REASON:
                rows[row["absolute_path"]] = (row["path"], row["category"], int(row["size_bytes"]),
                                              int(row["mtime_ns"]), int(row["inode"]))
    return rows


def target_stability(before: dict, after: dict, window_sec: float) -> dict:
    """Report targets that changed across the plan window; informational only.

    The paused bot keeps collecting research through writers that the reset's
    in-process gate blocks only while it is held, and sidecars honor the
    cross-process fence only during the reset. Changes here are therefore
    expected; the blocking two-sample check runs inside the reset under every
    barrier, before any reset pointer is written.
    """
    changed = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old != new:
            row = old or new
            changed.append({"path": row[0], "category": row[1],
                            "size_delta": (new[2] if new else 0) - (old[2] if old else 0),
                            "appeared": old is None, "vanished": new is None})
    return {"blocking": False, "window_sec": round(window_sec, 1), "sampled_targets": len(before),
            "changed_count": len(changed), "changed": changed[:50],
            "blocking_check": "RESET_TARGETS_CHANGED_UNDER_BARRIERS (in-reset, pre-pointer)"}


def plan(runtime_root: str) -> dict:
    import time
    root = Path(runtime_root)
    categories: dict[str, dict] = {}
    violations, scopes, incomplete = [], [], []
    started = time.monotonic()
    before = {}
    for name in _scopes(root):
        result = plan_research_reset(str(root), proof=None, allow_fly_runtime_aliases=True, scope_name=name)
        scopes.append(name or "runtime")
        if result.get("errors"):
            incomplete.append({"scope": name or "runtime", "errors": result["errors"]})
        for row in result.get("retained", []):
            if row.get("reason") != CANDIDATE_REASON:
                continue
            before[row["absolute_path"]] = (row["path"], row["category"], int(row["size_bytes"]),
                                            int(row["mtime_ns"]), int(row["inode"]))
            bucket = categories.setdefault(row["category"], {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += int(row.get("size_bytes") or 0)
            if protected(row["path"]):
                violations.append(row["path"])
    pointers = sorted(p.parent.name for p in (root / GENERATION_POINTERS).glob("*/ACTIVE.json"))
    gates = execute_gates(root)
    stability = target_stability(before, _candidate_sample(root), time.monotonic() - started)
    ok = not violations and not incomplete and gates["ok"]
    return {"schema": SCHEMA, "mode": "plan", "read_only": True, "ok": ok, "runtime_root": str(root),
            "execute_gates": gates, "target_stability": stability,
            "scopes": scopes, "would_delete": dict(sorted(categories.items())),
            "would_delete_files": sum(b["files"] for b in categories.values()),
            "would_delete_bytes": sum(b["bytes"] for b in categories.values()),
            "protected_candidates": sorted(violations), "incomplete_inventory": incomplete,
            "v3_generation_pointers": pointers, "relay_evidence": relay_evidence(root),
            "protected_present": _protected_inventory(root)}


def _as_proven(result: dict) -> dict:
    """The plan execute builds once the boundary proof exists: candidates become targets."""
    targets = [{k: v for k, v in row.items() if k != "reason"} for row in result["retained"]
               if row.get("reason") == CANDIDATE_REASON]
    retained = [row for row in result["retained"] if row.get("reason") != CANDIDATE_REASON]
    return {**result, "targets": targets, "retained": retained,
            "target_count": len(targets), "target_bytes": sum(int(r["size_bytes"]) for r in targets),
            "hardlinked_target_count": sum(bool(r.get("hardlinked")) for r in targets),
            "proof_sha256": hashlib.sha256(b"clean_epoch_reset_plan_dry_run").hexdigest()}


def execute_gates(root: Path) -> dict:
    """Dry-run every execute gate that can be evaluated read-only while the bot runs.

    Per scope: inventory completeness, target budget, deletion admission and the
    receipt-context binding, through the same ``admit_research_reset_plan`` the
    executor calls. Boundary: V3 read identity, emergency WAL, recovery and
    auxiliary audits. Quiescence/flat/disarmed are proven by the caller at execute.
    """
    from research_exact_deletion import ResearchDeletionRejected
    from research_reset_execution import admit_research_reset_plan

    failures, scopes = [], []
    for name in _scopes(root):
        label = name or "runtime"
        row = {"scope": label}
        try:
            result = plan_research_reset(str(root), proof=None, allow_fly_runtime_aliases=True, scope_name=name)
            if result.get("complete") is not True or result.get("errors"):
                raise ResearchDeletionRejected("RESET_INVENTORY_INCOMPLETE")
            proven = _as_proven(result)
            scope_root = Path(proven["scope_root"])
            context, admission = admit_research_reset_plan(
                proven, receipt_path=scope_root / "research_reset_receipts" / "plan-dry-run" / "deletion.json",
                quiescent=True, recovery_states={"reset_plan_dry_run": "NOT_PRESENT"}, validate_only=True)
            binding = admission["context"]
            row.update(status="ADMITTED", target_count=proven["target_count"],
                       target_bytes=proven["target_bytes"], retained_count=binding["retained_count"],
                       retained_bytes=binding["retained_bytes"],
                       receipt_context_bytes=len(json.dumps(binding, sort_keys=True).encode()))
        except (ResearchDeletionRejected, ValueError, OSError) as exc:
            row.update(status="REFUSED", code=str(exc) or type(exc).__name__)
            failures.append(f"{label}:{row['code']}")
        scopes.append(row)
    boundary = _boundary_gates(root)
    failures.extend(boundary["failures"])
    return {"ok": not failures, "failures": failures, "scopes": scopes, "boundary": boundary}


def _boundary_gates(root: Path) -> dict:
    import os
    from research_reset_auxiliary_audit import audit_auxiliary_cleanup
    from research_reset_recovery_audit import audit_research_reset_recovery
    from v3_marker_quarantine import preflight

    failures = []
    revision = str(os.getenv("SOURCE_GIT_REV") or "")[:12].lower()
    identity = preflight(str(root), revision)
    if identity.get("ok") is not True:
        failures.append("V3_IDENTITY_OR_WAL:" + str(identity.get("open_read_only")) + "/" + str(identity.get("wal")))
    adopted = identity.get("adopted_identity")
    # No ACTIVE authority means execute binds the legacy session identity,
    # which only the running bot can read; the audit then runs at execute.
    recovery = {"status": "SKIPPED_NO_ADOPTED_IDENTITY"}
    if adopted and adopted.get("epoch_id"):
        try:
            audit = audit_research_reset_recovery(str(root), expected_identity=adopted)
            recovery = {key: audit.get(key) for key in ("complete", "safe_for_reset_recovery_scope",
                                                         "pending_or_unknown_count", "blockers")}
            if (audit.get("complete") is not True or audit.get("safe_for_reset_recovery_scope") is not True
                    or audit.get("pending_or_unknown_count") != 0):
                failures.append("RESET_RECOVERY_NOT_PROVEN_CLEAR")
        except (ValueError, OSError) as exc:
            recovery = {"status": "ERROR", "error": str(exc)}
            failures.append("RESET_RECOVERY_NOT_PROVEN_CLEAR")
    auxiliary = []
    for path in ([root, root.parent] if root.name == "runtime" else [root]):
        try:
            audit = audit_auxiliary_cleanup(str(path))
            auxiliary.append({"path": str(path), **{key: audit.get(key) for key in
                              ("complete", "safe", "pending_or_unknown_count")}})
            if (audit.get("complete") is not True or audit.get("safe") is not True
                    or audit.get("pending_or_unknown_count") != 0):
                failures.append("RESET_AUXILIARY_RECOVERY_NOT_PROVEN_CLEAR")
        except (ValueError, OSError) as exc:
            auxiliary.append({"path": str(path), "error": str(exc)})
            failures.append("RESET_AUXILIARY_RECOVERY_NOT_PROVEN_CLEAR")
    active_reset = active_reset_gate(root)
    if active_reset["status"] not in {"ABSENT", "COMPLETE", "RETIREABLE_REVIEWED_ATTEMPT"}:
        failures.append("RESET_ACTIVE_POINTER_NOT_RETIREABLE:" + active_reset["status"])
    return {"identity": identity, "recovery": recovery, "auxiliary": auxiliary,
            "active_reset": active_reset, "failures": failures}


def active_reset_gate(root: Path) -> dict:
    """Predict whether execute can bind: an active pointer must be a registered,
    byte-exact restart-bound incident, else execute refuses it at resume."""
    import research_reset_predeletion_abort as abort
    from research_reset_receipt_state import active_reset_receipt_exists

    receipts = root / "research_reset_receipts"
    pointer = receipts / "ACTIVE_RESET.json"
    try:
        if not active_reset_receipt_exists(root):
            return {"status": "COMPLETE" if pointer.exists() else "ABSENT"}
        reset_id = json.loads(pointer.read_bytes()).get("reset_id")
        incident = abort.ADDITIONAL_REVIEWED_ATTEMPTS.get(reset_id)
        if incident is None or "restart_continuity" not in incident:
            return {"status": "UNREGISTERED_ACTIVE_POINTER", "reset_id": reset_id}
        paths = {"active": pointer, "binding": receipts / reset_id / "binding.json",
                 "operation": receipts / reset_id / "operation.json"}
        observed = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}
        later = [name for name in ("deletion.json", "deletion.json.progress.jsonl", "genome-deletion.json")
                 if (receipts / reset_id / name).exists()]
        if observed != incident["hashes"] or later:
            return {"status": "REGISTERED_ATTEMPT_CHANGED", "reset_id": reset_id, "later_stage_files": later}
        return {"status": "RETIREABLE_REVIEWED_ATTEMPT", "reset_id": reset_id}
    except (OSError, ValueError) as exc:
        return {"status": "UNREADABLE", "error": type(exc).__name__}


def relay_evidence(root: Path) -> dict:
    """Pending/acked relay outbox events persisted in paper_lifecycle_v1.json (never in V3 ledgers)."""
    try:
        doc = json.loads((root / "paper_lifecycle_v1.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"readable": False, "pending": None, "acks": None, "pending_ids_sha256": None}
    events = doc.get("relay_events") if isinstance(doc, dict) else None
    events = events if isinstance(events, dict) else {}
    pending = sorted(str(r["event_id"]) for r in events.get("pending") or [] if isinstance(r, dict) and r.get("event_id"))
    acks = [r for r in events.get("acks") or [] if isinstance(r, dict)]
    return {"readable": True, "pending": len(pending), "acks": len(acks), "pending_event_ids": pending,
            "pending_ids_sha256": hashlib.sha256(json.dumps(pending).encode()).hexdigest()}


def _protected_inventory(root: Path) -> list[str]:
    found = []
    for name in sorted(PROTECTED_NAMES | EPOCH_INDEPENDENT_MARKET_NAMES):
        if (root / name).is_file():
            found.append(name)
    return found


def verify(runtime_root: str, epoch_id: str, expect_present: list[str] | None = None,
           expect_relay_sha256: str | None = None) -> dict:
    root = Path(runtime_root)
    manifest = data_epoch.load_manifest(root)
    failures = []
    if not manifest or manifest.get("epoch_id") != epoch_id:
        failures.append("DATA_EPOCH_MANIFEST_MISMATCH")
    started = float((manifest or {}).get("started_at_ts") or 0)
    heads = {}
    for ledger in V3_LEDGERS:
        decision, ts, size = head_decision(root / "v3" / "ledgers" / f"{ledger}.jsonl", started)
        heads[ledger] = {"head_decision": decision, "head_first_row_ts": ts, "head_bytes": size}
        if decision in {"PRE_EPOCH_HEAD", "UNDATED_HEAD"}:
            failures.append(f"V3_PRE_EPOCH_ROWS:{ledger}")
        if any((root / "v3" / "ledgers").glob(f"{ledger}.jsonl.*")):
            failures.append(f"V3_SEALED_GENERATION_SURVIVED:{ledger}")
    pointers = sorted(p.parent.name for p in (root / GENERATION_POINTERS).glob("*/ACTIVE.json"))
    if pointers:
        failures.append("V3_GENERATION_POINTER_SURVIVED")
    present = set(_protected_inventory(root))
    missing = sorted(set(expect_present or []) - present)
    failures.extend(f"PROTECTED_FILE_MISSING:{name}" for name in missing)
    relay = relay_evidence(root)
    if expect_relay_sha256 and relay["pending_ids_sha256"] != expect_relay_sha256:
        failures.append("RELAY_PENDING_EVIDENCE_CHANGED")
    return {"schema": SCHEMA, "mode": "verify", "read_only": True, "ok": not failures, "epoch_id": epoch_id,
            "runtime_root": str(root), "v3_heads": heads, "v3_generation_pointers": pointers,
            "relay_evidence": relay, "protected_present": sorted(present), "failures": failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("plan", "verify"))
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--epoch")
    parser.add_argument("--expect-present", default="", help="comma-separated protected names from the plan")
    parser.add_argument("--expect-relay-sha256", default="", help="relay_evidence.pending_ids_sha256 from the plan")
    args = parser.parse_args(argv)
    if args.mode == "plan":
        out = plan(args.runtime_root)
    else:
        if not args.epoch:
            parser.error("verify requires --epoch")
        expected = [n for n in args.expect_present.split(",") if n]
        out = verify(args.runtime_root, args.epoch, expected, args.expect_relay_sha256 or None)
    print(json.dumps(out, sort_keys=True))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
