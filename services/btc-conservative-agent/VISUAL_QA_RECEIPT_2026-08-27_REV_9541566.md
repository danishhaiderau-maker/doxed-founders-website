# Visual QA Receipt — revision 95415660cbe9

- QA time: 2026-08-27 18:32–18:37 AEST
- Production: https://doxed-btc-bot.fly.dev/
- Analyzer: http://127.0.0.1:9001/
- Deployed/analyzer revision: `95415660cbe958929cbe5c2ada3bfbfe641f2fc0`
- Epoch: `epoch-7a84c7ef272dd454a78cfe53`
- Analyzer generation: 2026-08-27T08:29:50+00:00
- Viewports: desktop 1280×720; narrow/mobile 390×844 (true CDP mobile emulation used for production)

## Production dashboard

PASS — first-hand browser inspection confirmed:

- Overview, Decisions and Data anchors navigate to their expected sections.
- All five paper-family tiles and the separate Continuous benchmark render.
- Every tile visibly states paper-only eligibility and the live-copy boundary.
- Global execution selector visibly restricts initial paper submission to buckets 3, 4 and 5+.
- WebSocket/runtime/AI status, Active Signals, Virtual Chase Candidates, Pending Orders, Positions, Expired Orders, Trades and AI History sections render.
- Revision `95415660cbe9`, epoch, PAPER_ONLY messaging and Bitfinex relay boundary are visible.
- Desktop has no page-level horizontal overflow.
- Narrow/mobile navigation remains usable and has no page-level horizontal overflow; body and primary containers fit the 390 px viewport.

## Analyzer dashboard

PASS — every primary group and all 21 sub-routes were activated and inspected:

- Overview: Overview, Findings, Regime & ADX.
- Lanes & AI: Current Lanes, Direction & Gap.
- Chase & Exits: Attribution, Threshold, Delay, Top 100 Policy Combos, Legacy Gap Performance, Exit Combos, Exit Reason Leak, Ladder Simulator, Historical Exit Leakage.
- Genome & Reports: Safe Policy Genome V3.1, Edge & Features, Report Explorer, Archives, Downloads, Pathway Audit, Historical Recovery.

Observed results:

- Header revision and epoch match the current atomic manifest.
- Current Lane Analysis displays all five canonical families and explicitly separates executed from counterfactual evidence.
- Chase buckets 0, 1, 2, 3, 4 and 5+ all render. Empty buckets state `NO TERMINAL EVIDENCE` rather than fabricated statistics.
- Top Policies separates conservative execution rows from ideal-touch diagnostic hypotheses and marks ideal-touch rows as not execution verified or qualification eligible.
- Exit sections expose the expanded combination families and explain when current terminal exits are unavailable.
- Genome page renders current DNA/replay content; Pathway Audit displays PASS receipts for tile independence, coordinator-only AI scan role and runtime integrity.
- Archives shows the new `session_20260827_082950_5e823e1b6be3` archive.
- Downloads exposes one authoritative complete evidence bundle.
- Desktop and narrow/mobile layouts have no page-level horizontal overflow; navigation wraps and remains usable.

## Defects and limitations

- No visual defect was reproduced on this revision.
- Empty execution, leakage and recovery tables are truthful insufficiency states because the current cohort has no eligible terminal fills/exits.
- Strategy qualification remains unavailable; this receipt verifies presentation and provenance, not profitability or live readiness.
- Storage remains a separate readiness gate.

## Retest status

PASS for the revision-bound desktop/mobile visual matrix described above. Bitfinex remained disarmed and no mutating dashboard control was used.
