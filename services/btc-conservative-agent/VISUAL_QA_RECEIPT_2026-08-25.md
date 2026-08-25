# BTC V3.1 visual QA receipt — 2026-08-25

Status: **PARTIAL / NOT GLOBAL-READY**

## Bound evidence

- Production URL: `https://doxed-btc-bot.fly.dev/`
- Production revision: `534f1d2a8443`
- Execution/UI build: `v31-three-tile-protected-w234`
- Collector: `collector_v3.1`
- Epoch: `epoch-f9331f49c318f89040ce6993`
- Analyzer URL: `http://127.0.0.1:9001/`
- Analyzer report generation: `2026-08-25T10:30:19`
- Browser viewport actually verified: `1280x720`, DPR 1

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
- BLOCKED: narrow/mobile rendered verification. The connected browser remained `1280x720` and did not expose viewport resizing.

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
- PASS: Top policy screen shows 70 descriptive rows, zero terminal OOS fill policies, and zero profitable terminal OOS policies.
- PASS: shadow page shows 52 rejected paths, zero signed terminal shadow episodes, and explicitly waits for sufficient evidence.
- PASS: signed policy rows render explicit offset, fill world, TP, and stop parameters rather than unknown placeholders.
- BLOCKED: narrow/mobile rendered verification for the same viewport limitation.
- PASS: complete evidence bundle downloaded from `/download/everything` into a disposable non-OneDrive QA directory.
- PASS: bundle size `37,237,901` bytes; `483` files including preserved sessions and `64` current-report files.
- PASS: root bundle manifest schema `doxxed_everything_bundle_v2` and epoch `epoch-f9331f49c318f89040ce6993`.
- PASS: current report manifest schema `report_manifest_v1`, 56 reports, analyzer sync `v31-three-tile-protected-w234`.
- PASS: all current-report JSON, JSONL, and CSV artifacts parsed; zero parse failures and zero OneDrive path hits.
- BLOCKER: report manifest declares policy comparability `MISSING`; the bundle is parseable but does not prove policy qualification.

## Global readiness conclusion

This receipt does **not** authorize a FIXED, READY, GREEN, or live-copy claim. The remaining visual gate is narrow/mobile layout. Backend strategy qualification is also not met: there are no conservative terminal OOS fills, only one observed market regime, and policy comparability is missing.
