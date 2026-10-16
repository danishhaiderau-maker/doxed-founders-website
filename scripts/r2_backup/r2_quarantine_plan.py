#!/usr/bin/env python3
"""DRY-RUN ONLY: list the R2 objects that would be quarantined. This script has no move/delete code.

The 5,930 objects dated 14 Oct 03:xx AEDT (13 Oct 16:xx UTC) are pre-wipe laptop files re-uploaded by the first
nightly sync after the 13 Oct wipe. Per HM (c) they are quarantined (moved under ``_quarantine/<date>/``),
never deleted. The move step is NOT implemented here: it needs Danish's explicit go and a separate reviewed change
that consumes this plan's ``plan_sha256`` and, immediately before moving, calls ``verify_plan`` (``verify``
subcommand: re-list the bucket; re-check plan_sha256, size and last-modified per key; fail on any pinned key).
Building a plan FAILS (QuarantinePlanError) if any candidate is pinned by the keep manifest.

    python r2_quarantine_plan.py [--keep-prefixes a/,b/ | --keep-manifest keep.json]
                                 [--since 2026-10-13T16:00:00Z --until 2026-10-13T17:00:00Z]
                                 [--expect 5930] [--out quarantine-plan.jsonl]

Read-only: only ListObjectsV2. Exit 0 always for a successful listing; ``count_matches_expected`` says whether the
candidate count equals --expect (a mismatch means do NOT proceed to a move).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import r2_backup as rb


def build_plan(listing: dict, prefixes, since: str, until: str, expect: int | None) -> tuple[dict, list]:
    keys = rb.quarantine_candidates(listing, prefixes, since, until)
    rows = [{"object_key": k, "size": listing[k]["size"], "last_modified_ts": listing[k]["ts"],
             "quarantine_key": rb.QUARANTINE_PREFIX + "20261013/" + k} for k in keys]
    by_prefix: dict = {}
    for r in rows:
        top = r["object_key"].split("/", 1)[0]
        by_prefix[top] = by_prefix.get(top, 0) + 1
    summary = {"schema": "r2_quarantine_plan_v1", "mode": "DRY_RUN_NO_MOVE", "bucket": rb.BUCKET,
               "window_utc": [since, until], "candidates": len(rows), "bytes": sum(r["size"] for r in rows),
               "by_top_prefix": dict(sorted(by_prefix.items())), "expected": expect,
               "count_matches_expected": (expect is None or len(rows) == expect),
               "plan_sha256": plan_identity_sha256(rows),
               "examples": [r["object_key"] for r in rows[:5]]}
    return summary, rows


def plan_identity_sha256(rows: list) -> str:
    identity = json.dumps([[r["object_key"], r["size"], r["last_modified_ts"]] for r in rows],
                          separators=(",", ":")).encode()
    return hashlib.sha256(identity).hexdigest()


def verify_plan(rows: list, expected_sha256: str, listing: dict, prefixes, since: str, until: str) -> dict:
    """READ-ONLY re-validation a (future, separately reviewed) move step MUST pass immediately before moving.

    ``listing`` is a FRESH ``list_bucket`` result. ``ok`` only if: the plan rows hash to the approved
    ``plan_sha256``; every key still exists with the same size and last-modified; no key is pinned; and the
    fresh window selection yields no extra key that the approved plan does not contain.
    """
    problems: list[str] = []
    if not expected_sha256 or plan_identity_sha256(rows) != expected_sha256:
        problems.append("PLAN_SHA256_MISMATCH")
    missing = changed_size = changed_mtime = pinned = 0
    for r in rows:
        key = r["object_key"]
        cur = listing.get(key)
        if cur is None:
            missing += 1
            continue
        if int(cur["size"]) != int(r["size"]):
            changed_size += 1
        if float(cur["ts"]) != float(r["last_modified_ts"]):
            changed_mtime += 1
        sk = rb.obj_to_key(key)
        if sk is None or rb.is_pinned(sk, prefixes):
            pinned += 1
    extra = 0
    try:
        fresh = set(rb.quarantine_candidates(listing, prefixes, since, until))
        extra = len(fresh - {r["object_key"] for r in rows})
    except rb.QuarantinePlanError as exc:
        problems.append(f"PINNED_CANDIDATE: {exc}")
    for name, n in (("KEY_MISSING", missing), ("SIZE_CHANGED", changed_size), ("LAST_MODIFIED_CHANGED", changed_mtime),
                    ("PINNED_KEY_IN_PLAN", pinned), ("NEW_CANDIDATES_NOT_IN_PLAN", extra)):
        if n:
            problems.append(f"{name}:{n}")
    return {"schema": "r2_quarantine_verify_v1", "mode": "DRY_RUN_NO_MOVE", "ok": not problems, "problems": problems,
            "plan_rows": len(rows), "plan_sha256": expected_sha256}


def _verify_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="r2_quarantine_plan.py verify",
                                 description="re-list the bucket and re-verify an approved plan (read-only)")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--expect-plan-sha256", required=True)
    ap.add_argument("--keep-prefixes", default=os.environ.get("R2_KEEP_PREFIXES", ""))
    ap.add_argument("--keep-manifest", default=os.environ.get("R2_KEEP_MANIFEST", ""))
    ap.add_argument("--since", default=rb.QUARANTINE_SINCE)
    ap.add_argument("--until", default=rb.QUARANTINE_UNTIL)
    args = ap.parse_args(argv)
    keep = rb.load_keep(args.keep_prefixes, args.keep_manifest)
    with open(args.plan, "r", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    result = verify_plan(rows, args.expect_plan_sha256, rb.list_bucket(rb.client()), keep["prefixes"],
                         args.since, args.until)
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["ok"] else 4


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "verify":
        return _verify_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep-prefixes", default=os.environ.get("R2_KEEP_PREFIXES", ""))
    ap.add_argument("--keep-manifest", default=os.environ.get("R2_KEEP_MANIFEST", ""))
    ap.add_argument("--since", default=rb.QUARANTINE_SINCE)
    ap.add_argument("--until", default=rb.QUARANTINE_UNTIL)
    ap.add_argument("--expect", type=int, default=rb.QUARANTINE_EXPECTED)
    ap.add_argument("--out", default="quarantine-plan.jsonl")
    args = ap.parse_args(argv)
    keep = rb.load_keep(args.keep_prefixes, args.keep_manifest)
    listing = rb.list_bucket(rb.client())
    summary, rows = build_plan(listing, keep["prefixes"], args.since, args.until, args.expect)
    with open(args.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    summary["keep_manifest_sha256"] = keep["sha256"]
    summary["plan_file"] = os.path.abspath(args.out)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
