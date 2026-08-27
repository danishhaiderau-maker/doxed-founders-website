# Visual QA Receipt — revision 591bcc3

- Verified at: 2026-08-27 14:20–14:22 AEST
- Production: `https://doxed-btc-bot.fly.dev/#activityTables`
- Analyzer: `http://127.0.0.1:9001/`
- Full source revision: `591bcc338b22d9ea9d2f0c66fea3e87589beff6b`
- Signed epoch: `epoch-7a84c7ef272dd454a78cfe53`
- Tile registry: `a6a84cf156f65c2c6f73eda5a2421fc0716480536135e15f112304eb68af124b`

## Analyzer

Desktop and 390 × 844 narrow viewports were inspected first-hand in the in-app browser.

- Header showed analyzer revision `591bcc338b22`, Fly/mirror revision `591bcc338b22`, and parity `MATCH`.
- Current Lanes showed `INSUFFICIENT CURRENT-GENERATION EXECUTION EVIDENCE`; unavailable execution metrics rendered as em dashes instead of fabricated `$0.00` results.
- Primary routes inspected: Overview, Lanes & AI, Chase & Exits, Genome & Reports.
- Chase/exit routes inspected: Attribution, Threshold, Delay, Top 100 Policy Combos, Legacy Gap Performance, Exit Combos, Exit Reason Leak, Ladder Simulator, Historical Exit Leakage.
- Exit pages exposed paper, shadow/lab, conservative BBO/depth, and ideal-touch evidence separately. Empty current cohorts stated `SOURCE_EMPTY_OR_UNAVAILABLE` or `NO TERMINAL EVIDENCE`.
- Narrow viewport had no document-level horizontal overflow. Wide tables remained inside their own scrollable containers.
- Screenshot evidence was captured in the browser QA run for the complete narrow Exit Combinations page.

## Production

Desktop and 390 × 844 narrow viewports were inspected first-hand in the in-app browser.

- Runtime revision displayed `591bcc338b22` and collector `collector_v3.1`.
- Overview, Decisions, and Data navigation were present.
- All five paper-only family tiles and the separate Continuous benchmark were present.
- The active global submit buckets displayed `3, 4, 5+`; tile reprice templates were explicitly described as post-submit schedules rather than permission to bypass the global selector.
- Active Signals, Virtual Chase Candidates, Pending Orders, Positions, Expired Orders, Trades, and AI History were present.
- Paper-only and relay-blocked boundaries were visible on every family tile.
- Narrow viewport had no document-level horizontal overflow. Tile controls, labels, and table scrollbars remained usable.
- Screenshot evidence was captured in the browser QA run at the ATR Trail / Hybrid Runner transition.

## Download integrity

- A fresh `/download/everything` archive was generated after deployment.
- Archive size: 123,002,100 bytes; 856 ZIP entries.
- Manifest schema: `doxxed_everything_bundle_v2`.
- Manifest indexed 854 files; every indexed member existed and all SHA-256 and size checks passed.
- `missing_required_raw_members` was empty.
- Component coverage was true for relay lifecycle, counterfactual evidence, cohort reports, genome/DNA, report manifest, canonical V3 ledgers, canonical market segments, replay rotations, session/spec/index receipts, paper lifecycle, mirror sync receipt, and the GPT source-audit bundle.

## Remaining readiness limits

- This receipt proves the inspected UI and download contract at revision `591bcc3`; it is not a strategy-qualification or live-trading approval.
- Current execution evidence remains immature/empty for several current-epoch views.
- Two complete post-deployment advancing runtime, mirror, and analyzer cycles plus a later scheduled analyzer cycle remain required before technical readiness can be called GREEN.
- Bitfinex remained disabled and the system remained PAPER_ONLY throughout QA.
