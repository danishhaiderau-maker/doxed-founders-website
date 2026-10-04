# FREEZE21-20261004 — 3 hypotheses + 1 control, 21-day research freeze

Owner approval: Danish, 2026-10-04 15:02 AEDT, recommendations in
`SYSTEM-REVIEW-20261004` (PR-A). **Paper only.** Every tile is relay-ineligible;
nothing here arms the relay or Bitfinex or touches live flags or keys.

## Roster (registry `v31-freeze21-3h1c-v12`)

Source of truth: `services/btc-conservative-agent/combo_pathway_config.py`
(`ACTIVE_TILE_REGISTRY`, `pre_registration` per tile). All four default ON.

| Tile | Role | Rule | Exit | Target n_eff (fills) |
|---|---|---|---|---|
| `FAMILY_COMMITTED_FADE_TAKER_90` (H-A, `cft-`) | hypothesis | Inverted committed score-led side (raw AI side == score-led side), Asia+EU only, taker at signal, spread <= 3 bp | late BE 20->5, ATR 1.5 trail armed at +2 ATR, conditional early cut -12%/5 min, 40 bp stop, **90-min time exit**, max 3 open | 150 h (300) |
| `FAMILY_NOTRADE_FOLLOW_TAKER_60` (H-B, `ntt-`) | hypothesis | Score-led side only when the raw AI said NO_TRADE, all sessions, **taker** (the maker version filled 0 of 17) | late BE + ATR trail, 40 bp stop, 60-min time exit, max 10 open | 150 h (600) |
| `FAMILY_PREMIUM_REVERSION_60M` (H-C, `pmr-`) | hypothesis, **no AI** | Binance/Bybit premium over Bitfinex vs its own 60-min mean; beyond the fixed MODEL-A tails (+1.75 / -1.88 bp) take the Bitfinex taker toward convergence; <= 1 entry / 15 min | 60-min time exit + 40 bp stop only | 150 h (300) |
| `FAMILY_RANDOM_CONTROL_TAKER_90` (`rnd-`) | **control** | Same committed calls, session gate, taker entry and exits as H-A; side = `sha256(salt|shared_call_id)[0]` even LONG / odd SHORT | identical to H-A | 150 h (300) |

Why H-C: the funding/premium family is the only non-AI idea with a fixed
threshold that was not fitted on this tape; the lead rule had a CI below 0 at
60 s, and the session-follow tile is retired with the old roster. Honest label:
DESCRIPTIVE (2.8 days of minute tape, no execution replay).

Retired (existing machinery: `RETIRED_TILE_LANES`, `RETIRED_POLICY_IDENTITIES`,
policy modules deleted, signal-engine mirror rejects them):
`FAMILY_DANISH_CF`, `FAMILY_DANISH_CF_NOES`, `FAMILY_DANISH_CF_ALL_SESSIONS`,
`FAMILY_CONTINUOUS_AUG_ORIGINAL`, `FAMILY_COMMITTED_FADE_MAKER_90`,
`FAMILY_NOTRADE_FOLLOW_MAKER_60`, `FAMILY_XVENUE_SESSION_FOLLOW_60M`.

## Pre-registered rules (`tile_paired_comparison.py` applies them)

n_eff = distinct UTC hours with at least one closed fill in the freeze epoch;
CI = 1 h-cluster bootstrap in `REALISTIC_V1`; Bonferroni over the 3 hypotheses
(per-test alpha 0.0167, i.e. 98.33% CI).

Kill rules (hypotheses; an owner decision, executed as a freeze-override
toggle OFF with reason `KILL_RULE:<lane>:<rule>`):

- **K1** after 80 distinct hours, mean <= 0 bp.
- **K3** any trade worse than -60 bp, or stale-feed fills > 1%.
- **K4** paper drawdown > $3 (H-A, H-C) / > $5 (H-B).
- **K6** lifecycle / identity / analyzer / feed / mirror defect = pause and
  quarantine, not a strategy verdict.
