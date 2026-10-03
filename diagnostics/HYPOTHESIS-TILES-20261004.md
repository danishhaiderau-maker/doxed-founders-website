# Hypothesis tiles — final rules, re-checked numbers, deploy needs (2026-10-04)

Status: **draft POST-FREEZE PR
[#403](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/403),
not merged, not deployed.** Every tile is
paper-only, relay-ineligible and default OFF; Bitfinex stays DISARMED. The
registry (`combo_pathway_config.py`) is the only roster source. Stack version
`v31-danish-tiles-late-protection-v11`.

All numbers use the REALISTIC_V1 fill model with zero fees (verified) and a
1-second exit latency, replayed on the 1 s tape from 2026-09-30 to 2026-10-03
(4 UTC days). Units are basis points per filled trade. Brackets are 95%
confidence intervals clustered by hour. At `$0.25 margin @100x ≈ $25
notional`, 1 bp ≈ $0.0025 per trade. Margin is not a maximum loss: at 100x a
loss can exceed the posted margin through liquidation, slippage or a missed
stop.

## 1. Final roster

The tile number is the display order and is derived from the registry.

| # | Lane | Status | Live exits (first trigger wins) |
|---|------|--------|---------------------------------|
| 1 | `FAMILY_DANISH_CF` | new | hard stop -40, BE +20→+5, early cut, 90 min |
| 2 | `FAMILY_DANISH_CF_NOES` | new | hard stop -40, BE +20→+5, 90 min |
| 3 | `FAMILY_DANISH_CF_ALL_SESSIONS` | new (control) | hard stop -40, BE +20→+5, early cut, 90 min |
| 4 | `FAMILY_CONTINUOUS_AUG_ORIGINAL` | baseline, unchanged | Scenario-C ladder, thesis cut, early fail, stop -30% margin, 2 h |
| 5 | `FAMILY_COMMITTED_FADE_MAKER_90` | frozen in the deploying stack (#358) | hard stop -40, 90 min |
| 6 | `FAMILY_COMMITTED_FADE_TAKER_90` (H11) | rules changed | hard stop -40, BE +20→+5, ATR trail 1.5 @ +2 ATR, early cut, 90 min |
| 7 | `FAMILY_NOTRADE_FOLLOW_MAKER_60` (H9) | rules changed | hard stop -40, BE +20→+5, ATR trail 1.5 @ +2 ATR, 60 min |
| 8 | `FAMILY_XVENUE_SESSION_FOLLOW_60M` (H10) | rules changed | hard stop -40, BE +20→+5, 60 min |

The early cut is "-12 bp within 5 min, only if the trade never ran past +2 bp".
Every other protection in the canonical set is recorded per trade as a
shadow-only exit and never executed.

Retired: `FAMILY_XVENUE_LEAD_60S` and `FAMILY_XVENUE_PREMIUM_60S` (see §6).

## 2. Entry rules

- **Tiles 1–3, Danish confirmed fade.** Fade the shared AI's committed
  LONG/SHORT call. Rest a limit 0.10% better than the signal price. If price
  first moves 3 bp our way, cancel and take the market within a 5 bp cap
  (skip if the cap is exceeded). Drop the signal if neither happens within
  30 min. Stand aside if the spread is > 3 bp or the quote feed is > 5 s old.
  Tiles 1–2 trade Asia 00–08 and EU 08–16 UTC only; tile 3 trades all sessions.
  Max 3 concurrent.
- **Tile 6, H11.** Same fade signal, taker at the signal within a 5 bp cap,
  3 s fill window, Asia+EU only, spread ≤ 3 bp. Max 3 concurrent.
- **Tile 7, H9.** Follow the score-led side when the shared AI returns
  NO_TRADE. Maker 0.15% beyond the decision price; chase 25% of the remaining
  gap every 3 min in minutes 10–25; expire after 1 h. Max 10 concurrent.
- **Tile 8, H10.** Binance/Bybit lead Bitfinex by ≥ 8 bp over 10 s, or their
  premium leaves its 60-minute mean (+1.75 / -1.88 bp). Taker within a 5 bp cap
  in the leaders' direction; all three venue feeds must be < 2 s old.
  Max 3 concurrent.
- Tiles 4–5 are unchanged.

## 3. Re-checked numbers

### Danish confirmed fade (Asia+EU, max 3 concurrent)

| Entry × exit | n | bp/fill | 1 h CI | Test days (n, bp, CI) |
|---|---|---|---|---|
| Confirm entry, Danish A (early stop) — **tile 1** | 99 | 11.30 | [0.53, 23.44] | 73, 10.76, [-1.11, 25.37] |
| Confirm entry, no early stop — **tile 2** | 93 | 11.36 | [-0.58, 24.63] | 69, 11.61, [-1.22, 27.88] |
| Confirm entry, time 90 min + hard 40 (BASE) | 80 | 14.46 | [1.52, 29.37] | 57, 14.80, [-1.23, 33.11] |
| Maker no chase, Danish A | 57 | 16.84 | [0.36, 38.90] | 36, 10.94, [-5.90, 34.11] |
| Taker at signal, Danish A | 113 | 7.35 | [-1.80, 19.66] | 83, 9.46, [-2.84, 25.67] |

All sessions (**tile 3**): confirm entry with Danish A gives 139 fills at
5.60 bp [-3.53, 16.14]. Asia +8.6 bp (n 45), EU +13.8 bp (n 51), and **US
-7.2 bp (n 43)**.

Nested walk-forward (entry and exit chosen on the previous days, scored on the
next day): Asia+EU 65 fills at +11.62 bp [-4.11, 30.41]; all sessions 95 fills
at +2.41 bp [-10.26, 18.26].

Honest findings the owner should see:

- In this sample the **BASE time exit beat both Danish exits** (14.5 vs
  11.3 bp). Danish A and no-early-stop are shipped because they are the owner
  designs under test; BASE stays a shadow-only comparison on the cards.
- In Asia+EU the **early stop was net negative**: it cut 31 trades, 17 would
  have recovered, net -182.6 bp. Across all sessions it was net +55.5 bp
  because it saved US losers. Tiles 1 and 2 measure this question forward.

### Boss items (H11, H9, H10, CFM)

The rule was pre-set: adopt the composite (late BE +20→+5, ATR trail 1.5
armed at +2 ATR, time backstop, 40 bp stop) unless its test-day paired
difference vs the time exit was significantly negative; otherwise try late BE
alone; otherwise keep the time exit. The conditional cut goes live only if
its test-day paired mean on top of the adopted exit is ≥ 0.

| Tile | Base headline | Adopted exit | Conditional cut |
|---|---|---|---|
| H11 / 6 | nested walk-forward +6.8 [-6.0, 21.6]; Asia+EU composite +3.24 [-4.81, 11.22], test days +4.77 | composite | **live** (+0.56 bp on top of the composite) |
| H9 / 7 | nested walk-forward +4.5 [-3.8, 13.3]; needs 10 slots; one trend day carries most profit | composite | shadow (-0.87 bp, worse) |
| H10 / 8 | 1.3-day re-check +3.0 [-7.3, 13.5] | late BE only (the ATR trail was significantly worse per signal) | shadow (one nested fold only) |
| CFM / 5 | in-sample +9.5 [-13, 37]; walk-forward +5.0 [-15, 30]; deflated Sharpe ≈ 0 | **unchanged** (frozen in #358) | shadow (the rule would adopt composite + cut; owner follow-up) |

H11 session gate: the US session was negative in-sample and on test days
(-11.0 bp, n 39), and the nested gate chose Asia+EU in 2 of 2 folds.

Across the four tiles, the conditional cut cost more than it saved against a
plain time exit (net -489 to -4,307 bp per tile across the four cut variants). It only helps H11 when it is
layered on top of the composite.

## 4. Expected live EV and confidence

These are judgment ranges, not confidence intervals. Each starts from the
nested walk-forward (or test-day) estimate and shrinks it for 4-day samples,
design selection and the observed daily decay.

| # | Tile | Expected live bp/fill | P(mean > 0) |
|---|------|-----------------------|-------------|
| 1 | Danish A | +2 to +8 | ~65% |
| 2 | Danish, no early stop | +2 to +8 | ~65% |
| 3 | Danish, all sessions | -3 to +4 | ~50% |
| 4 | Continuous baseline | not estimated (benchmark) | — |
| 5 | CFM | -5 to +5 | ~45–50% |
| 6 | H11 | 0 to +5 | ~60% |
| 7 | H9 | -2 to +4 | ~55% |
| 8 | H10 | -3 to +3 | ~50% |

Every interval above crosses zero, so none of these tiles is proven. Kill
rules are on each card: for example, Danish tiles stop on mean ≤ 0 after 80
closes or a $1.00 drawdown.

## 5. Card sections (ENTRY / EXIT / RISK MANAGEMENT)

- The registry carries plain-English metadata for every tile.
  `tile_card_sections(lane)` renders the sections for the Fly dashboard
  (`bot.py`) and the analyzer (`/api/tile-cards`, Details → Current Lanes).
- Live exits are listed in first-trigger-wins order; shadow-only exits are
  listed separately.
- Size reads "$0.25 margin @100x ≈ $25 notional". No "max loss" wording.
- The registry validator fails if any tile lacks entry, exit or risk
  metadata.
- Visual QA: all 8 cards at 1366 and 1920 px on both dashboards, with no
  horizontal scroll and zero card overflow. Screenshots are in
  `hypothesis-tiles-20261004/visual-qa/`. The Fly screenshots use the card
  JavaScript extracted verbatim from `bot.py`. The analyzer screenshots come
  from the real Flask app with isolated scratch data, which is why its alarms
  read "no data".

## 6. XVL/XVP retirement proof

- Removed: both policy modules and their dedicated tests, plus their runtime,
  API, UI, analyzer and monitoring branches.
- Retained: the generic cross-venue evaluator, feeds, shadow collection and
  latency instrumentation, which tile 8 still uses.
- Lane tokens are listed in `RETIRED_TILE_LANES` and `RETIRED_POLICY_IDENTITIES`
  for one release.
- Remaining references to `XVENUE_LEAD` / `XVENUE_PREMIUM` (outside
  `diagnostics/`) are all non-executable:
  - the retired lists and the generated TypeScript mirror of them;
  - historical state-schema keys in `schema_registry.json`;
  - episode-class labels used for archive classification (`genome_grid_study`,
    `genome_mix_match`);
  - immutable hash-pinned pre-registrations;
  - historical ids in ledger, genome and insight tests;
  - one parity case that asserts the retired lane is not a benchmark;
  - tile 8's own raw policy id, which names the lead/premium trigger.
- Generic monitoring tests now use the live cross-venue lane as their fixture.

## 7. Forward tracker

The analyzer cycle now freezes the active roster into the forward-tracker
hash chain once per registry version (batch
`REGISTRY-v31-danish-tiles-late-protection-v11`). The freeze is idempotent
and append-only, and it refuses to write if the chain is broken. These rows
are scored by each tile's own pre-registration in
`tile_paired_comparison_report.json`, never by the genome grid. No live data
was written during this work.

## 8. What is needed to deploy

1. Land the deploying stack first: #358 → #367 → #373 → #395 → #365 → #364.
   This PR is stacked on those heads.
2. The DEPLOY-COORDINATOR merges this PR at a safe boundary: flat paper
   state, no open exchange exposure, relay DISARMED.
3. After deploy, verify:
   - the Fly `/api` roster shows 8 tiles in order with the v11 signature;
   - analyzer `/api/tile-cards` matches it;
   - mirror parity passes;
   - the first analyzer cycle reports `registry_freeze.status = FROZEN`;
   - two advancing collection/analyzer cycles.
4. Tiles stay OFF until the owner switches them on. A toggle never arms
   Bitfinex.
5. Owner follow-ups, not in this PR:
   - CFM adopting composite + cut (needs a new signed policy identity);
   - any relay eligibility (needs explicit approval plus the qualification
     gates).

## Artifacts

Everything in `diagnostics/hypothesis-tiles-20261004/` is aggregate study
output plus the study scripts. No raw tape or trade data is included.
