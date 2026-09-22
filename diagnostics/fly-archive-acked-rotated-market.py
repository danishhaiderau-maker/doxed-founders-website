#!/usr/bin/env python3
"""Quarantine already-ACKed closed market rotations out of Fly invent.

Moves (never deletes) sealed closed rotations into research_archive/, which is
already invent-excluded. Active (unsuffixed) writers are never touched.

Safe boundaries:
- Never arms Bitfinex.
- Never touches sole watchers / paper toggles.
- Refuses move unless size + sha256 match the sealed ACK membership proof.
- Quarantine only; unique un-ACKed data stays in place.

Env:
  DRY_RUN=true|false (default true)
  APPLY=true|false (default false; must be true with DRY_RUN=false to move)
  DATA_ROOT=/app/data
  EXPECTED_EPOCH=epoch-...
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

SCHEMA = "fly_archive_acked_rotated_market_v1"
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
APPLY = os.environ.get("APPLY", "false").strip().lower() in {"1", "true", "yes"}
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
EXPECTED_EPOCH = os.environ.get("EXPECTED_EPOCH", "epoch-281be253d7ee19636c6bf487").strip()

# Sealed ACK gen 6f100488212356de… membership + local mirror sizes/sha256.
ALLOWLIST = (
    {
        "rel": "market_microstructure_1s.jsonl.1",
        "size": 20971757,
        "sha256": "9bad6a4856c5767ac5e70b8933b4b7816c05c418acc18a43b82de0bc2a895f7b",
        "acked_gen": "6f100488212356de956551bf9ceeffc93d8609c843909a4433a1530b8bf40630",
    },
    {
        "rel": "source_order_market_evidence.jsonl.1",
        "size": 20971930,
        "sha256": "f167407d66c0fdf9177066c4ca8ad69c1591b81638a851645fd1107a4d332cc4",
        "acked_gen": "6f100488212356de956551bf9ceeffc93d8609c843909a4433a1530b8bf40630",
    },
)

SEARCH_ROOTS = (
    DATA_ROOT / "runtime",
    DATA_ROOT,
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _find(rel: str) -> Path | None:
    for root in SEARCH_ROOTS:
        cand = (root / rel).resolve()
        try:
            cand.relative_to(DATA_ROOT)
        except ValueError:
            continue
        if cand.is_file():
            return cand
    return None


def _fs_used_mb() -> float | None:
    try:
        st = os.statvfs(str(DATA_ROOT))
        return round((st.f_blocks - st.f_bfree) * st.f_frsize / 1048576, 1)
    except Exception:
        return None


def main() -> int:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    archive_root = (
        DATA_ROOT / "runtime" / "research_archive" / f"acked-rotated-market-{stamp}"
    ).resolve()
    try:
        archive_root.relative_to(DATA_ROOT)
    except ValueError:
        print(json.dumps({"ok": False, "error": "archive_escape"}))
        return 2

    report = {
        "schema": SCHEMA,
        "ok": False,
        "dry_run": DRY_RUN,
        "apply": APPLY,
        "expected_epoch": EXPECTED_EPOCH,
        "data_root": str(DATA_ROOT),
        "archive_root": str(archive_root),
        "before_fs_used_mb": _fs_used_mb(),
        "candidates": [],
        "actions": [],
        "errors": [],
        "bytes_moved": 0,
        "never_arm": True,
        "NO_SAFE": True,
    }

    do_move = APPLY and (not DRY_RUN)
    if do_move:
        archive_root.mkdir(parents=True, exist_ok=True)

    for row in ALLOWLIST:
        rel = row["rel"]
        entry = {
            "rel": rel,
            "expected_size": row["size"],
            "expected_sha256": row["sha256"],
            "acked_gen": row["acked_gen"],
            "found": False,
            "matched": False,
        }
        path = _find(rel)
        if path is None:
            entry["status"] = "NOT_FOUND"
            report["candidates"].append(entry)
            report["errors"].append(f"missing:{rel}")
            continue
        entry["found"] = True
        entry["path"] = str(path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            entry["status"] = "STAT_FAILED"
            entry["error"] = str(exc)
            report["candidates"].append(entry)
            report["errors"].append(f"stat:{rel}:{exc}")
            continue
        entry["actual_size"] = size
        if size != row["size"]:
            entry["status"] = "SIZE_MISMATCH"
            report["candidates"].append(entry)
            report["errors"].append(f"size_mismatch:{rel}:{size}!={row['size']}")
            continue
        try:
            digest = _sha256_file(path)
        except OSError as exc:
            entry["status"] = "HASH_FAILED"
            entry["error"] = str(exc)
            report["candidates"].append(entry)
            report["errors"].append(f"hash:{rel}:{exc}")
            continue
        entry["actual_sha256"] = digest
        if digest.lower() != row["sha256"].lower():
            entry["status"] = "SHA_MISMATCH"
            report["candidates"].append(entry)
            report["errors"].append(f"sha_mismatch:{rel}")
            continue
        entry["matched"] = True
        dest = archive_root / rel
        action = {
            "action": "MOVE" if do_move else "WOULD_MOVE",
            "from": str(path),
            "to": str(dest),
            "bytes": size,
            "sha256": digest,
        }
        if do_move:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    raise FileExistsError(str(dest))
                shutil.move(str(path), str(dest))
                action["moved"] = True
                report["bytes_moved"] += size
            except Exception as exc:
                action["moved"] = False
                action["error"] = f"{type(exc).__name__}:{exc}"
                report["errors"].append(f"move:{rel}:{exc}")
                entry["status"] = "MOVE_FAILED"
                report["candidates"].append(entry)
                report["actions"].append(action)
                continue
        entry["status"] = "ARCHIVED" if do_move else "ELIGIBLE"
        report["candidates"].append(entry)
        report["actions"].append(action)

    report["after_fs_used_mb"] = _fs_used_mb()
    report["bytes_moved_mib"] = round(report["bytes_moved"] / 1048576, 2)
    report["eligible_mib"] = round(
        sum(a.get("bytes") or 0 for a in report["actions"]) / 1048576, 2
    )
    report["ok"] = not report["errors"] and bool(report["actions"])
    report["invent_delta_mib_estimate"] = -report["eligible_mib"]

    # Persist receipt under archive (or /tmp on dry-run).
    receipt_dir = archive_root if do_move else Path("/tmp")
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "archive-acked-rotated-market-receipt.json"
    receipt_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["receipt_path"] = str(receipt_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
