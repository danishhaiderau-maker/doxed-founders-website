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
# HM (D) 24 Oct: the funding source is leg-specific. PAPER rows accept only the settlement-exact deriv-status model;
# a LIVE_LEDGER_V1 value on a paper row is rejected (FUNDING_UNVERIFIED). LIVE rows accept only the ledger read-back.
PAPER_FUNDING_MODEL_OK = frozenset({"SETTLEMENT_EXACT_V2"})   # HM #849 item 1: paper rows, settlement-exact funding
LIVE_FUNDING_MODEL_OK = frozenset({"LIVE_LEDGER_V1"})         # #849 RK 24 Oct: live rows, Bitfinex funding-ledger read-back
FUNDING_MODEL_OK = PAPER_FUNDING_MODEL_OK                      # r17_exclusion's paper-row gate
FUNDING_STATUS_OK = frozenset({"PRICED", "NO_CROSSING"})
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
    "funding_model": "SETTLEMENT_EXACT_V2 (legacy pro-rata accrual fails: FUNDING_UNVERIFIED; LIVE_LEDGER_V1 on a paper "
                     "row fails FUNDING_UNVERIFIED, HM D)",
    "funding_status": "PRICED | NO_CROSSING (UNVERIFIED_* -> FUNDING_UNVERIFIED; FUNDING_PRICER_ERROR -> FUNDING_PRICER_ERROR)",
    "funding_verified": "True (real bool or 'true'); False / missing fails FUNDING_UNVERIFIED (HM #849 item 1)",
    "funding_ts_fallbacks": "[] (real empty list): the window ends are the priced fill ts; any entry fails FUNDING_TS_FALLBACK",
}

