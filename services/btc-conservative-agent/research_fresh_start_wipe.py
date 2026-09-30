"""Plan-first deletion of pre-cutover backlog the running bot does not need.

Runs on the Fly guest through the dispatch-only ``fresh-start-wipe-plan`` /
``fresh-start-wipe-execute`` workflow modes, after a volume snapshot exists and
inside a paper maintenance boundary:

    python /app/research_fresh_start_wipe.py --data-root /app/data --plan --v1-acked-through auto
    python /app/research_fresh_start_wipe.py --data-root /app/data --execute \
        --expect-plan-sha256 <sha from --plan in the same boundary> --v1-acked-through auto

Only two classes are ever deleted:

* closed numbered rotations of append streams (``x.jsonl.7``) that are
  neither among the newest ``KEEP_NEWEST_ROTATIONS`` of their stream nor
  modified within ``KEEP_RECENT_SECONDS`` (boot replay and future-path
  windows read the newest rotations);
* the superseded v1 segment store and v1 shipper checkpoint, only once the
  v2 epoch has published its genesis and the laptop ACK covers every v1 seq.

Live streams, state, ledgers, locks, config, receipts and SQLite databases
never match either class. Anything else is reported, never deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

SCHEMA = "research_fresh_start_wipe_plan_v1"
KEEP_NEWEST_ROTATIONS = 2
KEEP_RECENT_SECONDS = 72 * 3600
ROTATION_RE = re.compile(r"^(?P<base>.+\.(?:jsonl|csv|log))\.(?P<n>[1-9][0-9]*)$")
RESEARCH_LINKS = ("research", "research_accumulator", "research_archive")


def _roots(data_root: Path) -> list[Path]:
    runtime = data_root / "runtime"
    roots = [runtime]
    for name in RESEARCH_LINKS:
        try:
            target = (runtime / name).resolve(strict=True)
            target.relative_to(data_root)
        except (OSError, ValueError):
            continue
        if target.is_dir() and target not in roots:
            try:
                target.relative_to(runtime)
            except ValueError:
                roots.append(target)
    return roots


def rotation_candidates(data_root: Path, now: float) -> list[dict]:
    streams: dict[tuple[str, str], list[tuple[int, Path, os.stat_result]]] = {}
    for root in _roots(data_root):
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [name for name in dirnames if not (Path(directory) / name).is_symlink()]
            for name in filenames:
                match = ROTATION_RE.match(name)
                path = Path(directory) / name
                if not match or path.is_symlink():
                    continue
                streams.setdefault((directory, match.group("base")), []).append(
                    (int(match.group("n")), path, path.stat()))
    doomed = []
    for (_directory, base), rotations in sorted(streams.items()):
        rotations.sort(key=lambda item: item[0], reverse=True)
        for rank, (number, path, stat) in enumerate(rotations):
            if rank < KEEP_NEWEST_ROTATIONS or now - stat.st_mtime < KEEP_RECENT_SECONDS:
                continue
            doomed.append({"path": str(path), "bytes": int(stat.st_size),
                           "mtime_ns": int(stat.st_mtime_ns), "reason": f"closed rotation {number} of {base}"})
    return doomed


def _tree_files(root: Path) -> list[dict]:
    rows = []
    for directory, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.is_symlink():
                continue
            stat = path.stat()
            rows.append({"path": str(path), "bytes": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns),
                         "reason": "superseded v1 segment epoch"})
    return rows


def v1_epoch_candidates(data_root: Path, v1_acked_through: int | str | None) -> tuple[list[dict], str]:
    store, checkpoint = data_root / "segment-store" / "v1", data_root / "segment-shipper"
    if not store.exists() and not checkpoint.exists():
        return [], "V1_ALREADY_ABSENT"
    if (os.environ.get("RESEARCH_SEGMENTS_PREFIX") or "v1").strip() == "v1":
        return [], "V1_STILL_ACTIVE"
    try:
        v2 = json.loads((data_root / "segment-shipper-v2" / "state.json").read_text("utf-8"))
        published = int(json.loads((checkpoint / "status.json").read_text("utf-8"))["shipped_seq"])
    except (OSError, ValueError, KeyError):
        return [], "V1_OR_V2_CHECKPOINT_UNREADABLE"
    if int(v2.get("seq") or 0) < 1 or not v2.get("baseline"):
        return [], "V2_GENESIS_NOT_PUBLISHED"
    ack = store / "acks" / "laptop" / f"{published:012d}.json"
    if v1_acked_through == "auto":
        v1_acked_through = published
    if v1_acked_through != published or not ack.is_file():
        return [], f"V1_NOT_FULLY_ACKED(published={published})"
    return _tree_files(store) + _tree_files(checkpoint), "V1_FULLY_ACKED"


def usage(data_root: Path, depth: int = 2) -> dict[str, int]:
    totals: dict[str, int] = {}
    for directory, dirnames, filenames in os.walk(data_root, followlinks=False):
        rel = Path(directory).relative_to(data_root).parts
        key = "/".join(rel[:depth]) or "."
        for name in filenames:
            try:
                totals[key] = totals.get(key, 0) + (Path(directory) / name).lstat().st_size
            except OSError:
                pass
    return dict(sorted(totals.items(), key=lambda item: -item[1])[:60])


def build_plan(data_root: Path, *, now: float, v1_acked_through: int | str | None) -> dict:
    rotations = rotation_candidates(data_root, now)
    v1, v1_status = v1_epoch_candidates(data_root, v1_acked_through)
    candidates = sorted(rotations + v1, key=lambda row: row["path"])
    identity = json.dumps([[row["path"], row["bytes"], row["mtime_ns"]] for row in candidates],
                          separators=(",", ":")).encode()
    return {"schema": SCHEMA, "data_root": str(data_root), "candidates": candidates,
            "rotation_files": len(rotations), "rotation_bytes": sum(r["bytes"] for r in rotations),
            "v1_files": len(v1), "v1_bytes": sum(r["bytes"] for r in v1), "v1_status": v1_status,
            "total_files": len(candidates), "total_bytes": sum(r["bytes"] for r in candidates),
            "plan_sha256": hashlib.sha256(identity).hexdigest(), "usage_bytes": usage(data_root)}


def execute(plan: dict, data_root: Path) -> dict:
    deleted, freed = 0, 0
    root = data_root.resolve()
    for row in plan["candidates"]:
        path = Path(row["path"])
        path.resolve().relative_to(root)
        stat = path.stat()
        if (int(stat.st_size), int(stat.st_mtime_ns)) != (row["bytes"], row["mtime_ns"]):
            raise RuntimeError(f"{path} changed after planning; refusing")
        path.unlink()
        deleted += 1
        freed += row["bytes"]
    for directory in (data_root / "segment-store" / "v1", data_root / "segment-shipper"):
        if plan["v1_files"] and directory.is_dir():
            for current, dirnames, filenames in os.walk(directory, topdown=False):
                if not filenames and not os.listdir(current):
                    os.rmdir(current)
    return {"deleted_files": deleted, "freed_bytes": freed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", default="/app/data")
    parser.add_argument("--v1-acked-through", type=lambda v: v if v == "auto" else int(v), default=None,
                        help="final v1 seq the laptop ACKed, or 'auto' to require the on-volume ACK of the published seq")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--expect-plan-sha256", default="")
    args = parser.parse_args(argv)
    data_root = Path(args.data_root)
    plan = build_plan(data_root, now=time.time(), v1_acked_through=args.v1_acked_through)
    summary = {key: plan[key] for key in plan if key != "candidates"}
    if args.plan:
        summary["examples"] = plan["candidates"][:25]
        print(json.dumps(summary, sort_keys=True))
        return 0
    if not args.expect_plan_sha256 or args.expect_plan_sha256 != plan["plan_sha256"]:
        print(json.dumps({"error": "PLAN_SHA256_MISMATCH", **summary}, sort_keys=True))
        return 3
    result = execute(plan, data_root)
    print(json.dumps({"executed": True, **result, **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
