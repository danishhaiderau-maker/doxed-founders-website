#!/usr/bin/env python3
"""Shallow volume inspect + optional safe residual purge (paper-safe).

Targets post-wipe junk that still sits under /app/data/runtime and would
enter the next inventory CURRENT (notably research_reset_receipts).

Safe boundaries:
- Never arms Bitfinex.
- Keeps research_reset_receipts/ACTIVE_RESET.json.
- Does not delete runtime/v3 ledgers (may be live epoch evidence).
- Optional purge of bulky operational logs + tmp files + large
  cancellation_evidence_handoffs.jsonl leftover.

Env:
  DRY_RUN=true|false (default true)
  PURGE_RESIDUAL=true|false (default false)
  EXPECTED_EPOCH=epoch-...
  DATA_ROOT=/app/data
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
EXPECTED_EPOCH = os.environ.get("EXPECTED_EPOCH", "").strip()
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
PURGE = os.environ.get("PURGE_RESIDUAL", "false").strip().lower() in {"1", "true", "yes"}


def deep_stats(path: Path) -> dict:
    files = 0
    bytes_ = 0
    if not path.exists():
        return {"exists": False, "files": 0, "bytes": 0, "mb": 0.0}
    if path.is_file():
        try:
            sz = path.stat().st_size
            return {"exists": True, "files": 1, "bytes": sz, "mb": round(sz / 1048576, 2)}
        except OSError:
            return {"exists": True, "files": 0, "bytes": 0, "mb": 0.0}
    for root, _dns, fns in os.walk(path, followlinks=False):
        for name in fns:
            files += 1
            try:
                bytes_ += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return {"exists": True, "files": files, "bytes": bytes_, "mb": round(bytes_ / 1048576, 2)}


def fs_used_mb():
    try:
        st = os.statvfs(str(DATA_ROOT))
        return round((st.f_blocks - st.f_bfree) * st.f_frsize / 1048576, 1)
    except Exception:
        return None


def children_stats(path: Path, limit: int = 40) -> list:
    rows = []
    if not path.is_dir():
        return rows
    for child in path.iterdir():
        rows.append({"path": child.name, **deep_stats(child)})
    rows.sort(key=lambda r: -int(r.get("bytes") or 0))
    return rows[:limit]


def rm_path(path: Path, actions: list, errors: list) -> None:
    st = deep_stats(path)
    try:
        rel = str(path.relative_to(DATA_ROOT))
    except ValueError:
        rel = str(path)
    actions.append({"action": "DELETE", "path": rel, **st, "dry_run": DRY_RUN})
    if DRY_RUN or not path.exists():
        return
    try:
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path)
    except Exception as exc:
        errors.append(f"{path}:{type(exc).__name__}:{exc}")


def main() -> int:
    before = fs_used_mb()
    report = {
        "schema": "fly_volume_residual_inspect_purge_v1",
        "expected_epoch": EXPECTED_EPOCH or None,
        "dry_run": DRY_RUN,
        "purge": PURGE,
        "before_mb": before,
        "ts": time.time(),
        "top": [],
        "runtime_hotspots": [],
        "reset_receipts": [],
        "v3_hotspots": [],
        "actions": [],
        "errors": [],
    }

    runtime = DATA_ROOT / "runtime"
    if DATA_ROOT.is_dir():
        for child in DATA_ROOT.iterdir():
            report["top"].append({"path": child.name, **deep_stats(child)})
        report["top"].sort(key=lambda r: -int(r.get("bytes") or 0))

        if runtime.is_dir():
            report["runtime_hotspots"] = children_stats(runtime, 40)
            report["reset_receipts"] = children_stats(runtime / "research_reset_receipts", 50)
            report["v3_hotspots"] = children_stats(runtime / "v3", 40)

        if PURGE:
            rr = runtime / "research_reset_receipts"
            if rr.is_dir():
                for child in list(rr.iterdir()):
                    if child.name == "ACTIVE_RESET.json":
                        continue
                    rm_path(child, report["actions"], report["errors"])

            log_names = (
                "bot_runtime.log",
                "near_edge.log",
                "signal_persist.log",
                "bot.log",
                "relay-state-pusher.log",
                "relay-state-pusher-stdlib.log",
            )
            for name in log_names:
                for base in (DATA_ROOT, runtime, DATA_ROOT / "logs"):
                    if not base.exists() or not base.is_dir():
                        continue
                    for child in list(base.iterdir()):
                        if not child.is_file():
                            continue
                        if child.name == name or child.name.startswith(name + "."):
                            rm_path(child, report["actions"], report["errors"])

            if runtime.is_dir():
                for child in list(runtime.iterdir()):
                    n = child.name
                    if (
                        n.endswith(".tmp")
                        or n.startswith(".paper_lifecycle_v1.json.")
                        or n.startswith("paper_lifecycle_v1.json.")
                        or n.startswith(".research_events_v22.provisional.json.")
                    ):
                        rm_path(child, report["actions"], report["errors"])
                # Old wipe epoch quarantine under runtime (not live research).
                eq = runtime / "epoch_quarantine"
                if eq.exists():
                    rm_path(eq, report["actions"], report["errors"])

            ce = runtime / "cancellation_evidence_handoffs.jsonl"
            if ce.is_file():
                try:
                    if ce.stat().st_size > 5 * 1048576:
                        rm_path(ce, report["actions"], report["errors"])
                except OSError:
                    pass

            # Explicit quarantine copies under v3 (name contains quarantine).
            v3 = runtime / "v3"
            if v3.is_dir():
                for child in list(v3.iterdir()):
                    if "quarantine" in child.name.lower():
                        rm_path(child, report["actions"], report["errors"])
                # Pre-wipe signal snapshot tree survives wipe and alone exceeds the
                # 80 MiB CURRENT soft-cap. Fresh epoch can rebuild; keep ledgers/WAL.
                snaps_v1 = v3 / "signal_snapshots_v1"
                if snaps_v1.exists():
                    rm_path(snaps_v1, report["actions"], report["errors"])
                # Rebuildable transfer bundle caches (not live ledgers).
                for name in ("lifecycle_transfer_bundles", "lifecycle_bundle_index"):
                    p = v3 / name
                    if p.exists():
                        rm_path(p, report["actions"], report["errors"])

            # Analyzer generation caches at volume root (not sealed research).
            ag = DATA_ROOT / "analyzer_generations"
            if ag.exists():
                rm_path(ag, report["actions"], report["errors"])

            snaps = DATA_ROOT / ".data-sync-snapshots"
            if snaps.is_dir():
                for child in list(snaps.iterdir()):
                    rm_path(child, report["actions"], report["errors"])

            # Root leftover relay evidence projection (not sealed research cohort).
            for name in ("relay_lifecycle_evidence_v1.json",):
                p = DATA_ROOT / name
                if p.is_file():
                    try:
                        if p.stat().st_size > 2 * 1048576:
                            rm_path(p, report["actions"], report["errors"])
                    except OSError:
                        pass

    report["after_mb"] = fs_used_mb()
    report["deleted_mb_planned"] = round(
        sum(int(a.get("bytes") or 0) for a in report["actions"]) / 1048576, 2
    )
    report["action_count"] = len(report["actions"])
    # One-line JSON so GH logs stay parseable.
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
