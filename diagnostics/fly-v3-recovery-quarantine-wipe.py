#!/usr/bin/env python3
"""Inspect/quarantine V3 blockers that abort research wipe at BOUNDARY.

Deployed wipe fails with RESET_RECOVERY_NOT_PROVEN_CLEAR when
research_reset_recovery_audit finds blockers — commonly:
  - emergency_record_idempotency_v1/append_heads/*.json (APPEND_HEAD_PENDING)
  - mismatched emergency_record_idempotency_v1/*/complete.json (UNKNOWN rev)

Safe defaults: DRY_RUN=1. Rename-only quarantine under
v3/receipts/authority_identity_quarantine_v1/<ts>/. Never arms live.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

RUNTIME = Path("/app/data/runtime")
RECEIPT = RUNTIME / "v3" / "receipts"
QUAR_ROOT = RECEIPT / "authority_identity_quarantine_v1"


def _load_json(path: Path):
    try:
        row = json.loads(path.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(row, dict):
        return None, "NOT_OBJECT"
    return row, None


def _identity_of(row):
    if not isinstance(row, dict):
        return None
    ident = row.get("identity")
    return ident if isinstance(ident, dict) else None


def _ident_key(ident):
    if not isinstance(ident, dict):
        return "NO_IDENTITY"
    return "|".join(
        str(ident.get(k) or "")
        for k in ("epoch_id", "source_revision", "deployed_revision", "tile_config_signature")
    )


def _bad_identity(ident):
    if not isinstance(ident, dict):
        return True
    for k in ("epoch_id", "source_revision", "deployed_revision", "tile_config_signature"):
        v = str(ident.get(k) or "").strip()
        if not v or v.upper() in {"UNKNOWN", "UNAVAILABLE", "NOT_DEPLOYED_LOCAL"}:
            return True
    return False


def _candidates():
    out = []
    gen = RECEIPT / "ledger_generations_v1"
    if gen.is_dir():
        for p in sorted(gen.glob("*/ACTIVE.json")):
            out.append(p)
    emerg = RECEIPT / "emergency_record_idempotency_v1"
    if emerg.is_dir():
        for p in sorted(emerg.glob("*/complete.json")):
            out.append(p)
        heads = emerg / "append_heads"
        if heads.is_dir():
            for p in sorted(heads.glob("*.json")):
                out.append(p)
    auth = RECEIPT / "transactional_record_authority_v1" / "ACTIVE.json"
    if auth.is_file():
        out.append(auth)
    active_reset = RUNTIME / "research_reset_receipts" / "ACTIVE_RESET.json"
    if active_reset.is_file():
        out.append(active_reset)
    return out


def main() -> int:
    expected_epoch = str(os.environ.get("EXPECTED_EPOCH") or "").strip()
    dry = str(os.environ.get("DRY_RUN") or "true").strip().lower() not in {
        "0", "false", "no",
    }
    report = {
        "schema": "fly_v3_recovery_quarantine_v2",
        "dry_run": dry,
        "expected_epoch": expected_epoch or None,
        "ts": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        "runtime": str(RUNTIME),
        "candidates": [],
        "quarantine_actions": [],
        "verify": {},
    }

    if not RUNTIME.is_dir():
        report["error"] = "RUNTIME_MISSING"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    rows = []
    for path in _candidates():
        row, err = _load_json(path)
        ident = _identity_of(row)
        try:
            rel = str(path.relative_to(RUNTIME))
        except ValueError:
            rel = str(path)
        entry = {
            "path": rel,
            "bytes": path.stat().st_size if path.exists() else 0,
            "error": err,
            "identity": ident,
            "ident_key": _ident_key(ident),
            "bad_identity": _bad_identity(ident),
            "kind": path.name,
        }
        if path.name == "ACTIVE_RESET.json" and isinstance(row, dict):
            entry["reset_state"] = row.get("state") or row.get("status")
            entry["operation_id"] = row.get("operation_id") or row.get("attempt_id")
        rows.append(entry)
        report["candidates"].append(entry)

    counts = Counter(r["ident_key"] for r in rows if r["kind"] in {"ACTIVE.json", "complete.json"})
    report["identity_counts"] = dict(counts)

    keep_key = None
    good = [
        r for r in rows
        if r["kind"] in {"ACTIVE.json", "complete.json"}
        and not r["bad_identity"]
        and isinstance(r.get("identity"), dict)
        and (not expected_epoch or r["identity"].get("epoch_id") == expected_epoch)
    ]
    if good:
        keep_key = Counter(r["ident_key"] for r in good).most_common(1)[0][0]
    elif counts:
        for key, _n in counts.most_common():
            if "UNKNOWN" not in key and key != "NO_IDENTITY":
                keep_key = key
                break
        if keep_key is None:
            keep_key = counts.most_common(1)[0][0]
    report["keep_ident_key"] = keep_key

    ts = report["ts"]
    quar_dir = QUAR_ROOT / ts
    actions = []
    for r in rows:
        rel = r["path"]
        path = RUNTIME / rel
        if not path.exists():
            continue
        demote = False
        reason = None
        if "append_heads" in rel.replace("\\", "/"):
            demote = True
            reason = "APPEND_HEAD_PENDING_BLOCKS_WIPE"
        elif r["kind"] in {"ACTIVE.json", "complete.json"}:
            if r["bad_identity"]:
                demote = True
                reason = "BAD_OR_UNKNOWN_IDENTITY"
            elif keep_key and r["ident_key"] != keep_key:
                demote = True
                reason = "IDENTITY_CONFLICT_VS_MODAL"
            elif expected_epoch and isinstance(r.get("identity"), dict):
                if r["identity"].get("epoch_id") != expected_epoch:
                    demote = True
                    reason = "EPOCH_MISMATCH"
        elif r["kind"] == "ACTIVE_RESET.json":
            state = str(r.get("reset_state") or "").upper()
            if state and state not in {"COMPLETE", "COMPLETED", "OK"}:
                demote = True
                reason = f"STUCK_ACTIVE_RESET_{state or 'UNKNOWN'}"
        if not demote:
            continue
        safe_name = rel.replace("/", "__").replace("\\", "__")
        dest = quar_dir / ("%02d__%s" % (len(actions), safe_name))
        action = {
            "from": rel,
            "to": str(dest.relative_to(RUNTIME)),
            "reason": reason,
            "ident_key": r["ident_key"],
            "dry_run": dry,
        }
        actions.append(action)
        if not dry:
            quar_dir.mkdir(parents=True, exist_ok=True)
            path.rename(dest)
    report["quarantine_actions"] = actions

    sys.path.insert(0, "/app")
    try:
        import research_v3_store  # type: ignore
        store = research_v3_store.V3EvidenceStore.open_read_only(str(RUNTIME))
        report["verify"]["open_read_only"] = {
            "ok": True,
            "epoch": getattr(store, "epoch_id", None),
        }
        try:
            from research_reset_recovery_audit import audit_research_reset_recovery  # type: ignore
            identity = store._identity_binding() if hasattr(store, "_identity_binding") else None
            if not isinstance(identity, dict):
                identity = getattr(store, "_read_identity_override", None)
            if isinstance(identity, dict):
                audit = audit_research_reset_recovery(RUNTIME, expected_identity=identity)
                report["verify"]["recovery_audit"] = {
                    "complete": audit.get("complete"),
                    "safe_for_reset_recovery_scope": audit.get("safe_for_reset_recovery_scope"),
                    "pending_or_unknown_count": audit.get("pending_or_unknown_count"),
                    "blockers": (audit.get("blockers") or [])[:20],
                }
        except Exception:
            report["verify"]["recovery_audit_error"] = traceback.format_exc()[-2000:]
    except Exception:
        report["verify"]["open_read_only"] = {
            "ok": False,
            "error": traceback.format_exc()[-2000:],
        }

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