- The control has no performance kill; it runs while H-A runs.

Day-21 decision (anchor: `started_at` of the freeze data epoch):

- **PASS -> CONTINUE (owner review, never relay):** n_eff >= 150 and the 98.33%
  CI lower bound > 0 bp (H-A additionally: paired difference vs the control > 0).
- **FAIL -> RETIRE:** mean <= 0 bp, or CI upper bound < +2 bp.
- **INCONCLUSIVE:** anything else, including n_eff short of target.
- **Control:** report its mean/CI as the execution cost; if its CI lower bound
  is > 0 bp (a random side made money) -> `FILL_MODEL_SUSPECT`: pause verdicts
  and audit the fill model.

## The freeze guard (`research_freeze.py`)

- One declared epoch: `DATA_EPOCH_ID = "ce-20261004-v31-freeze21"` in
  `fly.toml`. Window = `[started_at, started_at + 21 d)` of that epoch's
  `data_epoch.json`. `/api/status.research_freeze` shows status, day, end.
- States: `NOT_STARTED` -> `OPENING` (first 60 min; the deploy workflow's one
  guarded boundary reset is allowed) -> `ACTIVE` -> `COMPLETE`; `LIFTED`
  (owner, code change); `EPOCH_MISMATCH` (running a different epoch: guarded).
- Refused with HTTP 409 `RESEARCH_FREEZE_ACTIVE` while guarded: every
  fresh-collection / epoch reset (`perform_fresh_collection_reset` and its API
  callers), turning a frozen tile OFF (non-roster and retired lanes are
  already refused by the toggle), and
  `clean_epoch_wipe.py --pre-start execute`. Turning a frozen tile back ON is
  always allowed.
- CI (`test_research_freeze.py`): fails any registry change (roster, order,
  stack version, signature in both score-led and hypothesis modes) and any
  `DATA_EPOCH_ID` change while `FREEZE_STATUS == "ACTIVE"`.
- Deploy gate (`scripts/fly_postdeploy_active_gate.py`, step "Prove paper
  active with every registry tile ON"): the running roster must equal the
  checked-out registry exactly (these four, in order), all four ON, no retired
  lane ON or listed.

### Documented override (the only way through)

- Request body: `"freeze_override": {"confirmation":
  "BREAK_21_DAY_RESEARCH_FREEZE", "reason": "<>= 10 chars>"}` on
  `/api/toggle_research_lane` or the reset endpoints.
- Or environment: `RESEARCH_FREEZE_OVERRIDE=BREAK_21_DAY_RESEARCH_FREEZE` and
  `RESEARCH_FREEZE_OVERRIDE_REASON=...` (bot, `clean_epoch_wipe.py`, or the
  deploy gate when a held tile must go OFF).
- In code: set `research_freeze.CODE_OVERRIDE = {"approved_by", "approved_utc",
  "reason"}` (marks the freeze BROKEN for the record) or `FREEZE_STATUS =
  "LIFTED"` to end it. Owner approval required for all of these.

## Post-deploy verification

1. Deploy run green, including the gate step; its `tiles receipt` shows
   `lanes` = the four above, `roster_matches_checkout: true`, `retired_on: []`,
   `tiles_all_on: true`, `live_armed: false`, `bitfinex_live_enabled: false`.
2. `/api/status`: `active_tiles[*].lane` = the four; `research_freeze.status`
   `OPENING` then `ACTIVE`, `epoch_id` `ce-20261004-v31-freeze21`, `ends_at_utc`
   = epoch start + 21 d; `force_paper_mode: true`.
3. Within an hour: `cft-`/`rnd-` fills only inside Asia+EU hours, `ntt-` only on
   raw-AI NO_TRADE calls, `pmr-` at most 4 per hour.
4. Prove the guard: an unforced `POST /api/toggle_research_lane
   {"lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "enabled": false}` returns 409
   `RESEARCH_FREEZE_ACTIVE` (re-enable is not needed: nothing changed).
