#!/usr/bin/env python3
"""One-shot Fly volume ACTIVE.json identity reconcile (rename-only quarantine).

Preserves accounting/WAL. Never arms live trading. Intended for research wipe
unblock when open_read_only raises V3 read authority identity conflict.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

RUNTIME = Path("/app/data/runtime")
GEN_ROOT = RUNTIME / "v3" / "receipts" / "ledger_generations_v1"


def main() -> int:
    expected_epoch = str(os.environ.get("EXPECTED_EPOCH") or "").strip()
    dry = str(os.environ.get("DRY_RUN") or "true").strip().lower() == "true"
    if not expected_epoch.startswith("epoch-"):
        print(json.dumps({"ok": False, "error": "EXPECTED_EPOCH_REQUIRED"}))
        return 2

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rows = []
    for path in sorted(GEN_ROOT.glob("*/ACTIVE.json")):
        row = {"ledger": path.parent.name, "path": str(path), "keep": False}
        try:
            payload = json.loads(path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001 - surface exact failure
            row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            continue
        identity = payload.get("identity") if isinstance(payload, dict) else None
        if isinstance(identity, dict):
            row["identity"] = identity
            row["epoch"] = identity.get("epoch_id")
            row["source_revision"] = identity.get("source_revision")
            row["deployed_revision"] = identity.get("deployed_revision")
            row["tile_config_signature"] = str(identity.get("tile_config_signature") or "")[:16]
            row["keep"] = str(identity.get("epoch_id") or "") == expected_epoch
        else:
            row["error"] = "MISSING_IDENTITY"
        rows.append(row)

    keeper_blobs = [
        json.dumps(row["identity"], sort_keys=True, separators=(",", ":"))
        for row in rows
        if row.get("keep") and isinstance(row.get("identity"), dict)
    ]
    chosen = Counter(keeper_blobs).most_common(1)[0][0] if keeper_blobs else None
    if chosen:
        for row in rows:
            if not row.get("keep"):
                continue
            blob = json.dumps(row.get("identity") or {}, sort_keys=True, separators=(",", ":"))
            if blob != chosen:
                row["keep"] = False
                row["demote_reason"] = "NON_MODAL_IDENTITY_SAME_EPOCH"

    actions = []
    if not dry:
        qroot = RUNTIME / "v3" / "receipts" / "authority_identity_quarantine_v1" / stamp
        qroot.mkdir(parents=True, exist_ok=True)
        for row in rows:
            if row.get("keep"):
                continue
            src = Path(row["path"])
            if not src.is_file():
                continue
            dest = qroot / f"{row['ledger']}__ACTIVE.json"
            src.rename(dest)
            meta = Path(str(dest) + ".quarantine.json")
            meta.write_text(
                json.dumps(
                    {
                        "schema": "v3_active_identity_quarantine_v1",
                        "moved_from": str(src),
                        "moved_to": str(dest),
                        "reason": "IDENTITY_CONFLICT_RECONCILE",
                        "expected_epoch": expected_epoch,
                        "row": row,
                        "at": stamp,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                "utf-8",
            )
            actions.append({"ledger": row["ledger"], "from": str(src), "to": str(dest)})

    verify = {"ok": False}
    try:
        sys.path.insert(0, "/app")
        from research_v3_store import V3EvidenceStore  # type: ignore

        store = V3EvidenceStore.open_read_only(str(RUNTIME))
        verify = {
            "ok": True,
            "epoch_id": store.epoch_id,
            "identity": getattr(store, "_read_identity_override", None),
        }
    except Exception as exc:  # noqa: BLE001
        verify = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    session_epoch = None
    try:
        session_path = RUNTIME / "research_session.json"
        if session_path.is_file():
            session = json.loads(session_path.read_text("utf-8"))
            if isinstance(session, dict):
                session_epoch = session.get("collector_v22_epoch_id")
    except Exception as exc:  # noqa: BLE001
        session_epoch = f"ERROR:{type(exc).__name__}"

    print(
        json.dumps(
            {
                "ok": verify.get("ok") is True,
                "dry_run": dry,
                "expected_epoch": expected_epoch,
                "active_rows": rows,
                "chosen_identity_json": chosen,
                "quarantine_actions": actions,
                "verify_open_read_only": verify,
                "session_epoch": session_epoch,
                "remaining_active_count": sum(1 for row in rows if row.get("keep"))
                if dry
                else len(list(GEN_ROOT.glob("*/ACTIVE.json"))),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if verify.get("ok") is True or dry else 1


if __name__ == "__main__":
    raise SystemExit(main())
