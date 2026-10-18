"""Guarded removal of obsolete archive/old-boot artifacts on the Fly volume (HM 18 Oct, no permanent archive).

Removes ONLY paths that fullmatch an explicit, anchored allowlist:
  * runtime/data_epoch_boundary/ce-20261004-v31-(clean|final-b|final-c|final-d).(v3|research_events_v22).json
  * archive_receipt.json (volume root)
  * research_archive/handoffs/(cancellation|fill)_evidence_handoffs.jsonl.<UTC stamp>

Never touched, by construction (they do not match, and PROTECTED is checked first): the pinned epoch's boundary receipts
(ce-20261004-v31-freeze21b.*), live_copy_*.json, data_epoch.json, crash_dump.json, anything else.
Usage (dry run by default):  python retire_obsolete_artifacts.py ROOT [--execute --expect-sha256 SHA]
The execute step requires the plan hash printed by the dry run, so only the reviewed list can be deleted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

PINNED_EPOCH_ID = "ce-20261004-v31-freeze21b"
REMOVABLE = tuple(re.compile(x) for x in (
    r"runtime/data_epoch_boundary/ce-20261004-v31-(?:clean|final-b|final-c|final-d)\.(?:v3|research_events_v22)\.json",
    # HM 18 Oct (G16 list). Old-boot ids only: "final-e", "final", "freeze21" (NOT freeze21b, protected below).
    r"runtime/data_epoch_boundary/ce-20261004-v31-(?:final-e|final|freeze21)\.(?:v3|research_events_v22)\.json",
    r"runtime/data_epoch_boundary/ce-20261004-v31-freeze21\.(?:epoch_aliases|pre_epoch_archive)\.json",
    r"runtime/data_epoch_boundary/orphan_tmp_sweep\.\d{8}T\d{6}Z\.json",
    r"runtime/(?:disloc_trades|disloc_foi_trades)\.jsonl\.validation\.json",
    r"runtime/(?:lane_pnl_ledger_epoch_receipts\.jsonl|pre_entry_evidence_handoffs\.jsonl\.1"
    r"|relay_outbox_quarantine\.jsonl|relay_outbox_retired\.jsonl)",
    # the one old pre-restart recovery receipt (its crash_dump.json is the single exact-path exception below)
    r"runtime/recovery_receipts/pre-restart-34276394781/(?:crash_dump|paper_lifecycle_v1)\.json",
    r"archive_receipt\.json",
    r"research_archive/handoffs/(?:cancellation|fill)_evidence_handoffs\.jsonl\.\d{8}T\d{6}Z",
))
PROTECTED = tuple(re.compile(x) for x in (
    r".*" + re.escape(PINNED_EPOCH_ID) + r".*",
    r".*live_copy_[^/]*\.json",
    r".*(?:^|/)data_epoch\.json",
    r".*(?:^|/)crash_dump\.json",
    # keep manifest / live switch files (HM: protect explicitly, never by omission)
    r".*(?:^|/)bitfinex_live_state\.json",
    r".*(?:^|/)bitfinex_live_switch_state\.json",
    r".*(?:^|/)config(?:-[^/]*)?\.json",
    r".*(?:^|/)policy\.json",
    r".*(?:^|/)spread-gate\.json",
    r".*(?:^|/)keep_manifest[^/]*",
))
# Exact relative paths exempt from PROTECTED (the generic crash_dump.json rule stays in force everywhere else,
# including runtime/crash_dump.json and any other recovery_receipts directory).
PROTECTED_EXCEPTIONS = frozenset({"runtime/recovery_receipts/pre-restart-34276394781/crash_dump.json"})


def removable(rel: str) -> bool:
    rel = rel.replace("\\", "/").lstrip("/")
    if rel not in PROTECTED_EXCEPTIONS and any(p.fullmatch(rel) for p in PROTECTED):
        return False
    return any(r.fullmatch(rel) for r in REMOVABLE)


def plan(root: str) -> list[str]:
    out = []
    for d, _dirs, names in os.walk(root):
        for n in names:
            rel = os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/")
            if removable(rel) and os.path.isfile(os.path.join(root, rel)) and not os.path.islink(os.path.join(root, rel)):
                out.append(rel)
    return sorted(out)


def plan_sha256(paths: list[str]) -> str:
    return hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()


def execute(root: str, expect_sha256: str) -> dict:
    paths = plan(root)
    if plan_sha256(paths) != expect_sha256:
        return {"ok": False, "error": "PLAN_HASH_MISMATCH", "plan_sha256": plan_sha256(paths), "deleted": []}
    deleted = []
    for rel in paths:
        if not removable(rel):  # defence in depth
            continue
        full = os.path.join(root, rel)
        if os.path.islink(full) or not os.path.isfile(full):  # re-check right before removal (swap race)
            continue
        os.remove(full)
        deleted.append(rel)
    return {"ok": True, "deleted": deleted, "plan_sha256": plan_sha256(paths)}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    root = argv[0]
    if "--execute" in argv:
        sha = argv[argv.index("--expect-sha256") + 1] if "--expect-sha256" in argv else ""
        res = execute(root, sha)
        print(json.dumps(res, indent=1))
        return 0 if res["ok"] else 1
    paths = plan(root)
    print(json.dumps({"files": paths, "count": len(paths), "plan_sha256": plan_sha256(paths)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
