# Analyzer and dashboard visual-QA receipt — 2026-08-26

Scope: first-hand rendered desktop and narrow-screen checks after the Report
Explorer on-demand loading repair.

## Identity

- Production revision: `26db6ab2f786881d64ec1d977f2edcae7fa6787e`
- Epoch: `epoch-5c7b70f8d7e64985a67b6403`
- Collector: `collector_v3.1`
- Analyzer sync: `v31-four-tile-protected-patient-chase`
- Tile registry signature:
  `43b1cff08a1c6ea75f1c84ad8a99772afb880ce2413f3c7d5d1306b76c377b2f`

## Analyzer navigation

The following rendered controls were activated successfully at desktop width:

- Overview, Findings, Regime & ADX
- Current Lanes, Direction & Gap
- Attribution, Threshold, Delay, Top 100 Policy Combos
- Legacy Gap Performance, Exit Combos, Exit Reason Leak
- Ladder Simulator, Historical Exit Leakage
- Safe Policy Genome V3.1, Edge & Features, Report Explorer
- Archives, Downloads, Pathway Audit, Historical Recovery

The Report Explorer originally blocked the browser for more than 30 seconds
because opening the tab automatically fetched and pretty-printed its first
large report. The repaired tab opens without loading a report automatically:

- canonical page: `http://127.0.0.1:9001/`
- tab activation: 989 ms after the canonical dashboard restart
- report list: 56 items
- initial detail panel: `Select a report…`
- selected `AI Funnel`: 8,606 rendered characters, 1,019 ms
- horizontal document overflow at 1280 px: none

## Dedicated routes

The following routes rendered without HTTP/JavaScript error or document-width
overflow:

- `/safe-policy-genome-v3.1`
- `/static-policies`
- `/dynamic-policies`
- `/shadow-research`
- `/risk-drawdown`
- `/chronological-oos`
- `/evidence-maturity`
- `/partial-reduction`

All eight routes also passed a 390 x 844 viewport check with no document-width
overflow or off-screen primary controls.

## Production dashboard

The rendered Fly dashboard agreed with `/health` and `/ready` on:

- revision, collector, epoch and four-tile registry;
- paper-only boundary and relay disarmed state;
- four visible, distinct research tiles;
- zero active signals, zero paper positions and zero pending orders;
- two historical Patient Chase closes;
- current AI history;
- live WebSocket market source.

Overview, Decisions and Data navigation operated successfully. A 390 x 844
viewport check found no document-width overflow or off-screen controls.

This receipt does not qualify a strategy or authorize Bitfinex relay arming.