# #849 RK ruling 24 Oct (PASS + 4 amendments): LIVE-row funding read-back (live_funding_ledger.verify). The four
# gating fields above apply unchanged with funding_model LIVE_LEDGER_V1; the rest are proof / metric fields (not gates).
LIVE_FUNDING_CONTRACT = {
    "funding_model": "LIVE_LEDGER_V1",
    "funding_status": "PRICED (every crossed settlement tied to exactly one category-29 ledger entry, or a proven ZERO_RATE "
                      "settlement per the RK zero-rate ruling) | NO_CROSSING (no "
                      "00/08/16 UTC settlement in (entry fill, exit fill] per tile_fill.settlements_crossed -> a countable 0); "
                      "UNVERIFIED_LEDGER_READ_FAILED | UNVERIFIED_SETTLEMENT_NO_ENTRY | UNVERIFIED_ENTRY_UNTIED | "
                      "UNVERIFIED_POSITION_UNRESOLVED | UNVERIFIED_AMBIGUOUS_ENTRY | UNVERIFIED_FILL_TS_MISSING | "
                      "UNVERIFIED_LEDGER_POSITION_MISMATCH (SIGNED: |pf - POSITION_FUNDING_SIGN * ledger sum| > 1e-8) | "
                      "UNVERIFIED_POSITION_FUNDING_SIGN_UNPROVEN (RK 04:09 + HM R4 F1: a non-zero tied entry while "
                      "POSITION_FUNDING_SIGN is unset - no RK+HM-confirmed ACTIVE pin) | "
                      "UNVERIFIED_POSITION_FUNDING_MISSING | UNVERIFIED_ZERO_RATE_READ_FAILED | "
                      "UNVERIFIED_ZERO_RATE_MISSING | UNVERIFIED_ZERO_RATE_POSITION_CHECK | PENDING_READBACK (HM B/C: not read yet by the background "
                      "job) fail",
    "funding_verified": "True only for PRICED / NO_CROSSING",
    "funding_usd_raw": "COST-positive USD = -sum(ledger amounts used); 0.0 for NO_CROSSING; null when unverified",
    "funding_ts_fallbacks": "[] unless the exit ts is not the Bitfinex exit fill record AND lies within 60 s of a settlement; "
                            "entry_ts:fill_at_ts_missing / exit_ts:exit_fill_missing when a fill ts is absent (HM E: "
                            "explicit flag, non-countable, the window is never guessed)",
    "funding_exit_ts_source": "exit_fill_record_at_ts (else the fallback stamp name)",
    "funding_ts_crossed": "list of settlement epoch s in (entry fill, exit fill]",
    "funding_ledger_ids": "Bitfinex ledger ids used (RK 4)",
    "funding_ledger_mts": "their MTS (epoch s)",
    "funding_ledger_amounts": "their raw amounts (received-positive, as Bitfinex books them)",
    "funding_usd_by_settlement": "[{settlement_ts, ledger_id, ledger_mts, funding_usd}]",
    "funding_settlement_status": "per crossed settlement: LEDGER | UNTIED | AMBIGUOUS | ZERO_RATE (no entry, counted 0) | "
                                 "RATE_READ_FAILED | RATE_MISSING | RATE_NONZERO | ZERO_RATE_POSITION_CHECK",
    "funding_settlement_rate_readbacks": "RK zero-rate ruling (24 Oct): per crossed settlement with NO ledger entry "
                                         "{settlement_ts, rate (text, exactly as returned), row_mts, read_at_ts, endpoint "
                                         "(api-pub v2/status/deriv/tBTCF0:USTF0/hist), error, status}. Counted 0 ONLY "
                                         "when CURRENT_FUNDING of the last row before that EXACT settlement (its next-"
                                         "funding ts == the settlement, confirmed by the flip) is exactly 0 as returned "
                                         "(Decimal parse, never rounded, never from a neighbour) AND positions/hist "
                                         "FUNDING for the pid EQUALS the sum of the tied ledger entries within 1e-8 "
                                         "(RK amendment 04:07 + RK 04:09 / HM R4 F1: SIGNED, never magnitudes - both ~0, or |pf - POSITION_FUNDING_SIGN * sum| <= 1e-8 under an ACTIVE sign). Every "
                                         "crossed settlement needs exactly one proof: a tied entry, or (no entry only) "
                                         "an exact-0 rate read - an entry present means no rate read is used; read in "
                                         "the background job under the "
                                         "same 12/min budget, never in a GET. Else LIVE_FUNDING_UNVERIFIED",
    "funding_position_id": "the Bitfinex position id (positions/hist, unique match to the fills) or null",
    "funding_readback_at_ts": "epoch s of the background job's ledger read (RK 4), cached per row; null until read",
    "funding_readback_endpoint": "v2/auth/r/ledgers/hist category=29 (BFX_FUNDING_READ_KEY). HM B/C: every ledger / "
                                 "positions / permissions read runs in ONE background owner thread (live_funding_ledger."
                                 "FundingReadbackJob) under the global 12 req/min budget with 429 backoff; GETs only read "
                                 "the per-row cache and never touch the network",
    "funding_readback_error": "redacted code (PENDING_READBACK / NO_READ / KEY_GATE:<gate> / HTTP_<n> incl. HTTP_429 / "
                              "TRANSPORT_<cls> / API_ERROR / LEDGER_PAGE_FULL) or null; never a key, signature or body",
    "funding_key_gate": "OK, else READ_KEY_MISSING | READ_KEY_NOT_SEPARATE | KEY_HAS_WRITE:<scopes> | "
                        "KEY_PERMISSIONS_READ_FAILED:<code> (RK 03:38: BFX_FUNDING_READ_KEY/SECRET, a separate Fly-only "
                        "key, permission read-back with NO write right; any refusal -> every row UNVERIFIED)",
    "funding_position_funding": "positions/hist FUNDING [4] of the tied position, raw (cross-check vs the ledger). "
                                "HM R4 F1/F2: verified iff |pf| <= 1e-8 AND |ledger sum| <= 1e-8, or the sign is ACTIVE and "
                                "|pf - POSITION_FUNDING_SIGN * ledger sum| <= 1e-8 (signed, no abs, no relative tolerance); "
                                "same rule on the zero-rate, tied-only and no-crossing paths",
    "funding_position_funding_sign": "+1 / -1 from an ACTIVE pin confirmed by BOTH an RK and an HM ref, else null "
                                     "(POSITION_FUNDING_SIGN = None: unproven -> non-zero rows UNVERIFIED)",
    "funding_sign_pin_status": "NONE | PROPOSED | ACTIVE | DISAGREEMENT | UNREADABLE (persisted live_funding_sign_pin.json "
                               "on the BOT_DATA_DIR root, outside every reset path; LIVE_FUNDING_SIGN_PIN_PATH overrides)",
    "funding_sign_pin_refs": "{rk, hm} confirmation refs of the ACTIVE pin, else null",
    "funding_sign_pin_flag": "SIGN_PIN_DISAGREEMENT when checks (a)/(b)/(c) disagreed (nothing pinned, row UNVERIFIED), "
                             "else null",
    "funding_sign_pin_check": "this row's proposal check, if run (only while no pin state exists, only for ONE closed "
                              "live position with a SINGLE tied non-zero cat-29 entry): {result AGREE|DISAGREE|"
                              "INSUFFICIENT, position_funding_sign, a: [{ledger_id, amount, balance [6], prev_ledger_id, "
                              "prev_balance, balance_change, ok}], b: [{settlement_ts, rate, rate_row_mts, side, "
                              "expected_sign (Bitfinex: rate > 0 -> longs pay shorts), ok}], c: {position_funding_raw, "
                              "ledger_amount_raw, sign, diff, ok}, trade_id, pid, ledger_ids, ledger_mts, settlement_ts, "
                              "checked_at_ts}. AGREE persists a PROPOSED pin; ACTIVE only via `live_funding_ledger.py "
                              "sign-pin confirm --rk-ref <RK> --hm-ref <HM>`, never automatically",
    "funding_fee_anomaly": "True when the read returned a fee-like / non-funding row: flagged for HM, NEVER applied (R7); "
                           "not a gate",
    "funding_fee_anomaly_ledger_ids": "their ledger ids",
    "funding_paper_usd": "the paper twin's settlement-exact funding (cost-positive) for the same signal",
    "funding_live_minus_paper_usd": "funding_usd_raw - funding_paper_usd: realism metric only, NEVER an exclusion (RK 3)",
}
# Matching is EXACT: string values are compared after stripping surrounding spaces only (no case folding);
# numbers must be finite and >= 0.
REQUIRED_FIELDS = tuple(CONTRACT)
