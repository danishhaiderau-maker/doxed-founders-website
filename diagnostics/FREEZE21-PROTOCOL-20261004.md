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

---

## FREEZE21B (re-declared 2026-10-04 17:53/17:54 AEDT)

Owner orders (Danish, 4 Oct 2026, relayed by the boss agent):

- 17:53 AEDT: add Grok Strategist's B rules (B1..B3,
  `PREREG-GS-20261004-B`) as visible paper tiles and start a fresh
  21-day epoch with them.
- 17:54 AEDT: scope change — "every strategy must be a visible paper
  tile": also add the four earlier shadow rules GS-20261004-01..04
  (`PREREG-GS-20261004`) as paper tiles. Eleven tiles in total.

`research_freeze.py` now declares `FREEZE21B-20261004` over data epoch
`ce-20261004-v31-freeze21b` (`fly.toml` `DATA_EPOCH_ID`), registry
`v31-freeze21b-11t-v13`. The `ce-20261004-v31-freeze21` epoch (started
05:46:11Z) ran about three hours and is quarantined like every earlier
cohort. The same guards, opening-hour boundary reset and override apply.

### Roster (display order)

| # | Lane | Prefix | Trigger | Entry | Exit |
|---|------|--------|---------|-------|------|
| 1 | `FAMILY_COMMITTED_FADE_TAKER_90` (H-A) | cft | shared AI, committed calls inverted | taker | v12 composite (unchanged) |
| 2 | `FAMILY_NOTRADE_FOLLOW_TAKER_60` (H-B) | ntt | shared AI, raw NO_TRADE | taker | v12 composite (unchanged) |
| 3 | `FAMILY_PREMIUM_REVERSION_60M` (H-C) | pmr | cross-venue premium (no AI) | taker | 60 min / 40 bp (unchanged) |
| 4 | `FAMILY_RANDOM_CONTROL_TAKER_90` (control) | rnd | H-A calls, coin side | taker | H-A's (unchanged) |
| 5 | `FAMILY_GS01_XV_PREMIUM_ATR_TP` (GS-01) | gs1 | cross-venue premium, own evaluator | taker, spread <= 3 bp | TP max(8, 2.5 ATR) maker; BE max(6, 2 ATR) -> +1; cut 8 bp/5 min; 35 bp; 60 min |
| 6 | `FAMILY_GS02_NOTRADE_REGIME_ENTRY` (GS-02) | gs2 | shared AI, raw NO_TRADE | QUIET taker; VIOLENT (ATR pct >= 66 or spread >= 2 bp) 1 ATR post-only limit, 25 % chase in windows 2-3, 30 min TTL | BE 1.5 ATR; trail 2 ATR armed at 2 ATR; cut; 35 bp; 60 min |
| 7 | `FAMILY_GS03_CVD_DIV_TAKER` (GS-03) | gs3 | 3 m CVD divergence event (no AI) | taker at bar close | GS-02's |
| 8 | `FAMILY_GS04_NOTRADE_ATR_TP` (GS-04) | gs4 | shared AI, raw NO_TRADE | taker | GS-01's |
| 9 | `FAMILY_GSB1_CVD_DIV_REGIME` (B1) | gb1 | 3 m CVD divergence | QUIET touch (re-peg 60 s, guarded taker fallback at 600 s); TREND aside; VIOLENT deep 0.75 ATR | MOM_QUIET / MOM_VIOLENT, flip = divergence |
| 10 | `FAMILY_GSB2_REGIME_SWITCHER` (B2) | gb2 | QUIET/VIOLENT: CVD divergence; TREND: committed fade | touch / 0.25 ATR offset / deep 0.75 ATR | REV_QUIET / MOM_TREND / REV_VIOLENT |
| 11 | `FAMILY_GSB3_COMMITTED_FADE_REGIME` (B3) | gb3 | shared AI, committed fade | touch / 0.25 ATR offset / deep 1.5 ATR | MOM_QUIET / MOM_TREND / MOM_VIOLENT, flip = CVD trend |

B regimes: VIOLENT if 3 m ATR percentile >= 80 or spread >= 3 bp, TREND if
ADX >= 25, else QUIET. All GS/B tiles: one open position, paper only, relay
ineligible, default ON, `max_active_signals = 1`.

