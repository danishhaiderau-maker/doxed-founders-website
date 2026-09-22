#!/usr/bin/env python3
"""Quarantine closed market rotations out of Fly invent (preserve, never delete).

Moves closed ``market_microstructure_1s.jsonl.N`` / ``source_order_market_evidence.jsonl.N``
(N>=1) into invent-excluded ``research_archive/``. Active unsuffixed writers stay put.

Why: client soft-cap 200 MiB skips invents; these closed ~20 MiB rotations are the
dominant organic bloat. Already-ACKed copies exist in the laptop mirror; newly
rotated closed siblings are quarantined (not destroyed) so CURRENT can seal.

Safe boundaries:
- Never arms Bitfinex / never touches watchers or paper toggles.
- Never touches active (unsuffixed) files.
- Quarantine only (shutil.move into research_archive).
- Refuses if active sibling is missing.

Env:
  DRY_RUN=true|false (default true)
  APPLY=true|false (default false; must be true with DRY_RUN=false to move)
  DATA_ROOT=/app/data
  EXPECTED_EPOCH=epoch-...
  MODE=closed_family|acked_sha (default closed_family)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

SCHEMA = "fly_archive_acked_rotated_market_v2"
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
APPLY = os.environ.get("APPLY", "false").strip().lower() in {"1", "true", "yes"}
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
EXPECTED_EPOCH = os.environ.get("EXPECTED_EPOCH", "epoch-281be253d7ee19636c6bf487").strip()
MODE = os.environ.get("MODE", "closed_family").strip().lower()

STEMS = (
    "market_microstructure_1s.jsonl",
    "source_order_market_evidence.jsonl",
)
CLOSED_RE = re.compile(r"^(?P<stem>.+\.jsonl)\.(?P<n>\d+)$")

# Optional exact sealed-ACK pins (MODE=acked_sha only).
ACKED_SHA_ALLOWLIST = (
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


def _discover_closed_family() -> list[dict]:
    found: dict[str, dict] = {}
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_file():
                continue
            m = CLOSED_RE.match(child.name)
            if not m:
                continue
            stem = m.group("stem")
            if stem not in STEMS:
                continue
            rel = child.name
            if rel in found:
                continue
            active = _find(stem)
            try:
                size = child.stat().st_size
            except OSError:
                continue
            found[rel] = {
                "rel": rel,
                "stem": stem,
                "path": str(child.resolve()),
                "size": size,
                "active_present": active is not None,
                "active_path": str(active) if active is not None else None,
            }
    return [found[k] for k in sorted(found)]


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
        "mode": MODE,
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

    targets: list[dict] = []
    if MODE == "acked_sha":
        for row in ACKED_SHA_ALLOWLIST:
            path = _find(row["rel"])
            entry = {
                "rel": row["rel"],
                "expected_size": row["size"],
                "expected_sha256": row["sha256"],
                "acked_gen": row["acked_gen"],
                "found": path is not None,
            }
            if path is None:
                entry["status"] = "NOT_FOUND"
                report["candidates"].append(entry)
                report["errors"].append(f"missing:{row['rel']}")
                continue
            entry["path"] = str(path)
            try:
                size = path.stat().st_size
                digest = _sha256_file(path)
            except OSError as exc:
                entry["status"] = "STAT_OR_HASH_FAILED"
                entry["error"] = str(exc)
                report["candidates"].append(entry)
                report["errors"].append(f"hash:{row['rel']}:{exc}")
                continue
            entry["actual_size"] = size
            entry["actual_sha256"] = digest
            if size != row["size"] or digest.lower() != row["sha256"].lower():
                entry["status"] = "IDENTITY_MISMATCH"
                report["candidates"].append(entry)
                report["errors"].append(f"identity_mismatch:{row['rel']}")
                continue
            entry["matched"] = True
            targets.append({"rel": row["rel"], "path": path, "size": size, "sha256": digest, "meta": entry})
    else:
        for row in _discover_closed_family():
            entry = dict(row)
            if not row["active_present"]:
                entry["status"] = "ACTIVE_MISSING_REFUSE"
                report["candidates"].append(entry)
                report["errors"].append(f"active_missing:{row['stem']}")
                continue
            path = Path(row["path"])
            try:
                digest = _sha256_file(path)
            except OSError as exc:
                entry["status"] = "HASH_FAILED"
                entry["error"] = str(exc)
                report["candidates"].append(entry)
                report["errors"].append(f"hash:{row['rel']}:{exc}")
                continue
            entry["sha256"] = digest
            entry["matched"] = True
            targets.append(
                {
                    "rel": row["rel"],
                    "path": path,
                    "size": int(row["size"]),
                    "sha256": digest,
                    "meta": entry,
                }
            )

    for item in targets:
        rel = item["rel"]
        path = item["path"]
        size = item["size"]
        digest = item["sha256"]
        dest = archive_root / rel
        action = {
            "action": "MOVE" if do_move else "WOULD_MOVE",
            "from": str(path),
            "to": str(dest),
            "bytes": size,
            "sha256": digest,
        }
        entry = item["meta"]
        if do_move:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    raise FileExistsError(str(dest))
                shutil.move(str(path), str(dest))
                action["moved"] = True
                report["bytes_moved"] += size
                entry["status"] = "ARCHIVED"
            except Exception as exc:
                action["moved"] = False
                action["error"] = f"{type(exc).__name__}:{exc}"
                entry["status"] = "MOVE_FAILED"
                report["errors"].append(f"move:{rel}:{exc}")
                report["candidates"].append(entry)
                report["actions"].append(action)
                continue
        else:
            entry["status"] = "ELIGIBLE"
        report["candidates"].append(entry)
        report["actions"].append(action)

    report["after_fs_used_mb"] = _fs_used_mb()
    report["bytes_moved_mib"] = round(report["bytes_moved"] / 1048576, 2)
    report["eligible_mib"] = round(
        sum(a.get("bytes") or 0 for a in report["actions"]) / 1048576, 2
    )
    report["ok"] = (not report["errors"]) and bool(report["actions"])
    report["invent_delta_mib_estimate"] = -report["eligible_mib"]

    receipt_dir = archive_root if do_move else Path("/tmp")
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "archive-acked-rotated-market-receipt.json"
    receipt_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["receipt_path"] = str(receipt_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
