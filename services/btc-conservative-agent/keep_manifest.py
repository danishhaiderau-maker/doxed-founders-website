"""Pinned KEEP manifest (Danish 13 Oct 13:22 AEDT: KEEP the live Bitfinex order, fill and
reconciliation records). The fresh-start wipe never deletes these; integrity-gaps G16 reports
them separately as KEEP, and anything older than the epoch that is NOT listed is RED.

Changing this list changes MANIFEST_SHA256; test_keep_manifest pins the hash, so an edit needs
an explicit test update (reviewed).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json

DECISION = "Danish 2026-10-13 13:22 AEDT: KEEP live Bitfinex order/fill/reconciliation records"

FLY = (  # relative to /app/data
    "runtime/v3/**",                                   # live order / fill / lifecycle state
    "**/live_execution_reports.jsonl*",                # executor reports (orders, fills, closes)
    "**/live_copy_approvals.jsonl*",                   # signed approvals
    "**/live_copy_output_state.json",
    "**/live_copy_protection_evidence.json",
    "**/live_copy_trade_decisions.json",
    "**/live_copy_tile_eligibility.json",
    "**/live_copy_outbox.json",
    "**/live_copy_protection_bootstrap.json",
    "**/lane_pnl_ledger.json",                         # ledgers
    "**/lane_lab_pnl_ledger.json",
    "**/lane_pnl_ledger_epoch_receipts.jsonl*",
    "**/execution_settings_history.jsonl*",            # settings
    "**/spread-gate.json",
    "keep-*.tgz",                                      # the keep snapshot itself
)
BOX = ("backups/keep-*/keep.tgz",)
NEON_TABLES = ("SignalCycle", "SignalCycleEvent", "SignalCycleParticipant")

MANIFEST = {"decision": DECISION, "fly": list(FLY), "box": list(BOX), "neon_tables": list(NEON_TABLES)}
MANIFEST_SHA256 = hashlib.sha256(json.dumps(MANIFEST, sort_keys=True).encode()).hexdigest()


def _match(rel: str, pattern: str) -> bool:
    rel = rel.replace("\\\\", "/")
    if pattern.endswith("/**"):
        return rel == pattern[:-3] or rel.startswith(pattern[:-2])
    if pattern.startswith("**/"):
        base = pattern[3:]
        return fnmatch.fnmatch(rel.rsplit("/", 1)[-1], base)
    return fnmatch.fnmatch(rel, pattern)


def is_kept(rel: str, patterns=FLY) -> bool:
    return any(_match(rel, p) for p in patterns)


def kept_dir(rel_dir: str, patterns=FLY) -> bool:
    """Whole directory kept (lets the scan count it without walking 300k files)."""
    return any(p.endswith("/**") and (rel_dir == p[:-3] or rel_dir.startswith(p[:-2])) for p in patterns)
