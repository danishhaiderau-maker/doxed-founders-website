#!/usr/bin/env python3
"""Inspect and quarantine V3 read-authority identity candidates on Fly."""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

RUNTIME = Path("/app/data/runtime")
RECEIPT = RUNTIME / "v3" / "receipts"


def _load_identity(path: Path):
    try:
        row = json.loads(path.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(row, dict):
        return None, "NOT_OBJECT"
    identity = row.get("identity")
    if isinstance(identity, dict):
        return identity, None
    return None, "MISSING_IDENTITY"


def collect_candidates():
    rows = []
    gen = RECEIPT / "ledger_generations_v1"
    if gen.exists():
        for path in sorted(gen.glob("*/ACTIVE.json")):
            ident, err = _load_identity(path)
            rows.append({"kind": "ledger_ACTIVE", "path": str(path), "identity": ident, "error": err})
    emer = RECEIPT / "emergency_record_idempotency_v1"
    if emer.exists():
        for path in sorted(emer.glob("*/complete.json")):
            ident, err = _load_identity(path)
            rows.append({"kind": "emergency_complete", "path": str(path), "identity": ident, "error": err})
    marker = RECEIPT / "transactional_record_authority_v1" / "ACTIVE.json"
    if marker.exists() or marker.is_symlink():
        ident, err = _load_identity(marker)
        rows.append({"kind": "transactional_ACTIVE", "path": str(marker), "identity": ident, "error": err,
                     "is_symlink": marker.is_symlink()})
    return rows


def main() -> int:
    expected_epoch = str(os.environ.get("EXPECTED_EPOCH") or "").strip()
    dry = str(os.environ.get("DRY_RUN") or "true").strip().lower() == "true"
    if not expected_epoch.startswith("epoch-"):
        print(json.dumps({"ok": False, "error": "EXPECTED_EPOCH_REQUIRED"}))
        return 2

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rows = collect_candidates()
    for row in rows:
        ident = row.get("identity")
        keep = isinstance(ident, dict) and str(ident.get("epoch_id") or "") == expected_epoch
        row["keep"] = keep
        if isinstance(ident, dict):
            row["epoch"] = ident.get("epoch_id")
            row["source_revision"] = ident.get("source_revision")
            row["deployed_revision"] = ident.get("deployed_revision")
            row["tile"] = str(ident.get("tile_config_signature") or "")[:16]

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

    # If nothing matches expected epoch but conflict exists, prefer quarantining
    # ALL identity-bearing candidates so open_read_only can use LEGACY session path.
    identity_bearing = [row for row in rows if isinstance(row.get("identity"), dict)]
    if not keeper_blobs and identity_bearing:
        for row in rows:
            if isinstance(row.get("identity"), dict):
                row["keep"] = False
                row["demote_reason"] = "NO_EXPECTED_EPOCH_MATCH_QUARANTINE_ALL"

    actions = []
    if not dry:
        qroot = RECEIPT / "authority_identity_quarantine_v1" / stamp
        qroot.mkdir(parents=True, exist_ok=True)
        for idx, row in enumerate(rows):
            if row.get("keep"):
                continue
            src = Path(row["path"])
            if not (src.exists() or src.is_symlink()):
                continue
            safe_name = f"{idx:02d}__{row['kind']}__{src.parent.name}__{src.name}"
            dest = qroot / safe_name
            src.rename(dest)
            meta = Path(str(dest) + ".quarantine.json")
            meta.write_text(
                json.dumps(
                    {
                        "schema": "v3_active_identity_quarantine_v1",
                        "moved_from": str(src),
                        "moved_to": str(dest),
                        "reason": row.get("demote_reason") or "IDENTITY_CONFLICT_RECONCILE",
                        "expected_epoch": expected_epoch,
                        "row": {k: v for k, v in row.items() if k != "identity"} | {
                            "identity": row.get("identity")
                        },
                        "at": stamp,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                "utf-8",
            )
            actions.append({"kind": row["kind"], "from": str(src), "to": str(dest)})

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

    summary_idents = []
    for row in rows:
        ident = row.get("identity")
        if isinstance(ident, dict):
            summary_idents.append({
                "kind": row["kind"],
                "path": row["path"],
                "keep": row.get("keep"),
                "epoch": ident.get("epoch_id"),
                "source_revision": str(ident.get("source_revision") or "")[:12],
                "tile": str(ident.get("tile_config_signature") or "")[:12],
                "demote_reason": row.get("demote_reason"),
            })

    print(
        json.dumps(
            {
                "ok": verify.get("ok") is True,
                "dry_run": dry,
                "expected_epoch": expected_epoch,
                "candidate_count": len(rows),
                "identity_summary": summary_idents,
                "chosen_identity_json": chosen,
                "quarantine_actions": actions,
                "verify_open_read_only": verify,
                "session_epoch": session_epoch,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if dry or verify.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
