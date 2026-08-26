# BTC V3.1 visual-QA receipt — 2026-08-27

Status: **VISUAL PASS / STRATEGY NOT QUALIFIED**

## Bound evidence

- Production: `https://doxed-btc-bot.fly.dev/`
- Production revision: `dd3653de9a77`
- Runtime build: `v31-five-family-atomic-paper`
- Collector: `collector_v3.1`
- Epoch: `epoch-7a84c7ef272dd454a78cfe53`
- Analyzer: `http://127.0.0.1:9001/`
- Analyzer generation revision: `dd3653de9a778bf806f632529b862060afe0c77d`
- Analyzer generation: `2026-08-27 09:09:46 AEST`
- Desktop QA: current production and analyzer generation
- Narrow/mobile QA: `390x844`, DPR-managed browser viewport

## Production dashboard

- PASS: page rendered current runtime rather than placeholders.
- PASS: revision, collector, clean epoch, paper-only state and disarmed Bitfinex boundary were visible.
- PASS: five family tiles plus Continuous rendered with independent counters and policy details.
- PASS: all six tiles showed `Global submit windows 3, 4, 5+`.
- PASS: tile-local schedules were labelled `Reprice template` and explicitly could not bypass the global selector.
- PASS: global Chase 0, 1 and 2 were OFF; Chase 3, 4 and 5+ were ON.
- PASS: Overview, Decisions and Data navigation remained operable.
- PASS: Active Signals, Virtual Chase Candidates, Positions, Pending Orders, Expired Orders, Trades and AI History rendered truthful empty/current states.
- PASS: narrow/mobile viewport measured `390px` inner width, `375px` document/body width and zero overflowing visible buttons, links, inputs or selects.
- PASS: a first-hand mobile screenshot showed readable stacked cards and navigation without clipping.
- NOT EXERCISED: destructive wipe, sign-out, tile toggles and trading-setting mutations.

## Analyzer dashboard

- PASS: production/analyzer revision and epoch parity.
- PASS: corrected banner states that empty sections may mean no eligible current-cohort evidence.
- PASS: Overview, Lanes & AI, Chase & Exits, and Genome & Reports opened.
- PASS: Attribution, Threshold, Delay, Top 100, Legacy Gap, Exit Combos, Exit Reason Leak, Ladder and Historical Exit Leakage opened with populated or truthful insufficiency states.
- PASS: paper, shadow/lab, conservative BBO/depth and ideal-touch evidence remained visibly separated.
- PASS: narrow/mobile analyzer layout remained within the viewport with operable wrapped navigation.

## Current evidence limitation

The clean epoch had 13 independent opportunities at the latest monitor pass. All current family decisions were rejected/no-trade, with zero order intents, executions, paper positions or pending orders. Therefore no accepted signal has yet empirically exercised first paper submission in Chase 3, 4 or 5+, and no policy is strategy-qualified.

## Conclusion

Current desktop and narrow/mobile visual checks pass. This receipt does not authorize live trading: Bitfinex remains disarmed and strategy qualification remains unmet.
