# Visual QA Receipt — 2026-08-27

Scope: production bot dashboard and local read-only research dashboard after revision `262816dfb8c159d244861fb9bc6e336837293d0d`.

Retest time: 2026-08-27 03:34-03:36 AEST. Browser screenshots were captured for both dashboards at desktop size and at a 390 x 844 emulated mobile viewport.

## Production dashboard

- Verified exact deployed revision, collector V3.1, paper-only mode, Bitfinex disarmed, and five family tiles plus Continuous.
- Verified every family tile states that global buckets 3, 4, and 5+ control first paper submission; tile-local chase settings are repricing templates after submission.
- Verified current runtime sections render live API data for market/AI state, active signals, orders, positions, trades, and storage.
- Verified desktop and 390 x 844 narrow layouts. The page has no whole-document horizontal overflow; wide tables remain locally scrollable.
- Verified the live page reports `collector_v3.1`, runtime revision `262816dfb8c1`, epoch `epoch-f2ea95e53b3ff599a9419514`, and current settings-period Chase `3, 4, 5+`.

## Research dashboard

- Verified the four primary tabs and every Chase & Exits sub-tab load and render.
- Verified Safe Policy Genome, cross-world evidence, static policies, dynamic policies, shadow research, risk/drawdown, chronological OOS, evidence maturity, and partial-reduction routes.
- Verified the Top Policy screen keeps conservative execution evidence separate from ideal-touch diagnostics and gives an explicit insufficiency reason when no supported policy qualifies.
- Verified exit combinations and exit leakage are populated from executed paper and shadow/lab cohorts without adding their PnL together.
- Verified integrity fails closed on unresolved order/lifecycle identity rather than presenting rankings as qualified.
- Verified desktop and 390 x 844 narrow layouts; navigation wraps and pages do not create whole-document horizontal overflow.
- Verified the regenerated analyzer banner reports revision `262816dfb8c1`, epoch `f2ea95e5`, 105 independent opportunities, 633 decision branches, 465 terminal lifecycles, 185 market segments, and 21,070 evaluated policies.
- Verified Chase Analytics renders explicit zero-evidence rows for buckets 2, 4, and 5+ rather than silently omitting them.
- Verified Exit Combinations renders 34 executed-paper combinations, an executed-paper family scorecard, a separate shadow/lab family scorecard, and separate executed/shadow stop-effectiveness matrices.

## Remaining blockers observed

- Analyzer integrity remains invalid because current evidence contains unresolved orphan/order-lifecycle contamination.
- Cross-world causal comparisons remain not computable until all worlds carry the complete epoch, opportunity, policy, schedule, tape, and fill identity chain.
- The first analyzer process crashed inside Python 3.11 during protection replay; the single stability supervisor restarted it and the replacement completed 115/115 replay events and atomically published the current revision. A later scheduled analyzer cycle is still required for final stability proof.
- Relay-evidence forwarding recovered and the canonical mirror heartbeat now matches revision `262816dfb8c1`.
- No presentation unit-mixing or unavailable-to-zero defect reproduced after the `262816d` repair.

This receipt is evidence of the routes and states inspected. It is not a strategy qualification or authorization for live trading.
