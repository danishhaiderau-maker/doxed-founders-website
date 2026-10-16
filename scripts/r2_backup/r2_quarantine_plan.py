#!/usr/bin/env python3
"""DRY-RUN ONLY: list the R2 objects that would be quarantined. This script has no move/delete code.

The 5,930 objects dated 14 Oct 03:xx AEDT (13 Oct 16:xx UTC) are pre-wipe laptop files re-uploaded by the first
nightly sync after the 13 Oct wipe. Per HM (c) they are quarantined (moved under ``_quarantine/<date>/``),
never deleted. The move step is NOT implemented here: it needs Danish's explicit go and a separate reviewed change
that consumes this plan's ``plan_sha256``.

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
    identity = json.dumps([[r["object_key"], r["size"], r["last_modified_ts"]] for r in rows],
                          separators=(",", ":")).encode()
    by_prefix: dict = {}
    for r in rows:
        top = r["object_key"].split("/", 1)[0]
        by_prefix[top] = by_prefix.get(top, 0) + 1
    summary = {"schema": "r2_quarantine_plan_v1", "mode": "DRY_RUN_NO_MOVE", "bucket": rb.BUCKET,
               "window_utc": [since, until], "candidates": len(rows), "bytes": sum(r["size"] for r in rows),
               "by_top_prefix": dict(sorted(by_prefix.items())), "expected": expect,
               "count_matches_expected": (expect is None or len(rows) == expect),
               "plan_sha256": hashlib.sha256(identity).hexdigest(),
               "examples": [r["object_key"] for r in rows[:5]]}
    return summary, rows


def main(argv=None) -> int:
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
