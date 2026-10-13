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

_V3_KEEP = (  # runtime/v3: live / execution / lifecycle only (paper research ledgers excluded:
               # opportunity, decision, market_segment, pre_entry_features, deep, tile,
               # baseline_execution_context, qualification_horizon_index, market_segments, signal_snapshots)
    "runtime/v3/ledgers/execution.jsonl*",
    "runtime/v3/ledgers/lifecycle.jsonl*",
    "runtime/v3/ledgers/lifecycle/**",
    "runtime/v3/ledgers/order_intent.jsonl*",
    "runtime/v3/lifecycle_bundle_index/**",
    "runtime/v3/lifecycle_worker/**",
    "runtime/v3/lifecycle_owner/**",
    "runtime/v3/lifecycle_purge_transactions/**",
    "runtime/v3/lifecycle_cleanup_transactions/**",
    "runtime/v3/receipts/lifecycle_membership_v1/**",
)
_LIVE_FILES = (
    "live_execution_reports.jsonl*", "live_copy_approvals.jsonl*", "live_copy_output_state.json",
    "live_copy_protection_evidence.json", "live_copy_trade_decisions.json", "live_copy_tile_eligibility.json",
    "live_copy_outbox.json", "live_copy_protection_bootstrap.json",
    "live_copy_account_transitions.json", "live_copy_resolved_alerts.json",
    "bitfinex_order_action_audit.jsonl*", "bitfinex_live_state.json", "bitfinex_live_switch_state.json",
    "relay_lifecycle_evidence_v1.json", "relay_status_snapshot_v1.json",
    "relay_outbox_quarantine.jsonl*", "relay_outbox_retired.jsonl*",
    "ledger_reconciliation.json", "shadow_vs_live_entry.jsonl*", "live_drills.jsonl*",
    "mandatory-reserve.alarm-journal.json",
    "copy_only_exchange_fills_report.json", "bitfinex_copy_fidelity_lifecycles_report.json",
    "order_intent.jsonl*",
    "lane_pnl_ledger.json", "lane_lab_pnl_ledger.json", "lane_pnl_ledger_epoch_receipts.jsonl*",
    "execution_settings_history.jsonl*", "spread-gate.json",
)
# orders/positions/open_positions/pending_orders .json: KEPT only when they hold live state
# (see is_kept(..., content=...)); PaperPortfolio state is deliberately NOT kept.
_LIVE_STATE_CANDIDATES = ("orders.json", "positions.json", "open_positions.json", "pending_orders.json")

FLY = _V3_KEEP + tuple("**/" + f for f in _LIVE_FILES) + ("keep-*.tgz",)  # relative to /app/data
BOX = ("backups/keep-*/keep.tgz",)
# Railway relay-executor (apps/api relay-executor-worker) Neon order / fill / reconciliation tables
NEON_TABLES = ("SignalCycle", "SignalCycleEvent", "SignalCycleParticipant",
               "SignalCycleParticipantReduction", "RelayPositionReductionAudit")
# R2 bucket doxed-btc-archive: per-file mirror keys are matched against FLY after stripping the
# prefix and ".zst"; keep/ holds the keep snapshot. fly-segments/v3/seg tars are mixed (not keepable).
R2_MIRROR_PREFIXES = ("fly-mirror-segments/tree/", "pre-clean-epoch/fly-mirror-segments/tree/")
R2_KEEP_PREFIXES = ("keep/",)

MANIFEST = {"decision": DECISION, "fly": list(FLY), "box": list(BOX), "neon_tables": list(NEON_TABLES),
            "live_state_candidates": list(_LIVE_STATE_CANDIDATES),
            "r2_mirror_prefixes": list(R2_MIRROR_PREFIXES), "r2_keep_prefixes": list(R2_KEEP_PREFIXES)}
MANIFEST_SHA256 = hashlib.sha256(json.dumps(MANIFEST, sort_keys=True).encode()).hexdigest()


def _match(rel: str, pattern: str) -> bool:
    rel = rel.replace("\\\\", "/")
    if pattern.endswith("/**"):
        return rel == pattern[:-3] or rel.startswith(pattern[:-2])
    if pattern.startswith("**/"):
        base = pattern[3:]
        return fnmatch.fnmatch(rel.rsplit("/", 1)[-1], base)
    return fnmatch.fnmatch(rel, pattern)


def _live_state(content) -> bool:
    if not isinstance(content, (dict, list)):
        return False
    txt = json.dumps(content)[:200000].lower()
    if "paperportfolio" in txt or '"paper": true' in txt:
        return False
    return any(k in txt for k in ('"live"', "bitfinex", "exchange_order_id", '"mode": "live"'))


def is_kept(rel: str, patterns=FLY, content=None) -> bool:
    if any(_match(rel, p) for p in patterns):
        return True
    base = rel.replace("\\\\", "/").rsplit("/", 1)[-1]
    return base in _LIVE_STATE_CANDIDATES and content is not None and _live_state(content)


def is_r2_kept(key: str) -> bool:
    """Same KEEP rule for the wipe receipt and the independent box R2 listing."""
    if any(key.startswith(p) for p in R2_KEEP_PREFIXES):
        return True
    for p in R2_MIRROR_PREFIXES:
        if key.startswith(p):
            rel = key[len(p):]
            rel = rel[:-4] if rel.endswith(".zst") else rel
            return is_kept(rel) or is_kept("runtime/" + rel)
    return False


def kept_dir(rel_dir: str, patterns=FLY) -> bool:
    """Whole directory kept (lets the scan count it without walking 300k files)."""
    return any(p.endswith("/**") and (rel_dir == p[:-3] or rel_dir.startswith(p[:-2])) for p in patterns)
