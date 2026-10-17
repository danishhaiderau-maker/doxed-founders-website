"""Retire the superseded Fly segment prefix after a full reset (no archive).

HM confirmation #1 (17 Oct 2026). The full reset's file wipe only scans ``<data-root>/runtime``; the research segment
store lives OUTSIDE it (``<data-root>/segment-store/<prefix>/`` plus the shipper state dir ``<data-root>/segment-shipper-v3*``).
The pre-reset prefix (``v3``: seq 1..3242, windows from 13 Oct) therefore survives the reset on disk. Once the fresh-start
epoch is stamped and the bot restarted, ``RESEARCH_SEGMENTS_PREFIX_PER_WIPE=1`` rotates the live prefix to ``v3-w<epoch_ts>`` and the
server stops answering for ``v3`` (research_segment_server.SegmentServer.__call__: UNKNOWN_ROUTE), but the bytes stay on the volume.
This module deletes exactly those superseded directories, and nothing else.

Fail-closed gates (every one refuses BEFORE deleting anything):
* per-wipe prefixes on, and a stamped fresh-start epoch (the live prefix must differ from the retired ones);
* only ``segment-store/<name>`` with ``name`` = ``v3`` or ``v3-w<digits>`` and only ``segment-shipper-v3`` / ``segment-shipper-v3-w<digits>``;
  never the live prefix/state dir, never ``v1``/``v2`` (archive prefixes) or any other name, never a symlink;
* the live prefix must already be serving from the new epoch: its state dir exists (the shipper rotated).
Runs by hand / by the dispatch-only workflow step; nothing imports it at boot.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import research_segment_epoch as epoch_mod

SCHEMA = "full_reset_segment_retire_v1"
_PREFIX_RE = re.compile(r"^v3(?:-w[0-9]+)?$")
_STATE_RE = re.compile(r"^segment-shipper-v3(?:-w[0-9]+)?$")


class SegmentRetireRefused(RuntimeError):
    """Nothing was deleted."""


def _live_identity(data_root: Path, env) -> dict:
    if not epoch_mod.per_wipe_enabled(env):
        raise SegmentRetireRefused("RESEARCH_SEGMENTS_PREFIX_PER_WIPE=1 required")
    epoch_id = epoch_mod.fresh_start_epoch_id(data_root)
    if not epoch_id:
        raise SegmentRetireRefused("fresh-start epoch not stamped: the live prefix has not rotated")
    base = env.get("RESEARCH_SEGMENTS_PREFIX") or "v1"
    prefix = epoch_mod.resolve_prefix(base, epoch_id, per_wipe=True)
    if not _PREFIX_RE.match(prefix) or prefix == base:
        raise SegmentRetireRefused(f"live prefix {prefix!r} is not a per-wipe v3 prefix")
    state_dir = epoch_mod.resolve_state_dir(env.get("RESEARCH_SEGMENTS_STATE_DIR") or data_root / "segment-shipper",
                                            epoch_id, per_wipe=True)
    return {"epoch_id": epoch_id, "prefix": prefix, "state_dir": Path(state_dir), "base": base}


def _tree(path: Path) -> tuple[list[str], list[str], int]:
    files, dirs, size = [], [], 0
    for directory, dirnames, filenames in os.walk(path, followlinks=False):
        for name in list(dirnames):
            full = os.path.join(directory, name)
            if os.path.islink(full):
                raise SegmentRetireRefused(f"symlink inside retired tree refused: {full}")
            dirs.append(full)
        for name in filenames:
            full = os.path.join(directory, name)
            st = os.lstat(full)
            if not os.path.isfile(full) or os.path.islink(full):
                raise SegmentRetireRefused(f"non-regular file inside retired tree refused: {full}")
            files.append(full)
            size += st.st_size
    return files, dirs, size


def plan(data_root, env=None) -> dict:
    env = os.environ if env is None else env
    data_root = Path(data_root)
    live = _live_identity(data_root, env)
    if not live["state_dir"].is_dir():
        raise SegmentRetireRefused(f"live shipper state dir {live['state_dir']} absent: restart the bot first")
    targets = []
    candidates = []
    store = data_root / "segment-store"
    if store.is_dir():
        candidates += [(store / p.name, "segment-store") for p in store.iterdir() if _PREFIX_RE.match(p.name)]
    candidates += [(p, "shipper-state") for p in data_root.iterdir() if _STATE_RE.match(p.name)]
    for path, kind in sorted(candidates):
        if path.name == live["prefix"] and kind == "segment-store":
            continue
        if path == live["state_dir"]:
            continue
        if os.path.islink(path) or not path.is_dir():
            raise SegmentRetireRefused(f"{path} is a symlink or not a directory")
        files, dirs, size = _tree(path)
        targets.append({"path": str(path), "kind": kind, "files": len(files), "bytes": size})
    return {"schema": SCHEMA, "live_prefix": live["prefix"], "live_state_dir": str(live["state_dir"]),
            "fresh_start_epoch_id": live["epoch_id"], "targets": targets,
            "total_files": sum(t["files"] for t in targets), "total_bytes": sum(t["bytes"] for t in targets)}


def execute(data_root, env=None, *, receipts_dir=None, now=None) -> dict:
    env = os.environ if env is None else env
    doc = plan(data_root, env)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now))
    receipts = Path(receipts_dir or Path(data_root) / "runtime" / "full-reset-receipts")
    receipts.mkdir(parents=True, exist_ok=True)
    intent = receipts / f"segment-retire-intent-{stamp}.json"
    intent.write_text(json.dumps({**doc, "phase": "INTENT"}, indent=1, sort_keys=True), "utf-8")
    with open(intent, "rb") as handle:
        os.fsync(handle.fileno())
    deleted = 0
    for target in doc["targets"]:
        files, dirs, _ = _tree(Path(target["path"]))
        for f in files:
            os.unlink(f)
            deleted += 1
        for d in sorted(dirs, key=len, reverse=True):
            os.rmdir(d)
        os.rmdir(target["path"])
    done = {**doc, "phase": "COMPLETE", "deleted_files": deleted, "completed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (receipts / f"segment-retire-complete-{stamp}.json").write_text(json.dumps(done, indent=1, sort_keys=True), "utf-8")
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="full-reset-segment-retire")
    ap.add_argument("command", choices=("plan", "execute"))
    ap.add_argument("--data-root", default="/app/data")
    ap.add_argument("--expect-live-prefix", default="", help="execute: refuse unless the live prefix equals this")
    args = ap.parse_args(argv)
    try:
        doc = plan(args.data_root)
        if args.command == "execute":
            if not args.expect_live_prefix or args.expect_live_prefix != doc["live_prefix"]:
                raise SegmentRetireRefused(f"--expect-live-prefix must equal the live prefix {doc['live_prefix']}")
            doc = execute(args.data_root)
    except SegmentRetireRefused as exc:
        print(json.dumps({"error": "SEGMENT_RETIRE_REFUSED", "detail": str(exc)}))
        return 8
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
