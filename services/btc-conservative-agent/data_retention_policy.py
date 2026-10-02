"""Shared data-retention classification (Fly pruner, laptop retention, analyzer).

Pure constants and functions: no bot.py, Flask or I/O dependency.

Every relpath is POSIX and relative to the Fly runtime root (the laptop mirror
tree uses the same layout). A file is in exactly one class:

* PROTECTED  - never deleted by any retention path: current-epoch live state,
  ledgers needed for restart recovery, relay/Bitfinex evidence, quarantine and
  recovery receipts, SQLite stores, seal-validated research-event generations.
* TIER_A     - compact primitives kept long-term (compressed Parquet on the
  laptop): the 1 s L1 tape, cross-venue and market-context streams, AI calls
  and shadow challengers, order/fill/position ledgers, closed trades.
* TIER_B     - bulky derived data reconstructible from the 1 s tape plus a
  deterministic simulator: multiverse paths, post-exit replays, signal replays.
* OTHER      - everything else (kept; deletable only as a sealed rotation).

Only sealed numeric rotations (``x.jsonl.N``) are ever deletion candidates; an
active append head is never deleted.
"""

from __future__ import annotations

import fnmatch
import re

POLICY_VERSION = "data_retention_policy_v1"

PROTECTED = "PROTECTED"
TIER_A = "TIER_A"
TIER_B = "TIER_B"
OTHER = "OTHER"

_ROTATION_RE = re.compile(r"^(?P<base>.+\.(?:jsonl|csv|log))\.(?P<gen>[1-9][0-9]*)$")

# Reconstructible path blobs (DATA-SUFFICIENCY-MODEL-A/B/C-20261002).
TIER_B_BASES = frozenset({
    "signal_replay.jsonl",
    "order_multiverse.jsonl",
    "post_exit_replay.jsonl",
})

# Tier A datasets: base file -> (dataset name, schema_version, timestamp fields).
# Bump schema_version when the collected row format changes incompatibly.
TIER_A_DATASETS = {
    "market_microstructure_1s.jsonl": ("bitfinex_l1_tape_1s", 1, ("ts", "t", "timestamp", "second")),
    "cross_venue_tape_1m.jsonl": ("cross_venue_tape_1m", 1, ("ts", "minute_ts", "t", "timestamp")),
    "market_context_1m.jsonl": ("market_context_1m", 1, ("ts", "minute_ts", "t", "timestamp")),
    "liquidations.jsonl": ("liquidations", 1, ("ts", "t", "timestamp", "event_ts")),
    "ai_input_log.jsonl": ("ai_calls", 1, ("ts", "timestamp", "logged_at", "created_at")),
    "ai_shadow_challengers.jsonl": ("ai_shadow_challengers", 1, ("ts", "timestamp", "logged_at")),
    "ai_shadow_compact_prompt.jsonl": ("ai_shadow_compact_prompt", 1, ("ts", "timestamp", "logged_at")),
    "ai_reason_research.jsonl": ("ai_reason_research", 1, ("ts", "timestamp", "logged_at")),
    "ai_confidence_calibration.jsonl": ("ai_confidence_calibration", 1, ("ts", "timestamp", "closed_at")),
    "ai_tranche_log.csv": ("ai_decisions", 1, ("timestamp", "ts", "time")),
    "decisions_3factor.csv": ("decisions", 1, ("timestamp", "ts", "time")),
    "trades_3factor.csv": ("closed_trades", 1, ("close_ts", "exit_time", "timestamp", "ts")),
    "trade_outcome.jsonl": ("trade_outcomes", 1, ("close_ts", "exit_ts", "ts", "timestamp")),
    "trade_lifecycle.jsonl": ("trade_lifecycle", 1, ("ts", "timestamp", "event_ts")),
    "fill_quality.jsonl": ("fill_quality", 1, ("ts", "fill_ts", "timestamp")),
    "fill_markouts.jsonl": ("fill_markouts", 1, ("ts", "fill_ts", "timestamp")),
    "expired_orders_3factor.csv": ("expired_orders", 1, ("timestamp", "ts")),
    "v3/ledgers/order_intent.jsonl": ("v3_order_intent", 1, ("ts", "created_at", "recorded_at")),
    "v3/ledgers/execution.jsonl": ("v3_execution", 1, ("ts", "created_at", "recorded_at")),
    "v3/ledgers/lifecycle.jsonl": ("v3_lifecycle", 1, ("ts", "created_at", "recorded_at")),
    "v3/ledgers/decision.jsonl": ("v3_decision", 1, ("ts", "created_at", "recorded_at")),
    "v3/ledgers/opportunity.jsonl": ("v3_opportunity", 1, ("ts", "created_at", "recorded_at")),
}
TIER_A_GLOBS = {
    "*quarantine*/*quarantine_manifest.json": ("quarantine_receipts", 1, ("quarantined_at", "created_at", "ts")),
}

# Never deleted by any retention path (substring / prefix / glob, case-insensitive).
PROTECTED_PREFIXES = ("v3/", "recovery_receipts/", "research-timing-declarations/")
PROTECTED_SUBSTRINGS = (
    "relay", "bitfinex", "lifecycle", "quarantine", "recovery", "paper_lifecycle", "open_positions",
    "config", "research_session", "epoch", ".lock", "receipt", "manifest", "secret", "credential",
    "research_events_v22", "ledger", "crash_dump",
)
PROTECTED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm")
# Rotations read by exact name or by content digest from surviving rows
# (order_multiverse rows reference entry-grid digests with no timestamp, so a
# pruned grid rotation is indistinguishable from a broken reference).
FLY_KEEP_ROTATION_BASES = frozenset({"chase_offset_touch_grid.jsonl", "order_multiverse_entry_grid.jsonl"})


def rotation_parts(relpath: str) -> tuple[str, int] | None:
    match = _ROTATION_RE.match(relpath)
    return (match.group("base"), int(match.group("gen"))) if match else None


def base_of(relpath: str) -> str:
    parts = rotation_parts(relpath)
    return parts[0] if parts else relpath


def is_protected(relpath: str) -> bool:
    lower = relpath.lower()
    if lower.startswith(PROTECTED_PREFIXES) or lower.endswith(PROTECTED_SUFFIXES):
        return True
    return any(token in lower for token in PROTECTED_SUBSTRINGS)


def tier_a_dataset(relpath: str) -> tuple[str, int, tuple] | None:
    base = base_of(relpath)
    if base in TIER_A_DATASETS:
        return TIER_A_DATASETS[base]
    for pattern, spec in TIER_A_GLOBS.items():
        if fnmatch.fnmatch(base.lower(), pattern):
            return spec
    return None


def classify(relpath: str) -> str:
    """Return the retention class of one runtime-relative path."""
    base = base_of(relpath)
    if base in TIER_B_BASES:
        return TIER_B
    if tier_a_dataset(relpath) is not None:
        return TIER_A
    if is_protected(relpath):
        return PROTECTED
    return OTHER


def deletable_rotation(relpath: str, *, fly: bool = False) -> bool:
    """True only for a sealed rotation that no protection rule covers."""
    parts = rotation_parts(relpath)
    if parts is None or "/" in relpath:
        return False
    if parts[0] in FLY_KEEP_ROTATION_BASES:
        return False
    if classify(relpath) == TIER_B:
        return True
    return not is_protected(relpath)
