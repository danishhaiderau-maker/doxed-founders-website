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
# Timestamp fields are tried in order; dotted paths walk nested objects and
# numeric segments index lists. Values may be epoch seconds/ms or ISO strings;
# anything outside [TIER_A_MIN_VALID_TS, TIER_A_MAX_VALID_TS) is not a timestamp.
TIER_A_DATASETS = {
    "market_microstructure_1s.jsonl": ("bitfinex_l1_tape_1s", 1,
                                       ("bucket_ts", "source_ts", "ts", "t", "timestamp", "second")),
    "cross_venue_tape_1m.jsonl": ("cross_venue_tape_1m", 1, ("minute_ts", "ts", "t", "timestamp")),
    "market_context_1m.jsonl": ("market_context_1m", 1, ("minute_ts", "ts", "t", "timestamp")),
    "liquidations.jsonl": ("liquidations", 1, ("ts", "recv_ts", "exch_ts", "event_ts", "timestamp")),
    "ai_input_log.jsonl": ("ai_calls", 1, ("ts", "ts_epoch", "timestamp", "logged_at", "created_at")),
    "ai_shadow_challengers.jsonl": ("ai_shadow_challengers", 1,
                                    ("decision_ts", "decision_utc", "tape_features.as_of_ts", "ts", "timestamp",
                                     "logged_at")),
    "ai_shadow_compact_prompt.jsonl": ("ai_shadow_compact_prompt", 1,
                                       ("observed_at_utc", "facts.as_of_utc", "ts", "timestamp", "logged_at")),
    "ai_reason_research.jsonl": ("ai_reason_research", 1, ("ts", "timestamp", "logged_at")),
    "ai_confidence_calibration.jsonl": ("ai_confidence_calibration", 1, ("ts", "timestamp", "closed_at")),
    "ai_tranche_log.csv": ("ai_decisions", 1, ("ts", "shared_ai_call_ts", "timestamp", "time")),
    "decisions_3factor.csv": ("decisions", 1, ("ts", "shared_ai_call_ts", "timestamp", "time")),
    "trades_3factor.csv": ("closed_trades", 1, ("close_ts", "exit_time", "timestamp", "ts")),
    "trade_outcome.jsonl": ("trade_outcomes", 1, ("close_ts", "exit_ts", "ts", "timestamp")),
    "trade_lifecycle.jsonl": ("trade_lifecycle", 1, ("ts", "timestamp", "event_ts")),
    "fill_quality.jsonl": ("fill_quality", 1, ("ts", "fill_ts", "timestamp")),
    "fill_markouts.jsonl": ("fill_markouts", 1, ("ts", "fill_ts", "timestamp")),
    "shadow_exit_paths.jsonl": ("shadow_exit_paths", 1, ("fill_ts", "signal_ts", "ts")),
    "expired_orders_3factor.csv": ("expired_orders", 1, ("time", "expired_ts", "created_ts", "timestamp", "ts")),
    "v3/ledgers/order_intent.jsonl": ("v3_order_intent", 1,
                                      ("submitted_ts", "signal_ts", "entry_children.0.hypothetical_order_start_ts",
                                       "ts", "created_at", "recorded_at")),
    "v3/ledgers/execution.jsonl": ("v3_execution", 1,
                                   ("fill_ts", "close_ts", "exit_market_receipt.observed_ts", "ts", "created_at",
                                    "recorded_at")),
    "v3/ledgers/lifecycle.jsonl": ("v3_lifecycle", 1,
                                   ("signal_ts", "observed_ts", "submitted_ts", "terminal_ts",
                                    "replay_eligibility.signal_ts", "market_segment_coverage.requested_end_ts",
                                    "ts", "created_at", "recorded_at")),
    "v3/ledgers/decision.jsonl": ("v3_decision", 1, ("decision_ts", "ts", "created_at", "recorded_at")),
    "v3/ledgers/opportunity.jsonl": ("v3_opportunity", 1,
                                     ("signal_ts", "causal_identity.signal_ts", "ts", "created_at", "recorded_at")),
}
TIER_A_GLOBS = {
    "*quarantine*/*quarantine_manifest.json": ("quarantine_receipts", 1, ("quarantined_at", "created_at", "ts")),
}

TIER_A_MIN_VALID_TS = 1577836800.0  # 2020-01-01T00:00:00Z
TIER_A_MAX_VALID_TS = 4102444800.0  # 2100-01-01T00:00:00Z

# Rows with no timestamp of their own inherit the earliest timestamp of a dated
# row sharing one of these identity keys (e.g. a V3 decision row carries only
# event_id; its opportunity/lifecycle rows carry signal_ts).
TIER_A_JOIN_KEYS = ("event_id", "episode_id", "shared_ai_call_id")
# dataset -> join keys its dated rows publish into the index
TIER_A_JOIN_FEEDERS = {
    "v3_opportunity": TIER_A_JOIN_KEYS,
    "v3_order_intent": TIER_A_JOIN_KEYS,
    "v3_lifecycle": TIER_A_JOIN_KEYS,
    "v3_execution": TIER_A_JOIN_KEYS,
    "v3_decision": TIER_A_JOIN_KEYS,
    "ai_decisions": ("shared_ai_call_id",),
}
# datasets whose undated rows may be resolved through the join index
TIER_A_JOIN_RESOLVED = frozenset({"v3_opportunity", "v3_order_intent", "v3_lifecycle", "v3_execution",
                                  "v3_decision"})
# datasets deduplicated by their resolved timestamp when partitions are written
TIER_A_DEDUPE_BY_TS = frozenset({"bitfinex_l1_tape_1s"})

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


def tier_a_specs() -> dict[str, tuple[int, tuple]]:
    """dataset -> (schema_version, timestamp fields) for every Tier A dataset."""
    return {spec[0]: (spec[1], spec[2])
            for spec in list(TIER_A_DATASETS.values()) + list(TIER_A_GLOBS.values())}


def field_value(row, path: str):
    """Value at a dotted ``path`` (numeric segments index lists), or None."""
    current = row
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
        if current is None:
            return None
    return current


def parse_timestamp(value) -> float | None:
    """Epoch seconds from epoch seconds/ms or an ISO string; None if implausible."""
    from datetime import datetime, timezone

    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        number = number / 1000.0 if number > 1e11 else number
        return number if TIER_A_MIN_VALID_TS <= number < TIER_A_MAX_VALID_TS else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return parse_timestamp(float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parse_timestamp(parsed.timestamp())


def row_timestamp(row, fields: tuple) -> tuple[float | None, str | None]:
    """(timestamp, field) from the first field holding a valid timestamp."""
    if not isinstance(row, dict):
        return None, None
    for name in fields:
        ts = parse_timestamp(field_value(row, name))
        if ts is not None:
            return ts, name
    envelope = row.get("envelope")
    if isinstance(envelope, dict):
        for name in ("signal_ts", "ts", "timestamp", "recorded_at", "created_at"):
            ts = parse_timestamp(envelope.get(name))
            if ts is not None:
                return ts, f"envelope.{name}"
    return None, None


def join_keys(row, keys: tuple) -> list[str]:
    """``key:value`` identities of ``row`` for the join index."""
    if not isinstance(row, dict):
        return []
    out = []
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            out.append(f"{key}:{value}")
    return out


def valid_day(day: str) -> bool:
    """True for a YYYY-MM-DD partition day inside the plausible timestamp window."""
    from datetime import datetime, timezone

    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return False
    return TIER_A_MIN_VALID_TS <= start < TIER_A_MAX_VALID_TS


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