### Pre-registration (each GS/B tile, `tile_pre_registration_gs20261004_v1`)

Target >= 30 fills and n_eff >= 30 distinct UTC close hours. Kill any time on
harm (after 30 fills mean <= -2 bp and CI95 upper < 0), give-back (MFE >= arm,
then closed <= 0 in > 25 % after 30 fills), and per rule: worst trade < -45 bp,
> 10 % of break-even-armed trades negative, signal->fill p50 > 5 s. Day 21:
PASS_FORWARD (mean > 0 and the one-sided 1 h-cluster lower bound at alpha
0.05/k > 0; k = 4 for GS, 3 for B), KILLED (futility or any kill) or
INSUFFICIENT. `tile_paired_comparison.py` scores this live. The fill-rate kill
(< 60 % after 40 signals) and the random-control edge (>= 2 bp) are scored
offline by Grok Strategist. Kill action: owner decision, tile OFF with
`KILL_RULE:<lane>:<rule>`, retire after the freeze.

### Spec deviations (documented, accepted for the live paper run)

1. A maker take-profit (and TP1) fills when the side-correct mark moves past
   the target; it books at the target. There is no aggressor trade-through
   print check.
2. Marketable exits book the side-correct tick that fired the rule, not the
   REALISTIC worse-of-1 s price.
3. The REV thesis cut is checked on the first tick at or after each 60 s close.
4. Taker latency is the live path (about 1-2 s after the bar close for CVD),
   not a fixed 2 s.
5. The committed fade follows H-A's live definition (no score-gap >= 30
   filter): Asia+EU sessions, spread <= 3 bp.
6. Both preregs bind judging to the `ce-20261004-v31-freeze21` epoch; the
   live run is `ce-20261004-v31-freeze21b`. Grok Strategist must confirm the
   move.
7. The ATR percentile needs 160 closed bars. The engine hydrates 26 h of the
   durable 1 s tape at boot. A missing input never classifies as VIOLENT
   (QUIET fallback; TREND needs ADX).
8. The QUIET touch order has a 660 s order TTL backstop; the fallback
   decision happens at 600 s.
9. GS-01 runs its own instance of H-C's premium rule (same thresholds, 3 bp
   spread gate, 5 s minimum submit gap, no shadow file).
10. The fill-rate kill and the random-control kill are scored offline.
11. The GS-02 violent limit uses ref = last price (as in `gslib`), not the
    prose "mid".
12. A 10 bp spread sanity guard was added on GS-02/03/04 and B1..B3 (the
    specs have no spread filter).
13. The live metric is net bp per closed fill. Strategist's per-signal score
    (misses = 0) is the cross-check.
14. If a position's signal-time decision is missing (for example after a
    restart), its exit stack falls back to the first profile and 4 bp ATR.
15. Resting GS/B limits are never cancelled by fill-time AI revalidation
    (`SKIP_FILL_REVALIDATION`): the offline spec has none.
16. The day-21 verdict clock counts from each prereg's `registered_utc`
    (H-*: 04:02Z, GS/B: 06:53Z), which is a few hours before the freeze21b
    epoch start. The freeze window itself is the epoch's 21 days.

### Post-deploy verification (freeze21b)

As above, with 11 lanes: the gate receipt lists all eleven ON, and
`research_freeze.epoch_id` is `ce-20261004-v31-freeze21b` on day 1/21.
The evaluator health snapshot is `GET /api/status` ->
`collection.xvl_evaluator` (there is no `/api/xvl_evaluator` route). Per lane it
reports `by_status`, `stale_by_reason` (cumulative since process start) and the
paper attempt counters; `lanes.FAMILY_GS03_CVD_DIV_TAKER.engine` is the
`regime_bars_3m_v1` engine (`hydrated`, `bars`, latest ATR percentile, ADX and
spread).

### Expected alerts during the freeze

- `proof.latest` FAILING on `no_manual_intervention`: the 19:37 AEDT
  (08:37Z) manual flatten inside the FREEZE21B boundary reset is a deliberate
  owner-approved intervention; the proof window clears once it rolls out of
  the window. Not a defect.
- Fresh-epoch warmup (analyzer studies/genome below their row minimums,
  `MEAN_WARMING_UP` on GS-01/H-C for the first hour) and an external market
  stream marked DEGRADED are expected and are not freeze defects.
