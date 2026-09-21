#!/usr/bin/env python3
"""Inspect/quarantine V3 read-authority identity conflicts on Fly volume.

Deployed open_read_only() unions:
  - v3/receipts/ledger_generations_v1/*/ACTIVE.json
  - v3/receipts/emergency_record_idempotency_v1/*/complete.json
  - v3/receipts/transactional_record_authority_v1/ACTIVE.json

A prior inspect that only rglob'd ACTIVE.json missed complete.json conflicts.
This script mirrors the deployed candidate set, then optionally quarantines
non-chosen identity pointers so open_read_only succeeds. Never arms live.
Never deletes accounting/WAL; quarantines by rename under receipts quarantine.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

RUNTIME = Path("/app/data/runtime")
RECEIPT = RUNTIME / "v3" / "receipts"
DRY_RUN = str(os.environ.get("DRY_RUN", "true")).strip().lower() in {"1", "true", "yes"}
EXPECTED_EPOCH = str(os.environ.get("EXPECTED_EPOCH") or "").strip()
DEPLOYED_REV = str(os.environ.get("SOURCE_GIT_REV") or "").strip()


def _candidates() -> list[Path]:
    out: list[Path] = []
    out.extend(sorted((RECEIPT / "ledger_generations_v1").glob("*/ACTIVE.json")))
    out.extend(sorted((RECEIPT / "emergency_record_idempotency_v1").glob("*/complete.json")))
    marker = RECEIPT / "transactional_record_authority_v1" / "ACTIVE.json"
    if marker.exists() or marker.is_symlink():
        out.append(marker)
    return out


def _load(path: Path) -> dict:
    try:
        row = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"path": str(path), "error": f"{type(exc).__name__}:{exc}"}
    ident = row.get("identity") if isinstance(row, dict) else None
    return {
        "path": str(path.relative_to(RUNTIME)).replace("\\", "/"),
        "abs": str(path),
        "bytes": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "schema": row.get("schema") if isinstance(row, dict) else None,
        "identity": ident if isinstance(ident, dict) else None,
        "epoch": (ident or {}).get("epoch_id") if isinstance(ident, dict) else None,
        "source_revision": (ident or {}).get("source_revision") if isinstance(ident, dict) else None,
        "deployed_revision": (ident or {}).get("deployed_revision") if isinstance(ident, dict) else None,
        "tile": (
            str((ident or {}).get("tile_config_signature") or "")[:16]
            if isinstance(ident, dict)
            else None
        ),
    }


def _identity_key(ident: dict | None) -> str | None:
    if not isinstance(ident, dict):
        return None
    return json.dumps(ident, sort_keys=True, separators=(",", ":"))


def _choose_identity(rows: list[dict]) -> dict | None:
    """Prefer EXPECTED_EPOCH + deployed revision; else EXPECTED_EPOCH majority; else None."""
    with_ident = [r for r in rows if isinstance(r.get("identity"), dict)]
    if not with_ident:
        return None
    epoch_rows = (
        [r for r in with_ident if r.get("epoch") == EXPECTED_EPOCH]
        if EXPECTED_EPOCH
        else list(with_ident)
    )
    pool = epoch_rows or with_ident
    if DEPLOYED_REV:
        rev_hits = [
            r
            for r in pool
            if str(r.get("source_revision") or "").startswith(DEPLOYED_REV[:12])
            or str(r.get("deployed_revision") or "").startswith(DEPLOYED_REV[:12])
            or str(r.get("source_revision") or "") == DEPLOYED_REV
            or str(r.get("deployed_revision") or "") == DEPLOYED_REV
        ]
        if rev_hits:
            pool = rev_hits
    # majority by full identity json
    counts: dict[str, int] = {}
    by_key: dict[str, dict] = {}
    for r in pool:
        key = _identity_key(r["identity"])
        assert key is not None
        counts[key] = counts.get(key, 0) + 1
        by_key[key] = r["identity"]
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return dict(by_key[best])


def _quarantine(path: Path, reason: str) -> dict:
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    qdir = RECEIPT / "authority_identity_quarantine_v1" / ts
    qdir.mkdir(parents=True, exist_ok=True)
    rel = path.relative_to(RECEIPT)
    dest = qdir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if DRY_RUN:
        return {
            "action": "WOULD_QUARANTINE",
            "from": str(path.relative_to(RUNTIME)).replace("\\", "/"),
            "to": str(dest.relative_to(RUNTIME)).replace("\\", "/"),
            "reason": reason,
            "dry_run": True,
        }
    # rename keeps inode content; never unlink
    path.replace(dest)
    meta = {
        "schema": "v3_authority_identity_quarantine_v1",
        "quarantined_at": ts,
        "reason": reason,
        "expected_epoch": EXPECTED_EPOCH or None,
        "deployed_revision": DEPLOYED_REV or None,
        "original_rel": str(rel).replace("\\", "/"),
    }
    (dest.parent / (dest.name + ".quarantine.json")).write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    return {
        "action": "QUARANTINED",
        "from": str(rel).replace("\\", "/"),
        "to": str(dest.relative_to(RUNTIME)).replace("\\", "/"),
        "reason": reason,
        "dry_run": False,
    }


def _verify_open() -> dict:
    sys.path.insert(0, "/app")
    import research_v3_store  # noqa: WPS433

    try:
        store = research_v3_store.V3EvidenceStore.open_read_only(str(RUNTIME))
        return {
            "ok": True,
            "epoch": store.epoch_id,
            "identity": getattr(store, "_read_identity_override", None),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}


def main() -> int:
    if not RUNTIME.is_dir():
        print(json.dumps({"ok": False, "error": "RUNTIME_MISSING", "runtime": str(RUNTIME)}))
        return 2
    paths = _candidates()
    rows = [_load(p) for p in paths]
    chosen = _choose_identity(rows)
    chosen_key = _identity_key(chosen)
    actions = []
    for row, path in zip(rows, paths):
        if row.get("error"):
            actions.append(_quarantine(path, "UNREADABLE_AUTHORITY_POINTER"))
            continue
        ident = row.get("identity")
        if not isinstance(ident, dict):
            # non-identity complete/ACTIVE does not participate in override conflict
            continue
        key = _identity_key(ident)
        if EXPECTED_EPOCH and row.get("epoch") != EXPECTED_EPOCH:
            actions.append(_quarantine(path, "EPOCH_MISMATCH_VS_EXPECTED"))
            continue
        if chosen_key is not None and key != chosen_key:
            actions.append(_quarantine(path, "IDENTITY_DICT_MISMATCH_VS_CHOSEN"))
            continue
    verify = _verify_open()
    # If still conflicting after epoch/chosen filter (e.g. chosen None but multiple),
    # quarantine every identity-bearing candidate so LEGACY wipe boundary can bind.
    if verify.get("ok") is not True and not DRY_RUN:
        for row, path in zip([_load(p) for p in _candidates()], _candidates()):
            if isinstance(row.get("identity"), dict):
                actions.append(_quarantine(path, "FORCE_CLEAR_FOR_WIPE_BOUNDARY"))
        verify = _verify_open()
    elif verify.get("ok") is not True and DRY_RUN and chosen_key is None:
        for row, path in zip(rows, paths):
            if isinstance(row.get("identity"), dict):
                actions.append(
                    {
                        "action": "WOULD_QUARANTINE",
                        "from": row["path"],
                        "reason": "FORCE_CLEAR_FOR_WIPE_BOUNDARY",
                        "dry_run": True,
                    }
                )

    report = {
        "ok": bool(verify.get("ok")),
        "dry_run": DRY_RUN,
        "expected_epoch": EXPECTED_EPOCH or None,
        "deployed_revision_env": DEPLOYED_REV or None,
        "candidate_count": len(rows),
        "candidates": rows,
        "chosen_identity": chosen,
        "quarantine_actions": actions,
        "verify_open_read_only": verify,
        "remaining_active_count": len(_candidates()) if not DRY_RUN else None,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] or DRY_RUN else 1


if __name__ == "__main__":
    raise SystemExit(main())

# --- wipe progress (read-only) ---
try:
    active = RUNTIME / "research_reset_receipts" / "ACTIVE_RESET.json"
    wipe = {"active_reset_exists": active.exists()}
    if active.exists():
        wipe["active_reset"] = json.loads(active.read_text("utf-8"))
        rid = wipe["active_reset"].get("reset_id")
        if rid:
            op = RUNTIME / "research_reset_receipts" / rid / "operation.json"
            if op.exists():
                opj = json.loads(op.read_text("utf-8"))
                wipe["operation_stage"] = opj.get("stage")
                wipe["operation_keys"] = sorted(opj.keys())
                wipe["payload_copy_performed"] = opj.get("payload_copy_performed")
    diag = RUNTIME / "research_reset_diagnostics"
    if diag.exists():
        files = sorted(diag.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        wipe["recent_diagnostics"] = []
        for p in files:
            try:
                row = json.loads(p.read_text("utf-8"))
                wipe["recent_diagnostics"].append({
                    "path": str(p.relative_to(RUNTIME)).replace("\\", "/"),
                    "stage": row.get("stage"),
                    "status": row.get("status"),
                })
            except Exception as exc:
                wipe["recent_diagnostics"].append({"path": str(p), "error": str(exc)})
    print("===WIPE_PROGRESS===")
    print(json.dumps(wipe, indent=2, sort_keys=True, default=str))
except Exception:
    traceback.print_exc()
