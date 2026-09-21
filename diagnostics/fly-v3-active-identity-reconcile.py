#!/usr/bin/env python3
"""Ops entrypoint for fly-v3-authority-reconcile-once (Fly SSH).

EXPECTED_EPOCH / OPS_MODE:
  SIZE | SHALLOW | SHALLOW_SIZE  -> shallow volume probe
  BOOTSTRAP | RECOVERY           -> quarantine stale recovery-state
  anything else (incl. epoch-*)  -> physical postwipe purge v2

DRY_RUN=true|false from workflow input.
Never arms Bitfinex. Never deletes ACTIVE identity / append_heads / wipe receipts.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

MODE = (os.getenv("OPS_MODE") or os.getenv("EXPECTED_EPOCH") or "PURGE").strip().upper()
DRY_RUN = str(os.getenv("DRY_RUN", "true")).strip().lower() in {"1", "true", "yes"}
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/app/data")).resolve()


def _fs_used_mb(root: Path):
    try:
        st = os.statvfs(str(root))
        return round((st.f_blocks - st.f_bfree) * st.f_frsize / (1024 * 1024), 1)
    except Exception:
        return None


def _dir_size(path: Path):
    files = 0
    bytes_ = 0
    if not path.exists():
        return 0, 0
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 1, 0
    for dp, _dns, fns in os.walk(path, followlinks=False):
        for name in fns:
            files += 1
            try:
                bytes_ += (Path(dp) / name).stat().st_size
            except OSError:
                pass
    return files, bytes_


def run_size() -> int:
    if not DATA_ROOT.is_dir():
        print(json.dumps({"ok": False, "error": f"missing {DATA_ROOT}"}))
        return 2
    used_mb = _fs_used_mb(DATA_ROOT)
    try:
        st = os.statvfs(str(DATA_ROOT))
        free_mb = round(st.f_bfree * st.f_frsize / (1024 * 1024), 1)
        total_mb = round(st.f_blocks * st.f_frsize / (1024 * 1024), 1)
    except Exception:
        free_mb = total_mb = None
    top = []
    for child in sorted(DATA_ROOT.iterdir(), key=lambda p: p.name):
        files = 0
        bytes_ = 0
        if child.is_file():
            files = 1
            try:
                bytes_ = child.stat().st_size
            except OSError:
                pass
        elif child.is_dir():
            for dp, dns, fns in os.walk(child, followlinks=False):
                rel_parts = Path(dp).relative_to(child).parts
                if len(rel_parts) > 1:
                    dns[:] = []
                for name in fns:
                    files += 1
                    try:
                        bytes_ += (Path(dp) / name).stat().st_size
                    except OSError:
                        pass
        top.append({"path": child.name, "files": files, "mb": round(bytes_ / (1024 * 1024), 2)})
    top.sort(key=lambda r: -r["mb"])
    print(json.dumps({
        "ok": True,
        "used_mb": used_mb,
        "free_mb": free_mb,
        "total_mb": total_mb,
        "top": top[:25],
    }, separators=(",", ":")))
    return 0


def run_bootstrap() -> int:
    actions = []
    errors = []
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    candidates = []
    runtime_v3 = DATA_ROOT / "runtime" / "v3"
    if runtime_v3.is_dir():
        for dirpath, dirnames, _fns in os.walk(runtime_v3):
            depth = Path(dirpath).relative_to(runtime_v3).parts
            if len(depth) > 4:
                dirnames[:] = []
                continue
            if "recovery-state" in dirnames:
                candidates.append(Path(dirpath) / "recovery-state")
    qroot = DATA_ROOT / "research_epoch_quarantine" / f"lifecycle-recovery-state-{stamp}"
    seen = set()
    for src in candidates:
        key = str(src.resolve()) if src.exists() else str(src)
        if key in seen or not src.exists():
            continue
        seen.add(key)
        dest = qroot / src.name
        n = 0
        while dest.exists():
            n += 1
            dest = qroot / f"{src.name}-{n}"
        actions.append({"action": "MOVE", "src": str(src), "dest": str(dest), "dry_run": DRY_RUN})
        if not DRY_RUN:
            try:
                qroot.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
            except Exception as exc:
                errors.append(f"{src}:{type(exc).__name__}:{exc}")
    print(json.dumps({
        "ok": len(errors) == 0,
        "schema": "fly_lifecycle_recovery_state_quarantine_v1",
        "dry_run": DRY_RUN,
        "actions": actions,
        "errors": errors,
        "note": "Moves stale recovery-state aside so receipt_bootstrap can COMPLETE. Never arms.",
    }, separators=(",", ":")))
    return 0 if not errors else 1


def _rm_tree(path: Path, actions, errors) -> None:
    files, bytes_ = _dir_size(path)
    try:
        rel = str(path.relative_to(DATA_ROOT))
    except Exception:
        rel = str(path)
    actions.append({
        "action": "DELETE_TREE",
        "path": rel,
        "files": files,
        "bytes": bytes_,
        "dry_run": DRY_RUN,
    })
    if DRY_RUN or not path.exists():
        return
    try:
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path)
    except Exception as exc:
        errors.append(f"{path}:{type(exc).__name__}:{exc}")


def run_purge() -> int:
    if not DATA_ROOT.is_dir():
        print(json.dumps({"ok": False, "error": f"missing {DATA_ROOT}"}))
        return 2
    before_mb = _fs_used_mb(DATA_ROOT)
    actions = []
    errors = []
    targets = []
    rq = DATA_ROOT / "research_epoch_quarantine"
    if rq.is_dir():
        for child in sorted(rq.iterdir()):
            name = child.name
            if (
                name.startswith("postwipe-retained-")
                or name.startswith("postwipe-")
                or name == "corrupt_evidence_quarantine"
                or name.startswith("lifecycle-recovery-state-")
            ):
                targets.append(child)
    snaps = DATA_ROOT / ".data-sync-snapshots"
    if snaps.is_dir():
        for child in list(snaps.iterdir()):
            _rm_tree(child, actions, errors)
    for rel in ("lifecycle_recovery_quarantine", "research_archive"):
        p = DATA_ROOT / rel
        if p.exists():
            targets.append(p)
    for t in targets:
        _rm_tree(t, actions, errors)
    after_mb = _fs_used_mb(DATA_ROOT)
    deleted_bytes = sum(int(a.get("bytes") or 0) for a in actions if a.get("action") == "DELETE_TREE")
    out = {
        "ok": len(errors) == 0,
        "schema": "fly_postwipe_retained_purge_v2",
        "dry_run": DRY_RUN,
        "data_root": str(DATA_ROOT),
        "before_mb": before_mb,
        "after_mb": after_mb,
        "deleted_bytes_planned": deleted_bytes,
        "deleted_mb_planned": round(deleted_bytes / (1024 * 1024), 1),
        "actions": actions,
        "errors": errors[:40],
        "note": (
            "Physical delete of postwipe quarantine + rebuildable snapshots. "
            "Keeps ACTIVE identity, wipe receipts, append_heads, live research. Never arms."
        ),
        "ts": int(time.time()),
        "mode": MODE,
    }
    print(json.dumps(out, separators=(",", ":")))
    return 0 if out["ok"] else 1


def main() -> int:
    print(f"OPS_MODE_RESOLVED={MODE} DRY_RUN={DRY_RUN} DATA_ROOT={DATA_ROOT}", flush=True)
    if MODE in {"SIZE", "SHALLOW", "SHALLOW_SIZE"}:
        return run_size()
    if MODE in {"BOOTSTRAP", "RECOVERY", "RECOVERY_STATE"}:
        return run_bootstrap()
    return run_purge()


if __name__ == "__main__":
    sys.exit(main())
