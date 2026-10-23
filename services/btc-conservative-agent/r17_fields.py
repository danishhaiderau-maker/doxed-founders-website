"""R17 closed-row field contract (HM C4, 23 Oct 2026): the ONE list of closed-row fields a paper row must carry,
with the accepted positive values, before it can count. r17_exclusion imports it; the REALISM_V2 worker (#841)
copies these fields onto trade_row in bot.close_position. Mirror: /workspace/grokbot/r17-field-contract.md.

Sources at #841 realism-v2/rk-fixes @9f095dbf (what each field is copied FROM):
  trigger_price_source     pos["trigger_price_source"] (bot.py 5394/5498; quote_trigger.trigger_price_source)
  delay_source             entry_fill_sim.delay_source (already on the row via _entry_pricing_fields)
  exit_priced_delay_source pos["exit_priced_delay_source"] (_paper_exit_model_times, bot.py ~7681)
  entry_walk_bp / exit_walk_bp / exit_legs_unmeasured   _slippage_row_fields (already on the row)
  evidence_status          entry_fill_sim.evidence_status (already on the row, bot.py ~7858)
  entry_book_age_ms        entry_fill_sim.book_age_ms (paper_fill latest-row pricer; first-row legs: price_age_s*1000)
  exit_price_age_s         pos["exit_price_age_s"] (bot.py ~7739/7792)
  exit_book_age_ms         exit pricer book_age_ms (latest-row legs); alternative to exit_price_age_s (HM R3)
  exit_evidence_status     pos["exit_evidence_status"] (bot.py ~7793)
  exit_unverified_reason   pos["exit_unverified_reason"] (bot.py ~7740/7794)
  exit_pricing_basis       pos["exit_pricing_basis"] (already on the row)
  levels_crossed_unfired   NEW (REALISM_V2 C3/C4): list of stop/TP/BE levels crossed during the hold that did not fire
  stop_coverage            NEW (REALISM_V2 C3): STOP_COVERAGE_OK only when the trades tail covered the whole hold
  entry_book_receive_*     entry_fill_sim.book_receive_ts / _mono / _ts_source (paper_fill.book_receive_proof; RK/HM #851)
  exit_book_receive_*      exit_fill_sim.book_receive_ts / _mono / _ts_source (bot._r17_contract_fields)
  liq_unverified           pos["liq_unverified"] (bot._liquidation_stamp / _terminal_liq_fields; order_model.liquidation_check)
"""
from __future__ import annotations

MIN_POOL_N = 10  # paper_fill.MIN_POOL_N: below it the delay is UNVERIFIED_DELAY
RESIDENT_DELAY_SOURCE = "EXCHANGE_RESIDENT_NO_DELAY"
RESIDENT_EXIT_BASIS = "EXCHANGE_RESIDENT_FIRST_ROW_AT_OR_AFTER_TRIGGER"
MEASURED_DELAY_PATTERN = r"(?:lane|POOLED_LIVE_ACK_CROSS_LANE:(?:ENTRY_MKT_GTC|MAKER_REST|EXIT_RO_MKT))_(?:max|q90)_n(\d+)"  # RK A  # with N >= MIN_POOL_N, or RESIDENT_DELAY_SOURCE

TRIGGER_PRICE_SOURCE_OK = frozenset({"book"})
EVIDENCE_OK = frozenset({"COMPLETE"})
STOP_COVERAGE_OK = frozenset({"STOP_COVERAGE_OK"})
BOOK_RECEIVE_SOURCE_OK = frozenset({"WS_FRAME"})  # RK/HM #851: measured WS receipt; INFERRED_BUCKET_LAG never counts

# field -> accepted positive value (human/contract form; r17_exclusion implements the check)
CONTRACT = {
    "trigger_price_source": "book",
    "delay_source": f"lane_max_n<N> | lane_q90_n<N> | POOLED_LIVE_ACK_CROSS_LANE:<ENTRY_MKT_GTC|MAKER_REST|EXIT_RO_MKT>_<max|q90>_n<N> (N >= {MIN_POOL_N}) | {RESIDENT_DELAY_SOURCE}",
    "exit_priced_delay_source": f"same as delay_source; not required when exit_pricing_basis == {RESIDENT_EXIT_BASIS}",
    "entry_walk_bp": "number (not None/NaN)",
    "exit_walk_bp": "number (not None/NaN)",
    "exit_legs_unmeasured": "0 (missing fails)",
    "evidence_status": "COMPLETE",
    "entry_book_age_ms": "number >= 0",
    "exit_price_age_s": "finite number >= 0 (seconds); OR exit_book_age_ms below",
    "exit_book_age_ms": "finite number >= 0 (ms); accepted instead of exit_price_age_s for latest-row exit legs (TP/BE/time, BOT_SENT MODEL_TIME) whose pricer returns price_age_s=None but book_age_ms",
    "exit_evidence_status": "COMPLETE",
    "exit_unverified_reason": "absent / None / empty",
    "levels_crossed_unfired": "[] (real empty list) or False; a string such as 'None' fails",
    "stop_coverage": "STOP_COVERAGE_OK",
    "entry_book_receive_ts": "finite number > 0: MEASURED WS receive time (epoch s) of the entry book row (#851 collector)",
    "entry_book_receive_mono": "finite number >= 0: monotonic receive time of the same frame",
    "entry_book_receive_ts_source": "WS_FRAME (MISSING / INFERRED_BUCKET_LAG fail: pre-#851 rows are non-countable)",
    "exit_book_receive_ts": "finite number > 0: MEASURED WS receive time of the exit book row",
    "exit_book_receive_mono": "finite number >= 0",
    "exit_book_receive_ts_source": "WS_FRAME",
    # HM 02:55 nit: proof that the walked row was RECEIVED no later than the fill (receive_ts <= fill_ts by construction)
    "entry_book_receive_age_ms": "finite number >= 0 (ms): entry fill time - entry_book_receive_ts (age from receipt)",
    "exit_book_receive_age_ms": "finite number >= 0 (ms): exit fill time - exit_book_receive_ts (age from receipt)",
    "liq_unverified": "False (real bool or 'false') AND liq_mark_rows_n > 0 AND no liq_trigger_ts: the hold path is "
                      "proven clear of the liquidation distance (RK/HM K1, 24 Oct); True or missing fails",
    "liq_mark_rows_n": "number > 0 (the BFXCI mark series over the hold was read)",
    "liq_trigger_ts": "absent / None (neither the mark nor a last trade reached liq_px during the hold)",
}
# Matching is EXACT: string values are compared after stripping surrounding spaces only (no case folding);
# numbers must be finite and >= 0.
REQUIRED_FIELDS = tuple(CONTRACT)
