#!/usr/bin/env python3
"""True-flat maintenance: purge bulky non-research logs + persist ADMIN_MANUAL.

Safe boundaries:
- Never arms Bitfinex / never touches live credentials.
- Deletes bot_runtime.log* and other bulky debug/runtime log weight under /app/data.
- Persists manual_admin_pause=true so a machine restart loads true flat.
- Does NOT wipe sealed research receipts wholesale (use wipe_fly_only after flat).

Env:
  DRY_RUN=true|false (default true)
  DATA_ROOT=/app/data
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCHEMA = "fly_trueflat_log_purge_v1"
DRY_RUN = str(os.getenv("DRY_RUN", "true")).strip().lower() in {"1", "true", "yes"}
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/app/data")).resolve()

DELETE_NAME_PREFIXES = (
    "bot_runtime.log",
    "dashboard_busy",
    "flask_access",
    "gunicorn",
    "debug_export",
)


def _fs_used_mb(root: Path):
    try:
        st = os.statvfs(str(root))
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        return round(used / (1024 * 1024), 1)
    except Exception:
        return None


def _should_delete(name: str) -> bool:
    if name == "bot_runtime.log" or name.startswith("bot_runtime.log."):
        return True
    for prefix in DELETE_NAME_PREFIXES:
        if prefix == "bot_runtime.log":
            continue
        if name == prefix or name.startswith(prefix + ".") or name.startswith(prefix):
            return True
    return False


def _find_targets() -> list[Path]:
    targets: list[Path] = []
    if not DATA_ROOT.is_dir():
        return targets
    search_roots = [
        DATA_ROOT,
        DATA_ROOT / "runtime",
        DATA_ROOT / "logs",
        DATA_ROOT / "debug",
        DATA_ROOT / "exports",
    ]
    seen: set[str] = set()
    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                if not child.is_file():
                    continue
                if not _should_delete(child.name):
                    continue
                key = str(child.resolve())
                if key in seen:
                    continue
                seen.add(key)
                targets.append(child)
        except OSError:
            continue
    return sorted(targets, key=lambda p: str(p))


def _config_paths() -> list[Path]:
    candidates = [
        Path("/app/config-7002.json"),
        Path("/app/config.json"),
        DATA_ROOT / "config-7002.json",
        DATA_ROOT / "config.json",
        DATA_ROOT / "runtime" / "config.json",
        DATA_ROOT / "persistent_config.json",
        DATA_ROOT / "bot_config.json",
    ]
    return [p for p in candidates if p.is_file()]


def _persist_manual_pause(actions: list, errors: list) -> None:
    for path in _config_paths():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"config_read:{path}:{type(exc).__name__}")
            continue
        if not isinstance(raw, dict):
            continue
        before = bool(raw.get("manual_admin_pause"))
        actions.append({
            "action": "PERSIST_MANUAL_ADMIN_PAUSE",
            "path": str(path),
            "before": before,
            "after": True,
            "dry_run": DRY_RUN,
        })
        if DRY_RUN:
            continue
        raw["manual_admin_pause"] = True
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def main() -> int:
    before_mb = _fs_used_mb(DATA_ROOT)
    actions: list = []
    errors: list = []
    deleted_files = 0
    deleted_bytes = 0
    targets = _find_targets()
    for path in targets:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        rel = str(path.relative_to(DATA_ROOT)) if str(path).startswith(str(DATA_ROOT)) else str(path)
        actions.append({
            "action": "DELETE_LOG",
            "path": rel,
            "bytes": size,
            "dry_run": DRY_RUN,
        })
        if DRY_RUN:
            deleted_files += 1
            deleted_bytes += size
            continue
        try:
            path.unlink(missing_ok=True)
            deleted_files += 1
            deleted_bytes += size
        except Exception as exc:
            errors.append(f"{path}:{type(exc).__name__}:{exc}")

    snaps = DATA_ROOT / ".data-sync-snapshots"
    if snaps.is_dir():
        snap_files = 0
        snap_bytes = 0
        for dirpath, _dns, fns in os.walk(snaps, followlinks=False):
            for name in fns:
                fp = Path(dirpath) / name
                try:
                    snap_bytes += fp.stat().st_size
                    snap_files += 1
                except OSError:
                    pass
        actions.append({
            "action": "CLEAR_SNAPSHOTS",
            "path": ".data-sync-snapshots",
            "files": snap_files,
            "bytes": snap_bytes,
            "dry_run": DRY_RUN,
        })
        if not DRY_RUN:
            for dirpath, dirnames, filenames in os.walk(snaps, topdown=False, followlinks=False):
                base = Path(dirpath)
                for name in filenames:
                    try:
                        p = base / name
                        size = p.stat().st_size
                        p.unlink(missing_ok=True)
                        deleted_files += 1
                        deleted_bytes += size
                    except Exception as exc:
                        errors.append(f"snap:{base / name}:{type(exc).__name__}")
                if base.resolve() != snaps.resolve():
                    try:
                        base.rmdir()
                    except OSError:
                        pass

    _persist_manual_pause(actions, errors)
    after_mb = _fs_used_mb(DATA_ROOT)
    report = {
        "ok": len(errors) == 0,
        "schema": SCHEMA,
        "dry_run": DRY_RUN,
        "data_root": str(DATA_ROOT),
        "before_mb": before_mb,
        "after_mb": after_mb,
        "deleted_files": deleted_files,
        "deleted_bytes": deleted_bytes,
        "deleted_mb": round(deleted_bytes / (1024 * 1024), 2),
        "target_count": len(targets),
        "actions": actions[:200],
        "action_count": len(actions),
        "errors": errors[:40],
        "note": (
            "Persisted manual_admin_pause=true when config found. "
            "Restart machine so in-memory ADMIN_MANUAL true-flat loads."
        ),
        "ts": time.time(),
    }
    print(json.dumps(report, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
