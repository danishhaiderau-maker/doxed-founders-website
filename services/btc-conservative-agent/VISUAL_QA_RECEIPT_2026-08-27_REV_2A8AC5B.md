# Visual QA Receipt — BTC V3.1

- QA time: 2026-08-27 17:16–17:24 AEST
- Production revision: `2a8ac5b4ed4bc0c49bb370ba4ee0846d528c9af8`
- Collection epoch: `epoch-7a84c7ef272dd454a78cfe53`
- Tile registry signature: `a6a84cf156f65c2c6f73eda5a2421fc0716480536135e15f112304eb68af124b`
- Production: `https://doxed-btc-bot.fly.dev/`
- Analyzer: `http://127.0.0.1:9001/`
- Browser: Codex in-app Chromium
- Viewports: desktop 1280×approximately 710; mobile emulation 390×844

## Production dashboard

PASS on desktop and mobile:

- Overview, Decisions, and Data navigation reached their intended sections.
- Five independent paper-family tiles and the separate Continuous benchmark rendered.
- Tile states, counters, policy identifiers, global submit buckets 3/4/5+, hard-stop labels, and paper-only/live-blocked boundaries rendered.
- Runtime revision, clean epoch, WebSocket/runtime context, AI status, storage, Active Signals, Virtual Chase Candidates, Positions, Pending Orders, Expired Orders, Trades, and AI History rendered.
- Mobile document width was 390 px with a 390 px scroll width: no page-level horizontal overflow.
- Mobile table containers remained bounded/scrollable rather than expanding the document.
- Screenshot evidence was captured in the browser QA run at both viewport classes.

Observed current state: all paper and exchange positions/orders were zero; Bitfinex was disabled and disarmed. Empty current-period trading tables were consistent with the clean epoch and were not presented as profitable evidence.

## Research dashboard

PASS on desktop and mobile for the following semantic routes:

- Overview, Findings, Regime & ADX.
- Current Lanes and Direction & Gap.
- Attribution, Threshold, Delay, Top 100 Policy Combos, Legacy Gap Performance, Exit Combos, Exit Reason Leak, Ladder Simulator, and Historical Exit Leakage.
- Safe Policy Genome V3.1, Edge & Features, Report Explorer, Archives, Downloads, Pathway Audit, and Historical Recovery.

Each route switched to its expected visible heading. The dashboard displayed revision `2a8ac5b4ed4b`, epoch `7a84c7ef`, and analyzer/mirror parity `MATCH`. Current paper, shadow/lab, conservative BBO/depth, ideal-touch diagnostic, and legacy evidence were visibly labelled and separated. Empty terminal-exit sections stated their exact evidence insufficiency instead of displaying invented profit, win rate, or drawdown.

Desktop document width remained within the viewport. Mobile document width was 390 px with a 390 px scroll width. The Top 100 Policy Combos route and its diagnostic-only section remained reachable at mobile width. Screenshot evidence was captured in the browser QA run at both viewport classes.

## Download retest

PASS after the current-receipt repair:

- `Download Everything` returned a parseable ZIP with 935 entries.
- `MANIFEST.json` indexed all 933 payload files; only `MANIFEST.json` and `README.txt` were intentionally outside that list.
- No indexed files were missing, no entries were unindexed, and every recorded byte count and SHA-256 checksum matched.
- All seven required current pathway receipts, the raw Fly mirror, current reports, session/history evidence, accumulator, research SQLite database, V3 ledgers, market segments, replay rotations, and GPT audit bundle were present.
- SQLite `PRAGMA integrity_check` returned `ok`.

## Remaining readiness limitations

- Visual QA passed for the inspected revision; this does not qualify any strategy.
- The current epoch still has insufficient conservative terminal OOS evidence for a profitable-policy claim.
- Storage is AMBER at approximately 134 GiB free, below the 150 GiB GREEN threshold.
- A later scheduled analyzer generation must still complete and be checked before technical readiness can be GREEN.
- Bitfinex remains disarmed.
