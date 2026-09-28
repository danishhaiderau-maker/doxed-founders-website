# BTC V3.1 Visual QA Receipt — 2026-08-27

## Bound candidate

- Production: `https://doxed-btc-bot.fly.dev/`
- Analyzer: `http://127.0.0.1:9001/`
- Exact revision: `67600bf9fef26cd851c6cddb2278dde9bce0cd57`
- Runtime label: `v31-five-family-atomic-paper`
- Clean epoch: `epoch-ca59fec3c223953a05bc0da4`
- Tile registry signature: `a6a84cf156f65c2c6f73eda5a2421fc0716480536135e15f112304eb68af124b`
- Analyzer generation: `2026-08-27T04:46:43.911961+10:00`
- Evidence boundary: PAPER_ONLY, Bitfinex disabled and disarmed.

## Viewports

| Surface | Viewport | Screenshot |
| --- | --- | --- |
| Production dashboard | 1440 x 1000 | `C:\Users\danis\AppData\Local\DoxxedCrypto\visual-qa\production-desktop-67600bf.png` |
| Production dashboard | 390 x 844 | `C:\Users\danis\AppData\Local\DoxxedCrypto\visual-qa\production-mobile-67600bf.png` |
| Analyzer dashboard | 1440 x 1000 | `C:\Users\danis\AppData\Local\DoxxedCrypto\visual-qa\analyzer-desktop-67600bf.png` |
| Analyzer dashboard | 390 x 844 | `C:\Users\danis\AppData\Local\DoxxedCrypto\visual-qa\analyzer-mobile-67600bf.png` |

## Production dashboard checks

- Loaded live API-backed page and matched revision, epoch, PAPER_ONLY state, pause/resume state and Bitfinex boundary.
- Verified all five registered family tiles and the separate Continuous benchmark are visible.
- Verified execution controls and exact Chase selector: 0, 1 and 2 OFF; 3, 4 and 5+ ON.
- Inspected Active Signals, Virtual Chase Candidates, Pending Orders, Positions, Expired Orders, Trades and AI History.
- Inspected WebSocket/AI status, data storage, revision and epoch labels.
- Desktop and narrow layouts had no page-level horizontal overflow, clipped buttons, `NaN`, `undefined`, or rendered server errors.
- Wide tables use local horizontal scroll containers at narrow width.

## Analyzer dashboard checks

- Loaded current clean-epoch output and matched analyzer source revision, mirror revision, epoch and registry signature.
- Inspected Overview, Lanes & AI, Chase & Exits, Genome & Reports and their report routes.
- Inspected attribution, threshold, delay, Top Policies, legacy-gap, exit-combination, exit-reason, ladder, historical-leakage and cross-world views.
- Verified diagnostic, shadow/counterfactual, paper and cross-world evidence remain visibly separated.
- Verified empty current-epoch exit sections provide a truthful insufficiency reason instead of asking the operator to rerun an already-current analyzer.
- Verified cross-world current counts exclude legacy and missing-epoch evidence and display the excluded count separately.
- Desktop and narrow layouts had no page-level horizontal overflow, clipped buttons, `NaN`, `undefined`, or rendered server errors.

## Defects found and retested

1. Cross-world evidence previously presented legacy or missing-epoch rows as current observations.
   - Repair: exact epoch filtering and explicit excluded-row accounting.
   - Retest: PASS; current observed Bitfinex-copy count is zero and excluded historical/missing-epoch evidence is separately labelled.
2. Empty exit reports previously displayed stale instructions such as `Run analyzer` even when the analyzer was current.
   - Repair: exact clean-epoch insufficiency explanations.
   - Retest: PASS; no stale instruction remains on Exit Combos or Exit Reason Leakage.

## Machine-verifiable parity

- `/health` and `/ready`: exact revision and ready runtime.
- Analyzer `/api/status`: source/mirror parity `MATCH`.
- Analyzer `/api/integrity`: `VALID`, zero failed checks.
- Mirror heartbeat: exact revision, current tile signature, successful atomic sync.

## Remaining readiness limitations

- Disk free space was approximately 113 GB: AMBER under the goal threshold of 150 GB.
- The clean epoch does not yet have adequate conservative terminal OOS evidence. Empty rankings and exit tables are therefore truthful, and strategy qualification remains DESCRIPTIVE ONLY.
- This receipt verifies rendered UI and evidence labelling for the bound candidate. It does not qualify a policy or authorize Bitfinex.

## Post-deployment retest — exit-evidence repair

- Retest time: `2026-08-27 05:46 AEST`.
- Exact deployed revision: `b358f4ba21f5c3e4fffd41bcc92a05012e093351`.
- Clean epoch: `epoch-ca59fec3c223953a05bc0da4`.
- Production rendered `collector_v3.1`, the exact revision, PAPER_ONLY family labels, five family tiles plus Continuous, and a disarmed live boundary.
- Production rendered Chase 0, 1 and 2 unchecked and Chase 3, 4 and 5+ checked. Every tile now explains that this global selector controls first paper-order submission, while its own Chase label is a post-submit repricing template.
- Production AI History advanced through multiple post-deployment cycles and rendered `NO_TRADE` with independent per-family `AI_REJECT` outcomes and no paper orders, positions or closes.
- Analyzer rendered revision `b358f4ba21f5`, epoch `ca59fec3`, and the new four-world exit contract: executed paper, shadow/lab, conservative BBO/depth replay and ideal-touch diagnostic.
- Exit Combinations rendered `n/a` for unavailable leakage and EV metrics and an explicit clean-epoch terminal-evidence insufficiency reason.
- Exit Reason Leak rendered `n/a`, zero exit reasons, and an explicit clean-epoch terminal-evidence insufficiency reason; it did not fabricate zero PnL, zero drawdown or a recommendation.
- Narrow analyzer viewport `390 x 844` had no page-level horizontal overflow; navigation wrapped into usable rows and wide report tables remained locally scrollable.
- Result: PASS for the deployed analyzer exit-evidence repair. Strategy qualification remains blocked by immature terminal execution evidence and storage remains AMBER.
