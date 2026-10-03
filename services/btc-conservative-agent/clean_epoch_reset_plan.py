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


def plan(runtime_root: str) -> dict:
    root = Path(runtime_root)
    categories: dict[str, dict] = {}
    violations, scopes, incomplete = [], [], []
    for name in _scopes(root):
        result = plan_research_reset(str(root), proof=None, allow_fly_runtime_aliases=True, scope_name=name)
        scopes.append(name or "runtime")
        if result.get("errors"):
            incomplete.append({"scope": name or "runtime", "errors": result["errors"]})
        for row in result.get("retained", []):
            if row.get("reason") != CANDIDATE_REASON:
                continue
            bucket = categories.setdefault(row["category"], {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += int(row.get("size_bytes") or 0)
            if protected(row["path"]):
                violations.append(row["path"])
    pointers = sorted(p.parent.name for p in (root / GENERATION_POINTERS).glob("*/ACTIVE.json"))
    gates = execute_gates(root)
    ok = not violations and not incomplete and gates["ok"]
    return {"schema": SCHEMA, "mode": "plan", "read_only": True, "ok": ok, "runtime_root": str(root),
            "execute_gates": gates,
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
    return {"identity": identity, "recovery": recovery, "auxiliary": auxiliary, "failures": failures}


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
