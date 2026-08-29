# BTC V3.1 visual QA receipt — 2026-08-25

Status: **PARTIAL / NOT GLOBAL-READY**

## Bound evidence

- Production URL: `https://doxed-btc-bot.fly.dev/`
- Production revision: `922ad65a27b4`
- Execution/UI build: `v31-three-tile-protected-w234`
- Collector: `collector_v3.1`
- Epoch: `epoch-f9331f49c318f89040ce6993`
- Analyzer URL: `http://127.0.0.1:9001/`
- Analyzer report generation: `2026-08-25T22:40:35.55428+10:00`
- Browser viewports actually verified: desktop and narrow/mobile `390x844`

## Production dashboard — rendered inspection

- PASS: production page rendered authenticated current runtime data.
- PASS: revision, collector, epoch, paper-only state, and tile toggle states were visible.
- PASS: Patient Chase was visibly ON and paper-only.
- PASS: Continuous was visibly OFF and labelled `LAB SHADOW — no new orders`.
- PASS: Protected W234 was visibly ON and explicitly live-copy blocked pending partial-close relay proof.
- PASS: all three active registry tiles were distinct and had independent visible status/PnL sections.
- PASS: Overview, Decisions, and Data anchors were present.
- PASS: Refresh and download controls were present.
- NOT EXERCISED: destructive wipe, sign-out, strategy toggle, leverage, and capacity controls. Exercising them would mutate production state and is not required for a read-only visual smoke pass.
- PASS: narrow/mobile production rendering at `390x844`; current runtime values and all three active tiles loaded after refresh.
- PASS: measured document width remained within the mobile viewport with no page-level horizontal overflow.

## Analyzer dashboard — rendered interaction

Primary sections exercised successfully:

- Overview
- Lanes & AI
- Chase & Exits
- Genome & Reports

Chase/exit subsections exercised successfully:

- Attribution
- Threshold
- Delay
- Top 100 Policy Combos
- Legacy Gap Performance
- Exit Combos
- Exit Reason Leak
- Ladder Simulator
- Historical Exit Leakage

Genome/report subsections exercised successfully:

- Safe Policy Genome V3.1
- Edge & Features
- Report Explorer
- Archives
- Downloads
- Pathway Audit
- Historical Recovery

Dedicated routes opened and rendered without server errors:

- `/safe-policy-genome-v3.1`
- `/static-policies`
- `/dynamic-policies`
- `/shadow-research`

Truthfulness observations:

- PASS: current page states `MIXED — CURRENT V3.1 POLICY GRID + LEGACY EXECUTED` where cohorts are mixed.
- PASS: current signed V3.1 and legacy executed evidence are visibly separated.
- PASS: Safe Policy Genome shows `NO_SAFE_QUALIFIED_POLICY`.
- PASS: Top policy screen shows 100 descriptive rows from 21,070 evaluated policy specifications; no policy is qualified.
- PASS: shadow page shows 117 current-epoch rejected decisions and zero closed paused-shadow outcomes; it explicitly waits for sufficient comparable evidence.
- PASS: signed policy rows render explicit offset, fill world, TP, and stop parameters rather than unknown placeholders.
- PASS: narrow/mobile analyzer rendering at `390x844`.
- PASS: measured analyzer body/document width was `375px` within the `390px` viewport; no page-level horizontal overflow.
- PASS: mobile navigation wrapped into operable rows without clipping the current policy evidence cards.
- PASS: browser console contained no warning or error entries during the mobile analyzer pass.
- PASS: the next scheduled analyzer cycle completed atomically at `22:40:35 AEST`; API, manifest, and dashboard sync identity remained `v31-three-tile-protected-w234`.
- PASS: that completed snapshot ingested 79 independent opportunities and 222 terminal lifecycles; the live mirror advanced once more to 80/225 immediately afterward and is truthfully marked `PENDING_NEXT_ANALYZER_CYCLE` rather than silently merged.
- PASS: complete evidence bundle downloaded from `/download/everything` into a disposable non-OneDrive QA directory.
- PASS: bundle size `37,237,901` bytes; `483` files including preserved sessions and `64` current-report files.
- PASS: root bundle manifest schema `doxxed_everything_bundle_v2` and epoch `epoch-f9331f49c318f89040ce6993`.
- PASS: current report manifest schema `report_manifest_v1`, 56 reports, analyzer sync `v31-three-tile-protected-w234`.
- PASS: all current-report JSON, JSONL, and CSV artifacts parsed; zero parse failures and zero OneDrive path hits.
- BLOCKER: report manifest declares policy comparability `MISSING`; the bundle is parseable but does not prove policy qualification.

## Global readiness conclusion

This receipt does **not** authorize a FIXED, READY, GREEN, or live-copy claim. Desktop and narrow/mobile visual checks pass, but backend strategy qualification is not met: current evidence remains descriptive, conservative terminal OOS evidence is immature, and no safe policy has qualified. Pre-repair counterfactual rows without durable policy-comparability identity remain excluded while the collector waits for a naturally matured post-repair lifecycle.
