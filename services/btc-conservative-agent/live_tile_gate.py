"""Two-tile live gate (HM gate N1-N3 / G6; RK 24 Oct: its own PR). ONE shared helper for every live tile (R4): no per-tile code.

Pure decision logic, no I/O. bot.py feeds it the live-copy chains (executor reports), the relay's exchange mirror, the
persisted per-trade decisions and the per-tile switch rows; the first ENTRY event of a trade is admitted only when every
check passes. A refused signal stays paper-only (the paper tile is never touched) and is logged with the reason.

1. one live position account-wide (``LIVE_SKIPPED_MUTEX``): no other open / pending / in-flight live trade on ANY tile;
2. tile key: every live tile has a stable key (its trade-id prefix) and a stable 7-bit code carried in the Bitfinex cid
   (``encode_cid`` / ``decode_cid``; the relay mirror is ``live-tile-gate.ts``); an unknown tile is refused;
3. margin caps: per-tile cap (default $0.25) and a total cap (default = sum of the ARMED tiles' caps), checked every entry;
4. at most ``MAX_ENTRIES_PER_DAY`` (20) live entries per tile per **UTC** day (HM G6 / AMEND3 "per UTC day");
5. net position 0 before every new entry, from a fresh exchange mirror (no position, no resting non-reduce-only order);
6. (guardian side) ``live_guardian.evaluate`` attributes violations with ``chain_tile_key`` for every live tile.

Caps are in USD **margin** (the system's $0.25 per entry is margin at 100x = $25 notional, see live_copy_control
MAX_MARGIN_USD); the cap compares like with like. No fees anywhere (R7).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Iterable, Mapping

import live_exit_fill as _lef

# Stable tile key (= trade-id prefix) -> cid code (1..127). NEVER renumber or reuse a code; a new tile takes the next free one.
# Display tile numbers are NOT stable (derived from ACTIVE_TILE_ORDER), so they are never used as the key.
TILE_KEY_CODES: dict = {
    "rnd": 1,   # FAMILY_RANDOM_CONTROL_TAKER_90
    "gs1": 2,   # FAMILY_GS01_XV_PREMIUM_ATR_TP (GS-01)
    "gb1": 3,   # FAMILY_GSB1_CVD_DIV_REGIME
    "gb2": 4,   # FAMILY_GSB2_REGIME_SWITCHER
    "gs6": 5,   # FAMILY_GS06_COMMITTED_FADE_ATR_TP
    "gf3": 8,   # FAMILY_GS_FADE3_TP30 (GS-FADE3-TP30; paper only, see PAPER_ONLY_TILE_KEYS)
}
# Retired tiles keep their codes reserved forever (never reissued): dsl=6, dsf=7.
RESERVED_TILE_KEY_CODES = {"dsl": 6, "dsf": 7}

# cid layout (positive int32): bits 28-30 = 0b101 marker, bits 21-27 = tile code, bits 0-20 = hash.
CID_MARKER = 0b101
CID_MARKER_SHIFT = 28
CID_CODE_SHIFT = 21
CID_HASH_MASK = (1 << CID_CODE_SHIFT) - 1

DEFAULT_TILE_MARGIN_CAP_USD = 0.25
MAX_ENTRIES_PER_DAY = 20
DAY_BASIS = "UTC"
PENDING_EXPOSURE_SEC = 300.0      # same bound the guardian uses for a sent-but-unfilled order
INFLIGHT_APPROVAL_SEC = 180.0     # relay placement bound (FLY_APPROVAL_PLACEMENT_MAX_AGE_SEC): approved, not yet reported
MIRROR_MAX_AGE_SEC = 20.0

# HM G6 daily REALISED live loss caps (RK 02:25 24 Oct: ALL THREE caps (per tile, account-wide total, and the guardian's
# live_guardian.DAILY_LOSS_CAP_USD, which is DAILY_LOSS_CAP_TOTAL_USD) come from ONE source):
#   grok-strategist/prereg/PREREG-GS-FADE3-TP30-AMEND3-20261023.md line 22 (D3), sha256 CAP_PREREG_SHA256:
#   "Daily loss cap: $0.05 per tile and $0.10 in total (UTC day). At most 20 live entries per tile per day."
# FROZEN: there is no env override. These values change ONLY through a NEW frozen prereg amendment with RK PASS, and the
# pin below (CAP_PREREG_FILE / CAP_PREREG_LINE / CAP_PREREG_SHA256 / CAP_PREREG_LINE_TEXT) must be updated in the same
# commit. test_live_tile_gate.test_loss_caps_pinned_to_frozen_amend3 parses the values out of CAP_PREREG_LINE_TEXT, pins
# the sha and line, and when the prereg file is present it re-hashes it and re-reads line 22, so a silent edit fails CI.
# Breach response (AMEND4 E1, sha256 AMEND4_SHA256 below, L12): the caps are an ENTRY BLOCK ONLY, never a close and never a
# disarm. admit_entry refuses a new live entry (LIVE_SKIPPED_DAILY_CAP, AMBER; the signal stays a normal paper row) while the
# tile's (or the account's) UTC-day realised trade loss (REAL Bitfinex fill prices; no funding, no fees: RK D3 06:26) plus the open mark-to-book loss (full-size
# exit-side walk through the shared paper_fill book helper) is >= the cap. HM N1: a closed live trade of the day whose pnl is
# unreadable, or a book older than 2 s, is NOT skipped: it blocks entries (AMBER). The caps never SIZE an entry. The one
# switchable response is cap_breach_response() (== CAP_RESPONSE_ENTRY_BLOCK under AMEND4).
CAP_PREREG_FILE = "grok-strategist/prereg/PREREG-GS-FADE3-TP30-AMEND3-20261023.md"
CAP_PREREG_LINE = 22
CAP_PREREG_SHA256 = "632dcf347ce061a3f81d0afd73a4820d0406f4b714e11d5daed00485183efde9"
CAP_PREREG_LINE_TEXT = ("- Daily loss cap: $0.05 per tile and $0.10 in total (UTC day). "
                        "At most 20 live entries per tile per day.")
DAILY_LOSS_CAP_PER_TILE_USD = 0.05
DAILY_LOSS_CAP_TOTAL_USD = 0.10
DAILY_LOSS_CAP_SOURCE = (
    f"{CAP_PREREG_FILE} L{CAP_PREREG_LINE} (D3: \"Daily loss cap: $0.05 per tile and $0.10 in total (UTC day)\") "
    f"sha256 {CAP_PREREG_SHA256}; HM grokbot/health-monitor/LIVE-CHECK-RECIPE.md L94-107 (G6)"
)
VIOL_TILE_LOSS_CAP = "LIVE_TILE_DAILY_LOSS_CAP"
VIOL_TOTAL_LOSS_CAP = "LIVE_TOTAL_DAILY_LOSS_CAP"

SKIP_MUTEX = "LIVE_SKIPPED_MUTEX"

# ---- prereg AMEND4 (+A1, +A2): daily-cap scope, liquidation-aware stop backstop, stop residency, stale book ----------------
# grok-strategist/prereg/PREREG-GS-FADE3-TP30-AMEND4-20261024.md, sha256 AMEND4_SHA256 (supersedes ea582767 / 3ca9b4f7).
# FROZEN like the AMEND3 pin: no env override; test_live_tile_gate.test_amend4_constants_pinned re-hashes the file when present
# and re-reads every cited line. One shared implementation for every live tile (R4); prices only from the order book (R3).
AMEND4_FILE = "grok-strategist/prereg/PREREG-GS-FADE3-TP30-AMEND4-20261024.md"
AMEND4_SHA256 = "7073ed25e2ae8047b4f6e7993764af336b6599c912e13ea086a2e4e591fd8755"
AMEND4_LINES = {"E1": 12, "E2": (13, 15), "E2'": (20, 23), "E5": (24, 27), "E6": 28, "E7": (29, 32), "E8": 33,
                "E9": 36, "E10": 37, "E6E7_NON_COUNTABLE": 38}
STOP_BACKSTOP_LIQ_BUFFER_BP = 10.0  # E2'(L22): liquidation price minus 10 bp toward entry
STOP_RESIDENT_MAX_AGE_S = 5.0       # E6(L28): exchange stop confirmed resident at least every 5 s
BOOK_ENTRY_MAX_AGE_S = 2.0          # E7(L30): book > 2 s old => block new entries (== paper_fill.MAX_BOOK_AGE_S)
BOOK_HOLD_MAX_AGE_S = 10.0          # E7(L31): hold an open position only while the book is <= 10 s old AND the stop is resident
CAP_RESPONSE_ENTRY_BLOCK = "ENTRY_BLOCK"
SKIP_DAILY_CAP = "LIVE_SKIPPED_DAILY_CAP"          # E1(L12) stamp on the blocked (paper-only) signal
STOP_FAILURE_FLATTEN = "STOP_FAILURE_FLATTEN"      # E2(L15) / E2'(L23) event + guardian code
AMBER_BOOK_STALE = "BOOK_STALE_ENTRY_BLOCK"         # E7(L30)
# ---- prereg AMEND5 rev 4 FINAL (on AMEND4 7073ed25): backstop margin, in-trade liq check, G4 outcomes, FADE3 arm gate ----------
# grok-strategist/prereg/PREREG-GS-FADE3-TP30-AMEND5-20261024.md, sha256 AMEND5_SHA256. Binding: F1, F2, F4, G1, G2, G3,
# G4(a), G4(b), G4(c'), G5, G6. VOID (no code): F3 (L8-12), rev-2 G4(c) (L25), rev-3 F3'/G3' (L28-34, 7d1302b7).
# ONE place for every AMEND5 constant; test_live_protection.test_amend5_constants_pinned re-hashes the file and re-reads every
# cited line. Switchable only here (G4_ACTIONS maps each G4 outcome to its action); no env override.
AMEND5_FILE = "grok-strategist/prereg/PREREG-GS-FADE3-TP30-AMEND5-20261024.md"
AMEND5_SHA256 = "4407d5a2bd86b349c64d59aaeaf180c237b5872315eae4e1f3be92e99382c850"
AMEND5_LINES = {"F1": 6, "F2": 7, "F4": 13, "G1": 16, "G2": 17, "G3": 18, "G4": 19, "G4(a)": 20, "G4(b)": (21, 24),
                "G5": 26, "REV4": (36, 37), "G4(c')": (38, 42), "G6": 43}
AMEND5_VOID_LINES = {"F3": (8, 12), "G4(c)": 25, "REV3": (28, 34)}
AMEND5_BINDING = ("F1", "F2", "F4", "G1", "G2", "G3", "G4(a)", "G4(b)", "G4(c')", "G5", "G6")
# F1 (L6): 18 bp past the exchange-stop level. G1 (L16) deletes "only widen": after the receive-stamp PR (#851, F4 L13) has
# landed and >= 20 REAL exchange-stop fills exist, E10 re-sets it EITHER way. G2 (L17): the STOPLOSS-DIST basis is an INTERIM
# ESTIMATE used only for this safety margin, never realism/edge evidence.
STOP_BACKSTOP_PAST_STOP_BP = 18.0
STOP_BACKSTOP_STATUS = ("INTERIM ESTIMATE (AMEND5 F1/G2: STOPLOSS-DIST-20261024, proxy receive time; safety margin only, never "
                        "realism/edge evidence). E10 re-set (either way, G1) needs the receive-stamp PR #851 (F4) AND >= 20 real stop fills")
LIQ_CHECK_MAX_BP = 15.0      # G3 (L18): real L <= 15 bp past the stop (INCLUSIVE) or unreadable => LIQ_CHECK_FLATTEN + disarm ALL
LIQ_NORMAL_MIN_BP = 28.0     # G4(a) (L20): L >= 28 bp past the stop => normal (backstop = stop + 18)
LIQ_CLAMP_RATE_MAX = 0.05    # G4(b) (L23): disarm ALL if LIQ_CLAMP_FLATTEN > 5 % of live trades ...
LIQ_CLAMP_MIN_N = 20         # ... at n >= 20 live trades per tile
LIQ_SOURCE = "BITFINEX_POSITION_LIQ_PRICE"   # G5 (L26): L only ever read from Bitfinex (relay mirror); never estimated
LIQ_CHECK_FLATTEN = "LIQ_CHECK_FLATTEN"      # G3: non-countable, counted; disarm ALL
LIQ_CLAMP_FLATTEN = "LIQ_CLAMP_FLATTEN"      # G4(b): THIS position only, no disarm; non-countable, counted
LIQ_CLAMP_RATE = "LIQ_CLAMP_RATE_GT_5PCT"    # G4(b): disarm ALL
STOP_REEVAL = "STOP_REEVAL"                  # G4(c'): GS-01 + FADE3 stay disarmed; Strategist re-evaluates the stop
G4_A, G4_B, G4_C = "G4(a)", "G4(b)", "G4(c')"
G4_ACTIONS = {G4_A: "NORMAL", G4_B: "ARMED_LIQ_CLAMP_POSITION_ONLY", G4_C: "DISARMED_STOP_REEVAL"}
G4_FIRST_TILE = "gs1"        # G4 / E8: the real L on GS-01's FIRST live position decides the outcome
G6_GATED_TILE = "gf3"        # G6 (L43): FADE3 (its own tile key gf3, NOT GS-06's gs6) arms only after G4 (a)/(b) AND HM's gate
G4C_DISARMED_TILES = ("gs1", "gf3")   # G4(c'): GS-01 stays disarmed, and so does FADE3 (gf3; never gs6)
DENY_G4_STOP_REEVAL = "LIVE_G4C_STOP_REEVAL"
DENY_G6_NO_FIRST_L = "LIVE_G6_GS01_FIRST_L_PENDING"
DENY_G6_HM_GATE = "LIVE_G6_HM_GATE_NOT_PASSED"
# GS-FADE3-TP30 PAPER tile PR (RK PASS 25 Oct 06:24 AEDT): gf3 ships PAPER ONLY. Every arm path refuses it, whatever
# LIVE_HM_GATE_FADE3 says, until a later reviewed PR removes it from this set (the env stays unset in this PR).
PAPER_ONLY_TILE_KEYS = frozenset({"gf3"})
PAPER_ONLY_LANES = frozenset({"FAMILY_GS_FADE3_TP30"})
DENY_PAPER_ONLY_TILE = "LIVE_TILE_PAPER_ONLY"
# RK ruling (24 Oct) L27: no pre-entry liq ESTIMATE path (G5 bans estimates). Instead the arm gate refuses while the latest
# RECORDED real L (Bitfinex, persisted across restarts) is <= 15 bp past the stop, until a NEW stop amendment has passed
# (LIVE_STOP_AMENDMENT_PASSED=PASS:<ref>, different from the value in force when that L was recorded).
DENY_L27_LATEST_L = "LIVE_L27_LATEST_REAL_L_LE_15BP"
STOP_AMENDMENT_ENV = "LIVE_STOP_AMENDMENT_PASSED"
# RK binding ruling (2) (24 Oct, #852 R3): LIVE_STOP_AMENDMENT_PASSED must equal a FROZEN amendment sha that BOTH RK and HM have
# signed in /workspace/grokbot/SHARED-TRACKER.md (line format: ``STOP-AMENDMENT-SIGNED sha=<sha> RK=<ref> HM=<ref>``). Fly
# cannot read the tracker, so the signed shas are frozen HERE (sha -> {"rk": ref, "hm": ref}) and only a reviewed PR adds one
# (parse_signed_stop_amendments checks the tracker text). EMPTY = no amendment signed yet => every value is refused (fail
# closed). The pass itself is an explicit persisted record (bot l27_amendment_pass, set by the strict-admin route with
# actor + ts), never inferred from a later L reading (HM C-R3-2).
SIGNED_STOP_AMENDMENTS: Mapping[str, Mapping[str, str]] = {}
AMENDMENT_ENV_INVALID = "L27_AMENDMENT_ENV_NOT_A_SHA"
AMENDMENT_UNSIGNED = "L27_AMENDMENT_NOT_SIGNED_BY_RK_AND_HM"
AMENDMENT_PASS_MISSING = "L27_AMENDMENT_PASS_NOT_RECORDED"
AMENDMENT_MISMATCH = "L27_AMENDMENT_PASS_SHA_MISMATCH"
AMENDMENT_UNSTAMPED = "L27_AMENDMENT_PASS_UNSTAMPED"
AMENDMENT_PREDATES_L = "L27_AMENDMENT_PASS_PREDATES_LATEST_L"
AMENDMENT_NOT_NEW = "L27_AMENDMENT_IN_FORCE_WHEN_L_RECORDED"
DENY_DAILY_LOSS_CAP = SKIP_DAILY_CAP   # AMEND4 E1(L12): the cap refusal IS the LIVE_SKIPPED_DAILY_CAP stamp
DENY_TILE_KEY_UNKNOWN = "LIVE_TILE_KEY_UNKNOWN"
DENY_TILE_CAP = "LIVE_TILE_MARGIN_CAP"
DENY_TOTAL_CAP = "LIVE_TOTAL_MARGIN_CAP"
DENY_DAILY_LIMIT = "LIVE_DAILY_ENTRY_LIMIT"
DENY_NOT_FLAT = "ACCOUNT_NOT_FLAT"
DENY_MIRROR_BLIND = "EXCHANGE_MIRROR_BLIND"
DENY_GATE_ERROR = "LIVE_TILE_GATE_ERROR"


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _registry():
    from combo_pathway_config import ACTIVE_TILE_REGISTRY
    return ACTIVE_TILE_REGISTRY


def tile_key_for_lane(lane: str | None, registry: Mapping[str, Mapping] | None = None) -> str | None:
    """Stable tile key (trade-id prefix) for an active lane with a cid code; None for anything else (fail closed)."""
    reg = _registry() if registry is None else registry
    prefix = str(((reg.get(str(lane or "").upper()) or {}).get("id_prefix")) or "").lower()
    return prefix if prefix in TILE_KEY_CODES else None


def tile_key_for_trade_id(trade_id: str | None) -> str | None:
    tid = str(trade_id or "").strip().lower()
    for wrap in ("adopt:", "relink:"):
        if tid.startswith(wrap):
            tid = tid[len(wrap):]
    head, sep, rest = tid.partition("-")
    return head if sep and rest and head in TILE_KEY_CODES else None


def chain_tile_key(chain: Mapping[str, Any], registry: Mapping[str, Mapping] | None = None) -> str | None:
    """Tile of a live-copy chain: its lane, else its correlation/trade id prefix."""
    return (tile_key_for_lane(chain.get("lane"), registry) if chain.get("lane") else None) \
        or tile_key_for_trade_id(chain.get("correlation_id") or chain.get("trade_id"))


def encode_cid(tile_key: str, hash_int: int) -> int:
    code = TILE_KEY_CODES[tile_key]
    return (CID_MARKER << CID_MARKER_SHIFT) | (code << CID_CODE_SHIFT) | (int(hash_int) & CID_HASH_MASK)


def decode_cid(cid) -> str | None:
    """Tile key carried in a tile-keyed cid; None for legacy / foreign / stop cids (they are attributed via the ledger)."""
    try:
        c = int(cid)
    except (TypeError, ValueError):
        return None
    if c <= 0 or c > 0x7FFFFFFF or (c >> CID_MARKER_SHIFT) != CID_MARKER:
        return None
    code = (c >> CID_CODE_SHIFT) & 0x7F
    return next((k for k, v in TILE_KEY_CODES.items() if v == code), None)


def utc_day(ts: float) -> str:
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).strftime("%Y-%m-%d")


def load_caps(environ: Mapping[str, str] | None = None) -> dict:
    """Config: LIVE_TILE_MARGIN_CAPS_USD_JSON ({tile_key: usd}), LIVE_TOTAL_MARGIN_CAP_USD (optional, can only LOWER the
    armed-sum default), LIVE_TILE_MAX_ENTRIES_PER_DAY. Bad values fall back to the safe defaults (never raise)."""
    env = os.environ if environ is None else environ
    per_tile: dict = {}
    try:
        raw = json.loads(env.get("LIVE_TILE_MARGIN_CAPS_USD_JSON") or "{}")
        if isinstance(raw, dict):
            for k, v in raw.items():
                x = _f(v)
                if str(k).lower() in TILE_KEY_CODES and x is not None and x >= 0:
                    # HM C4: env can only LOWER a tile below the $0.25 default, never raise it
                    per_tile[str(k).lower()] = min(x, DEFAULT_TILE_MARGIN_CAP_USD)
    except ValueError:
        per_tile = {}
    total = _f(env.get("LIVE_TOTAL_MARGIN_CAP_USD"))
    try:
        max_day = int(env.get("LIVE_TILE_MAX_ENTRIES_PER_DAY") or MAX_ENTRIES_PER_DAY)
    except ValueError:
        max_day = MAX_ENTRIES_PER_DAY
    return {"per_tile_usd": per_tile, "total_usd": total if total is not None and total >= 0 else None,
            "max_entries_per_day": max(0, min(max_day, MAX_ENTRIES_PER_DAY))}


def tile_cap_usd(tile_key: str, caps: Mapping[str, Any]) -> float:
    return float((caps.get("per_tile_usd") or {}).get(tile_key, DEFAULT_TILE_MARGIN_CAP_USD))


def total_cap_usd(armed_tile_keys: Iterable[str], caps: Mapping[str, Any]) -> float:
    s = sum(tile_cap_usd(k, caps) for k in set(armed_tile_keys) if k in TILE_KEY_CODES)
    explicit = caps.get("total_usd")
    return min(s, float(explicit)) if explicit is not None else s


def live_exposures(chains: Iterable[Mapping[str, Any]], decisions: Iterable[tuple], now: float,
                   exclude_trade_id: str | None = None) -> list:
    """Other live trades that are open, pending on the exchange, or approved and still in flight to the relay."""
    out, seen = [], set()
    ex = str(exclude_trade_id or "")
    by_cid: dict = {}
    for c in chains or []:
        cid = str(c.get("correlation_id") or "")
        if cid:
            by_cid.setdefault(cid, []).append(c)
        if cid == ex or c.get("closed"):
            continue
        filled = c.get("fill_at_ts")
        sent = _f(c.get("order_sent_at_ts"))
        pending = sent is not None and now - sent < PENDING_EXPOSURE_SEC
        if filled or pending:
            out.append({"trade_id": cid, "tile": chain_tile_key(c), "state": "OPEN" if filled else "PENDING"})
            seen.add(cid)
    for tid, row in decisions or []:
        tid = str(tid)
        if tid == ex or tid in seen or not (row or {}).get("approved"):
            continue
        ats = _f((row or {}).get("approval_ts"))
        if ats is None or now - ats >= INFLIGHT_APPROVAL_SEC:
            continue
        rel = by_cid.get(tid) or []
        if any(c.get("closed") or (c.get("executor_decision") or {}).get("decision") == "SKIPPED"
               or any(e.get("type") in ("INTENT_SKIPPED", "INTENT_REJECTED", "ORDER_REJECTED") for e in c.get("errors") or [])
               for c in rel):
            continue
        out.append({"trade_id": tid, "tile": tile_key_for_trade_id(tid) or tile_key_for_lane((row or {}).get("lane")),
                    "state": "IN_FLIGHT"})
    return out


def flat_check(mirror: Mapping[str, Any] | None) -> list:
    """Net position 0 from a fresh mirror: no position and no resting entry (non-reduce-only) order."""
    m = mirror if isinstance(mirror, Mapping) else None
    if m is None or m.get("ok") is not True:
        return [DENY_MIRROR_BLIND]
    age = _f(m.get("read_age_s"))
    if age is None or age < 0 or age > MIRROR_MAX_AGE_SEC:     # HM C5: a missing read age is BLIND, never fresh
        return [DENY_MIRROR_BLIND]
    reasons = []
    positions = [p for p in (m.get("positions") or []) if (_f((p or {}).get("amount")) or 0.0) != 0.0 or "amount" not in (p or {})]
    if positions:
        reasons.append(f"{DENY_NOT_FLAT}:POSITION")
    # HM C6: only reduce-only (protective) orders are excluded; a non-reduce-only STOP (stop-entry) is an entry order.
    # reduce_only unknown (None) counts as an entry (fail closed).
    entries = [o for o in (m.get("open_orders") or []) if (o or {}).get("reduce_only") is not True]
    if entries:
        reasons.append(f"{DENY_NOT_FLAT}:ENTRY_ORDER")
    return reasons


def admit_entry(*, lane: str, trade_id: str, now: float, chains: Iterable[Mapping[str, Any]], mirror: Mapping[str, Any] | None,
                decisions: Iterable[tuple], armed_lanes: Iterable[str], entry_margin_usd: float,
                caps: Mapping[str, Any] | None = None, registry: Mapping[str, Mapping] | None = None,
                book: Mapping[str, Any] | None = None, g4_first_l: Mapping[str, Any] | None = None,
                hm_gate: str | None = None, latest_l: Mapping[str, Any] | None = None,
                stop_amendment: str | None = None,
                amendment_pass: Mapping[str, Any] | None = None) -> tuple[bool, list, dict]:
    """(ok, reasons, info) for the FIRST entry event of a live trade. Pure; fail closed on any error.

    ``decisions``: iterable of (trade_id, decision_row) (TradeDecisions rows; persisted, so the daily count and the in-flight
    mutex survive restarts). ``armed_lanes``: lanes whose live switch is ON now (the total cap is their caps' sum).
    ``book``: paper_fill.book10l_at(now) (the shared book helper); missing or > 2 s old => LIVE_SKIPPED_DAILY_CAP (AMEND4 E1/E7)."""
    try:
        caps = load_caps() if caps is None else caps
        key = tile_key_for_lane(lane, registry)
        info: dict = {"tile_key": key, "day_basis": DAY_BASIS}
        po = paper_only_arm_block(lane, key) or paper_only_arm_block(None, tile_key_for_trade_id(trade_id))
        if po:
            return False, [po], info   # gf3 PAPER ONLY: refused before any other check
        if key is None or tile_key_for_trade_id(trade_id) != key:
            return False, [DENY_TILE_KEY_UNKNOWN], info
        reasons: list = []
        rows = list(decisions or [])
        chains = list(chains or [])
        # 1. mutex: one live position account-wide
        others = live_exposures(chains, rows, now, exclude_trade_id=trade_id)
        info["exposures"] = others[:5]
        if others:
            reasons.append(SKIP_MUTEX)
        # 2. AMEND5 G4(c') / G6: GS-01 + FADE3 disarmed after a (c') first L; FADE3 only after (a)/(b) AND HM's gate
        g = arm_gate_block(key, g4_first_l, hm_gate, latest_l, stop_amendment, amendment_pass)
        info["g4_first_l_outcome"] = (g4_first_l or {}).get("outcome") if isinstance(g4_first_l, Mapping) else None
        if g:
            reasons.append(g)
        # 5. net = 0 before this entry
        reasons.extend(flat_check(mirror))
        # 3. margin caps
        margin = float(entry_margin_usd)
        armed = {k for k in (tile_key_for_lane(l, registry) for l in armed_lanes or []) if k}
        armed.add(key)   # the tile being entered is armed by definition (its switch passed entry_gate)
        tcap, total = tile_cap_usd(key, caps), total_cap_usd(armed, caps)
        open_margin = margin * len(others)
        info.update(entry_margin_usd=margin, tile_cap_usd=tcap, total_cap_usd=total, open_margin_usd=open_margin)
        if margin > tcap + 1e-9:
            reasons.append(DENY_TILE_CAP)
        if open_margin + margin > total + 1e-9:
            reasons.append(DENY_TOTAL_CAP)
        # 4. max entries per tile per UTC day (the trade itself is not yet decided, so it is never in this count)
        day = utc_day(now)
        n = sum(1 for tid, r in rows if (r or {}).get("approved") and str(tid) != str(trade_id)
                and (tile_key_for_trade_id(tid) or tile_key_for_lane((r or {}).get("lane"), registry)) == key
                and _f((r or {}).get("approval_ts")) is not None and utc_day(float(r["approval_ts"])) == day)
        info.update(entries_today=n, max_entries_per_day=int(caps.get("max_entries_per_day", MAX_ENTRIES_PER_DAY)), utc_day=day)
        if n >= info["max_entries_per_day"]:
            reasons.append(DENY_DAILY_LIMIT)
        # 6. HM G6 loss caps under AMEND4 E1 (L12): ENTRY BLOCK only (cap_breach_response()). Realised trade P&L (no funding/fees) + open
        #    mark-to-book (full-size exit-side book walk); unreadable pnl / mark or a book > 2 s also blocks (AMBER). The
        #    signal stays a normal paper row stamped LIVE_SKIPPED_DAILY_CAP. The E7 stale-book block comes from the same shared
        #    open_position_protection(). (No pre-arm/pre-entry liq-distance block: AMEND5 F3/F3' are VOID; G3/G4 act in-trade.)
        blocks, cap_info = daily_cap_entry_block(chains, mirror, book, now, key, registry)
        prot = open_position_protection(mirror, book, now)
        blocks += prot["block"]   # incl. AMBER_BOOK_STALE: E7 (L30) blocks entries on a > 2 s book even when flat
        info["daily_loss_violations"] = [b["code"] for b in blocks]
        info["daily_cap"] = cap_info
        if blocks:
            reasons.append(SKIP_DAILY_CAP)
        return (not reasons), reasons, info
    except Exception as exc:  # noqa: BLE001 - any error refuses the live entry (paper unaffected)
        return False, [f"{DENY_GATE_ERROR}:{type(exc).__name__}"], {}


def trade_realised_pnl_usd(c: Mapping[str, Any]) -> float | None:
    """THE realised P&L of one closed live trade for the D3 loss caps (RK final ruling (1), 24 Oct 06:26; R4: the ONE helper
    used by admit_entry/daily_cap_entry_block, daily_loss_violations and live_guardian.evaluate).

    ONLY from REAL Bitfinex fills (HM R4 B1, RK 08:03): (exit fill - entry fill) x filled qty x side, where the exit fill
    is live_exit_fill.exit_fill_price (the reporter's close.price with price_source exchange_fill | stop_fill; never the
    ticker/mark, never the entry price). NO funding term and NO fee term. There is NO pnl_usd fallback: the relay's
    pnl_usd can itself be computed from a mark or entry fallback (a $0 "loss"), so it is never read here.
    None = UNREADABLE: the caller fails closed (unreadable_pnl_rows -> LEDGER_PNL_UNREADABLE, AMBER entry block) and the
    trade is non-countable (live_exit_fill.exit_fill_non_countable_reason, R17)."""
    # HM R5 nit 6: the entry is the real entry fill only (live_exit_fill.entry_fill_price); no limit-price fallback
    entry, exit_, qty = _lef.entry_fill_price(c), _lef.exit_fill_price(c), _f(c.get("fill_qty"))
    side = str(c.get("side") or c.get("direction") or "").strip().upper()
    # HM R6 B1 / RK 10:26: without Bitfinex's exit fill time the trade cannot be placed in a cap window => UNREADABLE
    if entry and exit_ and _lef.exit_fill_ts(c) and qty and qty > 0 and side in ("LONG", "BUY", "SHORT", "SELL"):
        sgn = 1.0 if side in ("LONG", "BUY") else -1.0
        return (exit_ - entry) * qty * sgn
    return None


def daily_realised_loss(chains: Iterable[Mapping[str, Any]], now: float,
                        registry: Mapping[str, Mapping] | None = None) -> tuple[dict, float, int]:
    """({tile_key|None: loss_usd}, total_loss_usd, n_rows) for live chains CLOSED in the current UTC day.

    Realised trade P&L from trade_realised_pnl_usd (REAL Bitfinex fills; no funding, no fees: RK D3 06:26). Loss = -sum(pnl),
    so wins on the same tile/day offset losses. A closed row's time is its real Bitfinex exit fill time (HM R6 B1); without one its
    pnl is unreadable (never 'now'). Rows whose tile cannot be attributed count only towards the total."""
    day = utc_day(now)
    per: dict = {}
    n = 0
    for c in chains or []:
        if not c.get("closed"):
            continue
        pnl = trade_realised_pnl_usd(c)
        if pnl is None:
            continue   # never silently: unreadable_pnl_rows() turns it into an entry block (HM N1)
        t = _lef.exit_fill_ts(c)   # HM R6 B1: the real fill time (always set when pnl is readable); never 'now'
        if t is None or t > now + 60 or utc_day(t) != day:
            continue
        k = chain_tile_key(c, registry)
        per[k] = per.get(k, 0.0) - pnl
        n += 1
    return per, sum(per.values()), n


VIOL_PNL_UNREADABLE = "LEDGER_PNL_UNREADABLE"


def unreadable_pnl_rows(chains: Iterable[Mapping[str, Any]], now: float) -> list:
    """HM N1: live chains CLOSED in the current UTC day (or with no readable close time) whose realised pnl is missing or
    unreadable. They cannot be summed, so they fail CLOSED: an AMBER entry block (AMEND4 E1), never silently skipped and
    never a disarm."""
    day = utc_day(now)
    bad = []
    for c in chains or []:
        if not c.get("closed") or trade_realised_pnl_usd(c) is not None:
            continue
        t = _lef.exit_fill_ts(c)   # HM R6 B1: no real fill time => unreadable, blocks until the relay re-stamps it
        if t is None or (t <= now + 60 and utc_day(t) == day):
            bad.append(c)
    return bad


def daily_loss_violations(chains: Iterable[Mapping[str, Any]], now: float,
                          registry: Mapping[str, Mapping] | None = None) -> list:
    """HM G6 loss caps ([{code, tile, detail}]) on REALISED loss. AMEND4 E1: every one of these is an ENTRY BLOCK (AMBER), never
    a disarm/close. Fail safe: any error is itself a block, and so is a closed trade of the day whose pnl is unreadable (N1)."""
    try:
        chains = list(chains or [])
        per, total, n = daily_realised_loss(chains, now, registry)
        out = []
        for c in unreadable_pnl_rows(chains, now)[:4]:
            out.append({"code": VIOL_PNL_UNREADABLE, "tile": chain_tile_key(c, registry),
                        "detail": f"closed live trade {c.get('correlation_id') or c.get('trade_id')} has no real exit fill "
                                  f"(price_source={c.get('exit_price_source') or 'none'}): D3 unreadable, non-countable"})
        for k, loss in sorted(per.items(), key=lambda kv: str(kv[0])):
            if k is not None and loss >= DAILY_LOSS_CAP_PER_TILE_USD - 1e-9:
                out.append({"code": VIOL_TILE_LOSS_CAP, "tile": k,
                            "detail": f"{k} realised loss ${loss:.4f} >= ${DAILY_LOSS_CAP_PER_TILE_USD:.2f} UTC {utc_day(now)}"})
        if total >= DAILY_LOSS_CAP_TOTAL_USD - 1e-9:
            out.append({"code": VIOL_TOTAL_LOSS_CAP, "tile": None,
                        "detail": f"total realised loss ${total:.4f} >= ${DAILY_LOSS_CAP_TOTAL_USD:.2f} UTC {utc_day(now)} ({n} rows)"})
        return out
    except Exception as exc:  # noqa: BLE001
        return [{"code": f"{VIOL_TOTAL_LOSS_CAP}_ERROR", "tile": None, "detail": f"{type(exc).__name__}: {str(exc)[:120]}"}]


FORCE_FLAT_CLOSE_MARKERS = ("AUTHENTICATED_EMERGENCY_CLOSE", "FORCE_FLAT", "GUARDIAN_FORCE_FLAT", STOP_FAILURE_FLATTEN,
                            LIQ_CHECK_FLATTEN, LIQ_CLAMP_FLATTEN)   # AMEND5 G3 / G4(b): non-countable, counted


def force_flat_non_countable(chain: Mapping[str, Any] | None) -> bool:
    """RK (24 Oct): a live trade closed by the guardian force-flat (relay ops/force-flat-disarm -> emergency close) is
    NON-COUNTABLE for any gate/strategy evidence. Matched on the chain's close reason/source; fail-safe False only for
    a chain with no close info at all."""
    c = chain if isinstance(chain, Mapping) else {}
    blob = " ".join(str(c.get(k) or "") for k in ("close_reason", "exit_reason", "close_source", "exit_source", "reason")).upper()
    return any(m in blob for m in FORCE_FLAT_CLOSE_MARKERS)


def liq_too_close(stop_liq_bp: float | None) -> bool:
    """THE shared liq check (R4; RK L27): the REAL L is <= 15 bp (inclusive) past the exchange stop. One implementation used
    by E5/G4 (g4_outcome), G3 (open_position_protection) and the arm gate (l27_arm_block). Unknown distance => False here;
    each caller fails closed on an unreadable L on its own terms (G3: LIQ_UNREADABLE)."""
    d = _f(stop_liq_bp)
    return d is not None and d <= LIQ_CHECK_MAX_BP


def stop_amendment_sha(value: Any) -> str | None:
    """The amendment sha in a LIVE_STOP_AMENDMENT_PASSED value (``<sha>`` or ``PASS:<sha>``), lower-case hex 8-64; else None."""
    import re as _re
    v = str(value or "").strip()
    if v.upper().startswith("PASS:"):
        v = v[5:].strip()
    v = v.lower()
    return v if _re.fullmatch(r"[0-9a-f]{8,64}", v) else None


def stop_amendment_signed(sha: str | None, signed: Mapping[str, Mapping[str, str]] | None = None) -> bool:
    """RK (2): the sha is frozen in SIGNED_STOP_AMENDMENTS with BOTH an RK and an HM signature reference."""
    rec = (SIGNED_STOP_AMENDMENTS if signed is None else signed).get(sha or "") if sha else None
    return (isinstance(rec, Mapping) and bool(str(rec.get("rk") or "").strip()) and bool(str(rec.get("hm") or "").strip()))


def parse_signed_stop_amendments(tracker_text: str) -> dict:
    """{sha: {"rk": ref, "hm": ref}} from SHARED-TRACKER lines ``STOP-AMENDMENT-SIGNED sha=<sha> RK=<ref> HM=<ref>``.
    A line missing either signature is ignored (not signed)."""
    import re as _re
    out: dict = {}
    for m in _re.finditer(r"STOP-AMENDMENT-SIGNED\s+sha=(\S+)\s+RK=(\S+)\s+HM=(\S+)", str(tracker_text or "")):
        sha = stop_amendment_sha(m.group(1))
        if sha and m.group(2).strip() and m.group(3).strip():
            out[sha] = {"rk": m.group(2).strip(), "hm": m.group(3).strip()}
    return out


def l27_amendment_refusal(stop_amendment: Any, amendment_pass: Mapping[str, Any] | None,
                          latest_l: Mapping[str, Any] | None = None,
                          signed: Mapping[str, Mapping[str, str]] | None = None) -> str | None:
    """Why a stop-amendment pass does NOT lift L27 (None = it does). The env value must be a signed sha (RK + HM), the
    persisted pass record must carry the SAME sha, a named actor and a ts newer than the latest too-close L, and the sha must
    differ from the one in force when that L was recorded."""
    sha = stop_amendment_sha(stop_amendment)
    if sha is None:
        return AMENDMENT_ENV_INVALID
    if not stop_amendment_signed(sha, signed):
        return AMENDMENT_UNSIGNED
    rec = amendment_pass if isinstance(amendment_pass, Mapping) else None
    if not rec:
        return AMENDMENT_PASS_MISSING
    if stop_amendment_sha(rec.get("sha")) != sha:
        return AMENDMENT_MISMATCH
    ts = _f(rec.get("ts"))
    if not str(rec.get("actor") or "").strip() or ts is None or ts <= 0:
        return AMENDMENT_UNSTAMPED
    if isinstance(latest_l, Mapping):
        lts = _f(latest_l.get("ts"))
        if lts is None or ts <= lts:
            return AMENDMENT_PREDATES_L
        if stop_amendment_sha(latest_l.get("amendment_at_record")) == sha:
            return AMENDMENT_NOT_NEW
    return None


def l27_arm_block(latest_l: Mapping[str, Any] | None, stop_amendment: str | None = None,
                  amendment_pass: Mapping[str, Any] | None = None,
                  signed: Mapping[str, Mapping[str, str]] | None = None) -> str | None:
    """RK L27 (24 Oct): refuse arming while the latest recorded real L is too close (liq_too_close) and no NEW signed stop
    amendment has passed since it was recorded (HM C-R3-2 + RK (2): l27_amendment_refusal over the explicit persisted pass
    record). No estimate is ever made; with no recorded L there is nothing to refuse on."""
    if not isinstance(latest_l, Mapping) or not liq_too_close(latest_l.get("stop_liq_distance_bp")):
        return None
    if l27_amendment_refusal(stop_amendment, amendment_pass, latest_l, signed) is None:
        return None
    return DENY_L27_LATEST_L


def g4_outcome(stop_liq_bp: float | None) -> str | None:
    """AMEND5 G4 by the real L distance past the exchange stop (bp): (c') <= 15 (inclusive), (b) 15 < d < 28, (a) >= 28."""
    d = _f(stop_liq_bp)
    if d is None:
        return None
    if liq_too_close(d):
        return G4_C
    return G4_B if d < LIQ_NORMAL_MIN_BP else G4_A


def paper_only_arm_block(lane: str | None = None, tile_key: str | None = None) -> str | None:
    """Fail-closed live refusal for a paper-only tile (gf3), by lane name OR tile key: refused before any other arm gate,
    so LIVE_HM_GATE_FADE3 or a first L can never arm it."""
    if str(lane or "").upper() in PAPER_ONLY_LANES or str(tile_key or "").lower() in PAPER_ONLY_TILE_KEYS:
        return DENY_PAPER_ONLY_TILE
    return None


def arm_gate_block(tile_key: str | None, g4_first_l: Mapping[str, Any] | None, hm_gate: str | None = None,
                   latest_l: Mapping[str, Any] | None = None, stop_amendment: str | None = None,
                   amendment_pass: Mapping[str, Any] | None = None) -> str | None:
    """AMEND5 G4(c') + G6 arm/entry gate (pure, shared). GS-01 arms first (its first L is unknown until it trades); after a
    G4(c') reading GS-01 and FADE3 stay disarmed (STOP_REEVAL). FADE3 needs a first L in (a)/(b) AND HM's live gate pass.
    RK L27: every tile is refused while the latest recorded real L is too close and no new stop amendment has passed."""
    l27 = l27_arm_block(latest_l, stop_amendment, amendment_pass)
    if l27:
        return l27
    out = (g4_first_l or {}).get("outcome") if isinstance(g4_first_l, Mapping) else None
    if out == G4_C and tile_key in G4C_DISARMED_TILES:
        return DENY_G4_STOP_REEVAL
    if tile_key == G6_GATED_TILE:
        if out not in (G4_A, G4_B):
            return DENY_G6_NO_FIRST_L
        if not str(hm_gate or "").strip().upper().startswith("PASS"):
            return DENY_G6_HM_GATE
    return None


def liq_clamp_rate_violations(chains: Iterable[Mapping[str, Any]], liq_events: Iterable[Mapping[str, Any]],
                              registry: Mapping[str, Mapping] | None = None) -> list:
    """AMEND5 G4(b) (L23): per tile, LIQ_CLAMP_FLATTEN events / closed live trades > 5 % at n >= 20 => disarm ALL."""
    n: dict = {}
    for c in chains or []:
        if (c or {}).get("closed") and (c.get("fill_at_ts") or c.get("fill_price")):
            k = chain_tile_key(c, registry)
            n[k] = n.get(k, 0) + 1
    k_clamp: dict = {}
    for e in liq_events or []:
        if (e or {}).get("code") == LIQ_CLAMP_FLATTEN:
            k_clamp[e.get("tile")] = k_clamp.get(e.get("tile"), 0) + 1
    out = []
    for k, cnt in k_clamp.items():
        tot = n.get(k, 0)
        if tot >= LIQ_CLAMP_MIN_N and cnt / tot > LIQ_CLAMP_RATE_MAX:
            out.append({"code": LIQ_CLAMP_RATE, "tile": k,
                        "detail": f"{k} LIQ_CLAMP_FLATTEN {cnt}/{tot} = {cnt / tot:.1%} > {LIQ_CLAMP_RATE_MAX:.0%} at n >= {LIQ_CLAMP_MIN_N} (AMEND5 G4(b))"})
    return out


# ---- AMEND4 shared functions (one implementation for every live tile, R4) -------------------------------------------------

def cap_breach_response() -> str:
    """THE switchable daily-cap breach response. AMEND4 E1 (L12): entry block only (no close, no disarm)."""
    return CAP_RESPONSE_ENTRY_BLOCK


def book_age_s(book: Mapping[str, Any] | None, now: float) -> float | None:
    """Age of a paper_fill.book10l_at row: a row stamped s is complete at s + 1 (None = unreadable)."""
    s = _f((book or {}).get("s")) if isinstance(book, Mapping) else None
    return None if s is None else float(now) - (s + 1.0)


def position_side(p: Mapping[str, Any] | None) -> str | None:
    p = p if isinstance(p, Mapping) else {}
    amt = _f(p.get("amount"))
    if amt is not None and amt != 0.0:
        return "LONG" if amt > 0 else "SHORT"
    sd = str(p.get("side") or "").upper()
    return sd if sd in ("LONG", "SHORT") else None


def book_exit_walk(side: str, qty: float, book: Mapping[str, Any] | None, now: float) -> dict:
    """Full-size EXIT-side walk through the shared book helper (paper_fill.taker_side_levels + _walk): exit LONG sells into
    the bid, exit SHORT buys the ask. Never the ticker / mark (R3). ok only for a full fill on a readable book."""
    import paper_fill as _pf
    q = abs(_f(qty) or 0.0)
    out = {"ok": False, "vwap": None, "filled": 0.0, "qty": q, "book_s": None, "book_age_s": None, "error": None}
    if not isinstance(book, Mapping) or q <= 0 or side not in ("LONG", "SHORT"):
        out["error"] = "NO_BOOK" if not isinstance(book, Mapping) else "BAD_INPUT"
        return out
    out["book_s"], out["book_age_s"] = book.get("s"), book_age_s(book, now)
    if out["book_age_s"] is None:
        out["error"] = "BOOK_TS_UNREADABLE"
        return out
    vwap, filled = _pf._walk(_pf.taker_side_levels(side, "exit", book), q)
    out.update(vwap=vwap, filled=filled)
    if vwap is None or filled < q - 1e-12:
        out["error"] = "PARTIAL_DEPTH"
        return out
    out["ok"] = True
    return out


def open_mark_to_book_loss(position: Mapping[str, Any], book: Mapping[str, Any] | None, now: float) -> tuple[float | None, dict]:
    """(loss_usd, walk) of one open position marked to the full-size exit-side book walk (AMEND4 E1). None = unreadable."""
    side, amt, entry = position_side(position), _f((position or {}).get("amount")), _f((position or {}).get("base_price"))
    walk = book_exit_walk(side or "", abs(amt or 0.0), book, now)
    if not walk["ok"] or entry is None or amt is None:
        return None, walk
    pnl = (walk["vwap"] - entry) * abs(amt) * (1.0 if side == "LONG" else -1.0)
    return -pnl, walk


def overshoot_bp(side: str, stop_level: float | None, px: float | None) -> float | None:
    """AMEND4 A2 E9 (L36): bp past the stop level, signed so that POSITIVE = WORSE (LONG: below the stop; SHORT: above)."""
    s, p = _f(stop_level), _f(px)
    if s is None or p is None or s <= 0 or side not in ("LONG", "SHORT"):
        return None
    return round(((s - p) if side == "LONG" else (p - s)) / s * 1e4, 3)


def backstop_level(side: str, entry: float | None, stop_level: float | None, liq_price: float | None) -> dict:
    """AMEND5 F1/F2 (L6-7, on AMEND4 E2' L20-22): the CLOSER to entry of (stop + STOP_BACKSTOP_PAST_STOP_BP = 18 bp past
    it) and (REAL liq price - 10 bp toward entry). The liq leg is an outer limit, never a minimum distance (F2)."""
    s, lq = _f(stop_level), _f(liq_price)
    out = {"stop_leg": None, "liq_leg": None, "level": None, "binding": None}
    if side not in ("LONG", "SHORT") or s is None or s <= 0:
        return out
    sign = 1.0 if side == "LONG" else -1.0   # LONG: adverse is down
    out["stop_leg"] = s * (1.0 - sign * STOP_BACKSTOP_PAST_STOP_BP / 1e4)
    if lq is not None and lq > 0:
        out["liq_leg"] = lq * (1.0 + sign * STOP_BACKSTOP_LIQ_BUFFER_BP / 1e4)
    legs = [(out["stop_leg"], f"STOP+{STOP_BACKSTOP_PAST_STOP_BP:g}BP")] + ([(out["liq_leg"], "LIQ-10BP")] if out["liq_leg"] is not None else [])
    # closer to entry == the higher level for a LONG, the lower for a SHORT
    lvl, which = (max if side == "LONG" else min)(legs, key=lambda x: x[0])
    out["level"], out["binding"] = lvl, which
    return out


def stop_liq_distance_bp(side: str, stop_level: float | None, liq_price: float | None) -> float | None:
    """bp between the exchange stop and the real liq price on the adverse side (AMEND4 E5 L27; E8 records the first one)."""
    s, lq = _f(stop_level), _f(liq_price)
    if s is None or lq is None or s <= 0 or lq <= 0 or side not in ("LONG", "SHORT"):
        return None
    return round(((s - lq) if side == "LONG" else (lq - s)) / s * 1e4, 3)


def resident_stops(orders: Iterable[Mapping[str, Any]] | None) -> list:
    """Exchange-resident protective stops: reduce-only STOP orders (flags & 1024 read back from Bitfinex)."""
    return [o for o in (orders or []) if isinstance(o, Mapping) and o.get("reduce_only") is True and "STOP" in str(o.get("type") or "").upper()]


# #852 E6 timing budget (seconds), mirrored from the relay's STOP_EVIDENCE_TIMING (signal-subscriber-execution.service.ts):
# relay republishes stop evidence at 1.75 s of age (read SEND time), due-checked every 0.25 s; orders+positions reads
# serialized 2 x 0.6 s p95; DB write 0.25 s; Fly re-fetches on every 1 s guardian tick (cache ttl 0.5 s), HTTP 0.5 s.
STOP_EVIDENCE_TIMING = {"relay_every_s": 1.75, "due_check_s": 0.25, "read_s": 1.2, "write_s": 0.25, "fly_http_s": 0.5}


def stop_evidence_worst_case_age_s(t: Mapping[str, float] | None = None) -> float:
    """Worst-case evidence age the guardian computes under normal cadence (must stay < STOP_RESIDENT_MAX_AGE_S)."""
    t = dict(STOP_EVIDENCE_TIMING if t is None else t)
    return round(sum(float(t[k]) for k in ("relay_every_s", "due_check_s", "read_s", "write_s", "fly_http_s")), 3)


def mirror_evidence_age_s(mirror: Mapping[str, Any] | None, now: float) -> float | None:
    """Age of the relay's exchange read: read_age_s at Fly's fetch + time since that fetch (fetched_at_ts), when given."""
    m = mirror if isinstance(mirror, Mapping) else {}
    age = _f(m.get("read_age_s"))
    if age is None or age < 0:
        return None
    fetched = _f(m.get("fetched_at_ts"))
    return age + (max(0.0, float(now) - fetched) if fetched is not None else 0.0)


def open_position_protection(mirror: Mapping[str, Any] | None, book: Mapping[str, Any] | None, now: float,
                             fill_grace: bool = False) -> dict:
    """AMEND4 E2'/E5/E6/E7 for the open live position(s) in the relay mirror (pure, shared for every tile).

    Returns {"disarm": [violations => RED + disarm ALL + reduce-only force-flat, non-countable], "block": [AMBER entry
    blocks], "flatten_position": [AMEND5 G4(b) LIQ_CLAMP_FLATTEN: this position only, no disarm], "positions": [per-position
    evidence incl. backstop level, walk, overshoot_bp, real liq distance, g4 outcome]}. ``fill_grace``: a
    live chain filled < 5 s ago (HM G6 fill->stop bound) - only the missing-stop rule waits for it."""
    m = mirror if isinstance(mirror, Mapping) else {}
    res: dict = {"disarm": [], "block": [], "flatten_position": [], "positions": []}
    age_book = book_age_s(book, now) if isinstance(book, Mapping) else None
    if age_book is None or age_book > BOOK_ENTRY_MAX_AGE_S:
        res["block"].append({"code": AMBER_BOOK_STALE, "tile": None, "detail": f"book age {age_book} s > {BOOK_ENTRY_MAX_AGE_S} s (E7)"})
    if m.get("ok") is not True:
        return res   # blind mirror: the guardian's EXCHANGE_MIRROR_BLIND rule owns this (no position evidence to act on)
    orders = list(m.get("open_orders") or [])
    stops = resident_stops(orders)
    ev_age = mirror_evidence_age_s(m, now)
    for p in m.get("positions") or []:
        p = p if isinstance(p, Mapping) else {}
        side, amt = position_side(p), _f(p.get("amount"))
        if amt == 0.0:
            continue   # a zero row is no position (flat_check agrees); an unreadable amount is NOT skipped
        entry = _f(p.get("base_price"))
        stop = None
        if stops and side:
            # the protective stop sits on the adverse side; the one closest to entry is the binding exchange stop
            px = [x for x in (_f(o.get("price")) for o in stops) if x]
            stop = (max(px) if side == "LONG" else min(px)) if px else None
        liq = _f(p.get("liq_price"))
        liq_ok = liq is not None and liq > 0 and p.get("liq_read_at") is not None
        row = {"side": side, "amount": amt, "entry": entry, "stop_level": stop, "liq_price": liq if liq_ok else None,
               "liq_read_at": p.get("liq_read_at"), "evidence_age_s": ev_age, "book_age_s": age_book,
               "stop_liq_distance_bp": stop_liq_distance_bp(side or "", stop, liq) if liq_ok else None}
        res["positions"].append(row)
        # E6 (L28): resident stop confirmed on an exchange read <= 5 s old end to end (RK 24 Oct); missing => flatten
        if stop is None and not fill_grace:
            res["disarm"].append({"code": STOP_FAILURE_FLATTEN, "tile": None, "cause": "STOP_NOT_RESIDENT",
                                  "detail": f"open {side} {amt} with no resident reduce-only stop on the exchange (E6)"})
        # RK ruling (24 Oct, AMEND4 E6): stop evidence must be <= 5 s old (end to end); the 10 s bound is ONLY the E7 book age
        # while holding. Older evidence => STOP_FAILURE_FLATTEN (RED => flatten + disarm ALL).
        if ev_age is None or ev_age > STOP_RESIDENT_MAX_AGE_S:
            res["disarm"].append({"code": STOP_FAILURE_FLATTEN, "tile": None, "cause": "STOP_RESIDENCY_UNCONFIRMED",
                                  "detail": f"stop residency evidence age {ev_age} s > {STOP_RESIDENT_MAX_AGE_S:g} s (E6)"})
        # AMEND5 G3 (L18): right after every live fill the REAL L (G5: Bitfinex only, never estimated) is read with its time.
        # Unreadable, or <= 15 bp past the exchange stop (inclusive) => LIQ_CHECK_FLATTEN: reduce-only flatten + disarm ALL.
        # ``fill_grace`` (fill < 5 s ago, HM G6 fill->stop bound) only covers the first relay read of L / the stop.
        row["liq_source"] = LIQ_SOURCE if liq_ok else None
        row["g4_outcome"] = g4_outcome(row["stop_liq_distance_bp"])
        if not liq_ok and not fill_grace:
            res["disarm"].append({"code": LIQ_CHECK_FLATTEN, "tile": None, "cause": "LIQ_UNREADABLE",
                                  "detail": f"position liq_price={p.get('liq_price')!r} read_at={p.get('liq_read_at')!r} unreadable (AMEND5 G3)"})
        elif liq_ok and liq_too_close(row["stop_liq_distance_bp"]):
            res["disarm"].append({"code": LIQ_CHECK_FLATTEN, "tile": None, "cause": "LIQ_LE_15BP_PAST_STOP",
                                  "stop_liq_distance_bp": row["stop_liq_distance_bp"],
                                  "detail": f"real L {liq} is {row['stop_liq_distance_bp']} bp past stop {stop} <= {LIQ_CHECK_MAX_BP:g} bp (AMEND5 G3)"})
        # E7 (L31-32): hold only with a resident stop AND book <= 10 s
        if age_book is None or age_book > BOOK_HOLD_MAX_AGE_S:
            res["disarm"].append({"code": STOP_FAILURE_FLATTEN, "tile": None, "cause": "BOOK_STALE_GT_10S",
                                  "detail": f"position open and book age {age_book} s > {BOOK_HOLD_MAX_AGE_S} s (E7)"})
            continue
        # E2' (L20-23): backstop on the full-size exit-side book walk
        bs = backstop_level(side or "", entry, stop, liq if liq_ok else None)
        walk = book_exit_walk(side or "", abs(amt or 0.0), book, now)
        row.update(backstop=bs, walk_vwap=walk["vwap"], walk_ok=walk["ok"])
        if stop is not None and bs["level"] is not None:
            if not walk["ok"]:
                res["disarm"].append({"code": STOP_FAILURE_FLATTEN, "tile": None, "cause": "BOOK_WALK_UNREADABLE",
                                      "detail": f"exit-side walk {walk['error']} filled {walk['filled']}/{walk['qty']} (E7 read failure)"})
                continue
            v = walk["vwap"]
            hit = v <= bs["level"] if side == "LONG" else v >= bs["level"]
            row["overshoot_bp"] = overshoot_bp(side or "", stop, v)
            if hit and bs["binding"] == "LIQ-10BP" and ev_age is not None and ev_age <= STOP_RESIDENT_MAX_AGE_S:
                # AMEND5 G4(b) (L22): the L-10 clamp fired with the exchange stop confirmed resident (no stop failure) =>
                # flatten THIS position only, NO disarm; LIQ_CLAMP_FLATTEN, non-countable and counted (rate rule: G4(b) L23)
                res["flatten_position"].append({"code": LIQ_CLAMP_FLATTEN, "tile": None, "cause": "BACKSTOP_LIQ-10BP",
                                                "overshoot_bp": row["overshoot_bp"], "stop_liq_distance_bp": row["stop_liq_distance_bp"],
                                                "detail": f"{side} exit walk {v:.2f} past L-10 clamp {bs['level']:.2f}; stop {stop} resident, "
                                                          f"L {liq} (AMEND5 G4(b): this position only, no disarm)"})
            elif hit:
                res["disarm"].append({"code": STOP_FAILURE_FLATTEN, "tile": None, "cause": "BACKSTOP_" + str(bs["binding"]),
                                      "overshoot_bp": row["overshoot_bp"],
                                      "detail": f"{side} exit walk {v:.2f} past backstop {bs['level']:.2f} ({bs['binding']}); stop {stop}, "
                                                f"overshoot {row['overshoot_bp']} bp (E2')"})
    return res


def d3_open_mark_loss(mirror: Mapping[str, Any] | None, book: Mapping[str, Any] | None, now: float) -> tuple[float, list]:
    """RK D3 (24 Oct 06:26/07:57, R4): THE open-position term of the daily loss cap. (open_loss_usd, blocks): every open
    position in a fresh mirror marked to the book by a full-size exit-side walk; an unreadable mark or a book > 2 s old =>
    OPEN_MARK_UNREADABLE (BLOCK). No funding, no fees. Shared by daily_cap_entry_block (admit_entry) and d3_cap_violations
    (live_guardian)."""
    open_loss, blocks = 0.0, []
    m = mirror if isinstance(mirror, Mapping) else {}
    for p in (m.get("positions") or []) if m.get("ok") is True else []:
        if _f((p or {}).get("amount")) == 0.0:
            continue
        loss, walk = open_mark_to_book_loss(p, book, now)
        if loss is None or (walk.get("book_age_s") or 0) > BOOK_ENTRY_MAX_AGE_S:
            blocks.append({"code": "OPEN_MARK_UNREADABLE", "tile": None, "detail": f"open mark-to-book unreadable: {walk.get('error')} age {walk.get('book_age_s')}"})
            continue
        open_loss += loss
    return open_loss, blocks


def d3_cap_violations(chains: Iterable[Mapping[str, Any]], mirror: Mapping[str, Any] | None, book: Mapping[str, Any] | None,
                      now: float, registry: Mapping[str, Mapping] | None = None) -> tuple[list, dict]:
    """RK D3 (07:57): the ONE daily loss cap for the guardian = trade_realised_pnl_usd realised (UTC day) + d3_open_mark_loss.
    Same codes as daily_loss_violations (entry blocks, AMBER); the open mark counts against every tile (one live position
    account-wide, N1). (violations, info). No balance, no funding, no fees."""
    chains = list(chains or [])
    out = list(daily_loss_violations(chains, now, registry))
    per, total, n = daily_realised_loss(chains, now, registry)
    open_loss, blocks = d3_open_mark_loss(mirror, book, now)
    out.extend(blocks)
    have = {(v["code"], v.get("tile")) for v in out}
    if open_loss:
        for k in sorted({k for k in per if k is not None}, key=str):
            if per[k] + open_loss >= DAILY_LOSS_CAP_PER_TILE_USD - 1e-9 and (VIOL_TILE_LOSS_CAP, k) not in have:
                out.append({"code": VIOL_TILE_LOSS_CAP, "tile": k, "detail": f"{k} realised+open ${per[k] + open_loss:.4f} >= ${DAILY_LOSS_CAP_PER_TILE_USD:.2f}"})
        if open_loss >= DAILY_LOSS_CAP_PER_TILE_USD - 1e-9 and (VIOL_TILE_LOSS_CAP, None) not in have:
            out.append({"code": VIOL_TILE_LOSS_CAP, "tile": None, "detail": f"open mark-to-book ${open_loss:.4f} >= ${DAILY_LOSS_CAP_PER_TILE_USD:.2f} (every tile)"})
        if total + open_loss >= DAILY_LOSS_CAP_TOTAL_USD - 1e-9 and (VIOL_TOTAL_LOSS_CAP, None) not in have:
            out.append({"code": VIOL_TOTAL_LOSS_CAP, "tile": None, "detail": f"total realised+open ${total + open_loss:.4f} >= ${DAILY_LOSS_CAP_TOTAL_USD:.2f}"})
    return out, {"realised_loss_total": round(total, 4), "open_mark_loss": round(open_loss, 4), "d3_loss_usd": round(total + open_loss, 4), "rows": n}


def daily_cap_entry_block(chains: Iterable[Mapping[str, Any]], mirror: Mapping[str, Any] | None, book: Mapping[str, Any] | None,
                          now: float, tile_key: str | None, registry: Mapping[str, Mapping] | None = None) -> tuple[list, dict]:
    """AMEND4 E1 (L12): (blocks, info) for a NEW live entry of ``tile_key``. Realised trade (trade_realised_pnl_usd: real fills, no funding, no fees) UTC-day loss
    plus the open mark-to-book loss of any live position (full-size exit-side book walk) vs $0.05/tile and $0.10 total.
    Unreadable realised pnl, an unreadable open mark or a book > 2 s => block (AMBER). Never a close, never a disarm."""
    chains = list(chains or [])
    per, total, n = daily_realised_loss(chains, now, registry)
    blocks = [{"code": v["code"], "tile": v.get("tile"), "detail": v.get("detail")} for v in daily_loss_violations(chains, now, registry)
              if v.get("tile") in (None, tile_key) or v["code"] == VIOL_PNL_UNREADABLE]
    open_loss, _mark_blocks = d3_open_mark_loss(mirror, book, now)   # RK D3 R4: the one open-mark term
    blocks.extend(_mark_blocks)
    tile_loss = per.get(tile_key, 0.0) + open_loss   # one live position account-wide (N1): its loss counts against every tile
    if open_loss and tile_loss >= DAILY_LOSS_CAP_PER_TILE_USD - 1e-9 and not any(b["code"] == VIOL_TILE_LOSS_CAP for b in blocks):
        blocks.append({"code": VIOL_TILE_LOSS_CAP, "tile": tile_key, "detail": f"{tile_key} realised+open ${tile_loss:.4f} >= ${DAILY_LOSS_CAP_PER_TILE_USD:.2f}"})
    if open_loss and total + open_loss >= DAILY_LOSS_CAP_TOTAL_USD - 1e-9 and not any(b["code"] == VIOL_TOTAL_LOSS_CAP for b in blocks):
        blocks.append({"code": VIOL_TOTAL_LOSS_CAP, "tile": None, "detail": f"total realised+open ${total + open_loss:.4f} >= ${DAILY_LOSS_CAP_TOTAL_USD:.2f}"})
    age = book_age_s(book, now) if isinstance(book, Mapping) else None
    if age is None or age > BOOK_ENTRY_MAX_AGE_S:
        blocks.append({"code": AMBER_BOOK_STALE, "tile": None, "detail": f"book age {age} s > {BOOK_ENTRY_MAX_AGE_S} s"})
    info = {"realised_loss_by_tile": {str(k): round(v, 4) for k, v in per.items()}, "realised_loss_total": round(total, 4),
            "open_mark_loss": round(open_loss, 4), "rows": n, "book_age_s": age, "response": cap_breach_response()}
    return blocks, info


def stop_fill_overshoots(chains: Iterable[Mapping[str, Any]]) -> list:
    """AMEND4 A2 E9 (L36): overshoot_bp of every closed live chain that exited on its exchange stop (stop level from
    STOP_CONFIRMED, fill from the authenticated close). Side is implied by the stop being below (LONG) / above (SHORT) the fill."""
    out = []
    for c in chains or []:
        if not c.get("closed") or "STOP" not in str(c.get("exit_reason") or c.get("close_reason") or "").upper():
            continue
        if force_flat_non_countable(c):
            continue   # a guardian/liq flatten (STOP_FAILURE_FLATTEN, LIQ_CHECK_FLATTEN, LIQ_CLAMP_FLATTEN) is no stop fill
        # HM R5 nit 7: the fill is the REAL exit fill (live_exit_fill.exit_fill_price), never the display exit_price
        stop, entry, px = _f(c.get("stop_price")), _f(c.get("fill_price")), _lef.exit_fill_price(c)
        side = None if stop is None or entry is None else ("LONG" if stop < entry else "SHORT")
        out.append({"trade_id": c.get("correlation_id"), "tile": chain_tile_key(c), "side": side, "stop_level": stop,
                    "fill_px": px, "overshoot_bp": overshoot_bp(side or "", stop, px)})
    return out


def skipped_daily_cap_count(decisions: Iterable[tuple], now: float, registry: Mapping[str, Mapping] | None = None) -> dict:
    """AMEND4 E1: LIVE_SKIPPED_DAILY_CAP decisions this UTC day, total and per tile (monitor route)."""
    day, per, n = utc_day(now), {}, 0
    for tid, r in decisions or []:
        r = r or {}
        t = _f(r.get("decided_at_ts"))
        if t is None or utc_day(t) != day or SKIP_DAILY_CAP not in (r.get("reasons") or []):
            continue
        k = tile_key_for_trade_id(tid) or tile_key_for_lane(r.get("lane"), registry)
        per[str(k)] = per.get(str(k), 0) + 1
        n += 1
    return {"utc_day": day, "count": n, "by_tile": per}


def non_countable_summary(chains: Iterable[Mapping[str, Any]], events: Iterable[Mapping[str, Any]] = (),
                          liq_events: Iterable[Mapping[str, Any]] = (),
                          stop_failure_cause_counts: Mapping[str, Any] | None = None) -> dict:
    """AMEND4 E2/E6/E7/A2: counts of non-countable force-flattened trades and STOP_FAILURE_FLATTEN events (each RED in the
    daily report), plus AMEND5 G3/G4(b) LIQ_CHECK_FLATTEN / LIQ_CLAMP_FLATTEN counts and the real stop-fill overshoot sample (E9/E10).

    HM R4 N6: ``by_cause`` counts every non-countable cause: STOP_FAILURE_FLATTEN:<cause> (e.g. E6_READ_TIMEOUT) from the
    UNCAPPED running per-cause counter (``stop_failure_cause_counts``; the event list itself keeps only the last 50), plus
    FORCE_FLAT and EXIT_FILL_UNREADABLE:<source> (HM R4 B1) over the closed chains."""
    chains = list(chains or [])
    nc = [c for c in chains if c.get("closed") and force_flat_non_countable(c)]
    ef = [(c, _lef.exit_fill_non_countable_reason(c)) for c in chains if c.get("closed")]
    ef = [(c, r) for c, r in ef if r]
    by_cause: dict = {}
    for k, v in (stop_failure_cause_counts or {}).items():
        try:
            by_cause[f"{STOP_FAILURE_FLATTEN}:{k}"] = int(v)
        except (TypeError, ValueError):
            continue
    if not stop_failure_cause_counts:   # no running counter (old state): fall back to the (capped) event list
        for e in events or []:
            if (e or {}).get("code", STOP_FAILURE_FLATTEN) == STOP_FAILURE_FLATTEN and (e or {}).get("cause") is not None:
                k = f"{STOP_FAILURE_FLATTEN}:{e.get('cause')}"
                by_cause[k] = by_cause.get(k, 0) + 1
    if nc:
        by_cause["FORCE_FLAT"] = len(nc)
    for _c, r in ef:
        by_cause[r] = by_cause.get(r, 0) + 1
    sff = [e for e in events or [] if (e or {}).get("code") == STOP_FAILURE_FLATTEN]
    liq = [e for e in liq_events or [] if isinstance(e, Mapping)]
    ov = [o for o in stop_fill_overshoots(chains) if o.get("overshoot_bp") is not None]
    return {"non_countable_trades": len(nc), "non_countable_ids": [c.get("correlation_id") for c in nc][:20],
            "by_cause": by_cause, "exit_fill_unreadable_trades": len(ef),
            "exit_fill_unreadable_ids": [c.get("correlation_id") for c, _r in ef][:20],
            "stop_failure_flatten_events": len(sff), "stop_failure_flatten_red": bool(sff),
            "real_stop_fills_measured": len(ov), "stop_overshoot_bp": [o["overshoot_bp"] for o in ov][-50:],
            "liq_check_flatten_events": sum(1 for e in liq if e.get("code") == LIQ_CHECK_FLATTEN),
            "liq_clamp_flatten_events": sum(1 for e in liq if e.get("code") == LIQ_CLAMP_FLATTEN),
            "stop_backstop_bp": STOP_BACKSTOP_PAST_STOP_BP, "amend5_sha256": AMEND5_SHA256, "stop_backstop_bp_status": (
                STOP_BACKSTOP_STATUS if len(ov) < 20 else
                "E10 RE-SET DUE (>= 20 real stop fills; either way per AMEND5 G1) once the receive-stamp PR #851 has landed (F4)")}
