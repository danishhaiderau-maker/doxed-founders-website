# Cursor independent audit handoff — 2026-09-09

## Request from Danish

Independently assess whether the current work is moving BTC V3.1 toward reliable collection, useful strategy analysis and eventual Bitfinex limit-order readiness. Challenge Codex claims with source, executable tests and fresh runtime receipts. Do not merely confirm this brief. All snapshots can become stale.

## Ownership and safety

Cursor and its agents: READ-ONLY AUDIT. Split independent audit work among agents if available. Do not edit, commit, deploy, restart, kill processes, start downloads, purge data, change strategy/AI/toggles or arm Bitfinex. No credential output. Ask Codex before overlapping implementation. Lightweight authenticated observations are acceptable with existing local access; no recursive Fly scans or repeated heavy export requests.

Codex owns integration and operational actions. Active Codex agents:
- fill_durable_evidence: implementing LAB outcome/accounting truth in isolated btc-v31-shared-admission worktree.
- review_recovery: diagnosing installed analyzer download requirements versus current mirror schemas; read-only currently. Also owns an isolated synthetic LAB-history preview.
- Root: integration, transfer readiness, test review and dashboard QA.

## Source of truth

- Integration: C:\DoxxedCrypto\btc-v31-current\.qa-dashboard-bounded-45767d1
- Pinned integration HEAD at handoff: ec0691a64ff10546661c99f94862e00bd0dcda54. Verify before audit, pin your conclusions and distinguish later dirty work.
- Main checkout C:\DoxxedCrypto\btc-v31-current contains user-owned dirty work. Do not treat it as deployed or overwrite it.
- Do not use the OneDrive Final-Bot checkout.
- Full objective: C:\Users\danis\.codex\attachments\153df37e-a37b-4692-87ee-6507fb560b80\goal-objective.md
- Historical checkpoint archive beside it: goal-history-20260909.md. History is not a fresh receipt.
- Canonical production: https://doxed-btc-bot.fly.dev
- Latest observed production SHA: bdfcd737205725b0a3e1daca0d8995f293046429
- Latest observed epoch: epoch-5a856f6f873c84fee7cefb2a
- Local canonical mirror: C:\DoxxedCrypto\btc-v31-current\services\btc-conservative-agent\canonical-research-data

Ultimate acceptance has TWO independent parts: technical Bitfinex order-management readiness AND data-supported strategy qualification, including frozen 15-day forward paper comparison (not automatic qualification after 15 days). Live remains disabled until both pass AND Danish explicitly arms. No profitability guarantee. Reset already completed; do not repeat it.

## Actual changes, not claims of deployed completion

Inspect the full git range from deployed SHA to pinned HEAD; this list highlights recent work, not a substitute for reviewing the full range.

| Local commits | Purpose / reported verification |
|---|---|
| c85e763/e87d12d | Capture bounded report inputs under research gate, compute outside gate, publish with reset/identity fences. 42 reset/report tests passed, 2 skipped. Full generated-engine parity passed. No deployed latency claim. |
| 2800b42/00ac074 | LAB accumulator precision repair. Does not reconcile historical totals or fix legacy cost semantics. |
| 689f5e9/0d9083d/a06917c | Counterfactual source replay, promoted inventory membership, causal availability and integration tests. No paper identity impersonation. |
| e285c25 | Adapter-to-counterfactual-proof producer/evaluator wiring with shared lease handling. |
| bf502c5/0905352 | Resumable proof production with trusted-local receipts; fixed invalid first proofs starving later records. Combined producer/replay/membership/evaluation tests: 43 passed. Original verification availability retained; current source revalidated. |
| 4aa481f/c5bb456 | Correct native Stoch RSI fields into compact AI input, preserving zero. Closed-bar timestamp stays unknown rather than fabricated. 12 tests and engine parity passed. No prompt/gate relaxation. |
| 61c1f13/6b1697f | Bounded authenticated LAB history route and real dashboard controls, isolated from paper trades. 256 KiB / 100 scanned rows per page; strict HMAC file-generation cursors. Append invalidates cursor explicitly; no hidden re-loop. No cohort total or dedup claim. |
| ec0691a | Actual JS loader tests: single-flight, cursor forwarding, stale/error row clearing, retry controls. Combined LAB history/mobile tests 17 passed. |

These changes are LOCAL, not proof of deployment. Re-run relevant tests and inspect their assertions, not just counts. Do not add test counts as unique coverage.

## Current operational blockers

- Inventory last observed SCAN, 39,423 files, STALE_REVALIDATING, no authoritative checksum or ACK eligibility. Same resume token ddd831c57f6148fce0882821133af76a8dcb992c57f3ae189a4974219f5d95e7 had advanced through 38,408 -> 39,143 -> 39,333 -> 39,423. Do not restart an advancing worker.
- Local canonical generation marker: RETIRED_AWAITING_VERIFIED_FRESH_PROMOTION, ready=false, raw_payloads_deleted=false. No current pointer at last inspection.
- Installed analyzer :9001 is unbound/stale; complete export refuses missing required raw mirror artifacts. User sees HTTP 500 at /download/everything. This is not proof of lost Fly data and must not be repaired by fabricating empty files.
- Existing batch/resume scripts are scripts/start-fly-batch-sync.ps1 and scripts/fly-sync-generation-resume.ps1. Transfer requires authoritative pinned generation, exact revision/epoch/config and immutable ACK. Download does not delete source. Retention must preserve active lifecycle/recovery dependencies.
- Local report/writer gate incident traced to 5.28s gate wait plus processing beyond 5s hook deadline; late callback return is not durable completion.
- No current qualified strategy, completed forward trial, or Bitfinex readiness verdict.

## Newly established LAB defects — priority independent verification

Live convenience ledger last returned CONTINUOUS: 122 closes, 75 wins, 47 losses (61.5%, NOT 75%), reported +$1.48; 121 short, 1 long. Gross wins $3.36 and losses -$1.85 do not exactly reconcile to reported net. Historical row-level explanation is not yet proven.

Source trace to verify against DEPLOYED blob as well as local tip:
- continuous_score_gap_execution_tier requires executable non-abstaining AI verdict, valid direction and score sum >=50; gap >=5 yields executable tier. This path is NOT all rejected opportunities.
- _spawn_lab_combo_shadow creates legacy Continuous with pullback=0, virtual_entry=current price and virtual_fill_t=0; policy continuous_shared_direction_gap_v1. It assumes instantaneous fill, unlike separate 0.1% maker-limit paper logic.
- simulate_replay_outcome books configured stop/lock thresholds; it does not prove exit liquidity or queue position.
- Its field named net_pnl_usd is computed from margin percentage without subtracting fees/funding. Naming it verified net profit is wrong.
- Default LAB buffer TTL 30 minutes versus two-hour time exit can yield BUFFER_TRUNCATED. Finalization can still add filled truncations to convenience close/win/loss totals.
- Existing _load_reconciled_lab_outcome_metrics excludes Continuous and scans the whole file. Codex agent is repairing this; do not concurrently edit it.
- New history projection matches actual timestamp/fill fields. Existing LAB ai_snapshot does not itself preserve long/short scores or model ID; original evidence join or producer amendment is needed, never invent values.

## Proposed direction to challenge

Repair outcome/cost honesty and complete the current mirror/analyzer chain before selecting one candidate beside a benchmark. Retain research comparisons across all families, AI accepted/rejected directions and causal market conditions. Retiring execution tiles must be an atomic registry/runtime/API/analyzer/monitoring change, not merely hiding HTML.

Compare static versus causally selected dynamic policy with grouped, purged walk-forward and untouched holdout. Count independent opportunities, not variants. Equal eligibility for paper/shadow requires equal evidence standards, not equating instant touches with depth-supported limits. Higher frequency must be evaluated against avoided losses and costs, not assume every rejection was a missed winner.

## Requested parallel audit tracks

1. OPERATIONS/TRANSFER: Is inventory actually advancing efficiently? Any avoidable full reindex/scan? Do bounded packages preserve exact members and resumable ACK semantics? Can latest completed evidence be transferred without restarting or stressing the current owner? Identify the precise next operational step.
2. COLLECTION/ACCOUNTING: Reproduce the LAB defects above; check all AI verdicts and both sides from producer to terminal. Can truncation, missing costs, duplicates, partial fill or look-ahead enter a profitable score? What exact evidence is missing versus present but ignored? Assess whether current repair addresses the root cause rather than merely changing labels.
3. ANALYZER/STRATEGY: Trace fresh schemas into reports. Does the complete ZIP gate require obsolete files? Are reports current and source-bound? Can fixed/MFE/hybrid/ATR and AI-filter comparisons genuinely execute through the actual adapter/proof/evaluator, not just unit fixtures? Check holdout availability, leakage, grouping and dynamic-selector causal features.
4. UI/REGRESSION: Independently test main and analyzer navigation and exports read-only. Verify population vs explained UNKNOWN. LAB preview at http://127.0.0.1:9017 is SYNTHETIC ONLY, pinned 6b1697f: root verified 100+22 pagination and literal hostile text; mobile screenshot not yet a passing receipt. Do not confuse preview with deployed UI. Check gross/net labels and counts.

## Required reply format

- Pin source SHA, production SHA, epoch and observation time separately.
- Table: claim / VERIFIED, CONTRADICTED or UNVERIFIED / exact file lines, command output or runtime receipt.
- Prioritized defects with a reproducible counterexample and smallest complete fix. Distinguish product bug, missing evidence and normal waiting.
- List tests actually executed and what they fail to cover.
- Name any unnecessary complexity or work that should stop; propose faster safe alternatives with integrity preserved.
- Give next THREE actions in dependency order and state which Codex agent owns overlapping work.
- State technical readiness and strategy qualification separately. No guessed completion percentages, unsupported dates, profitability claims or endorsement based solely on test count.
- If no independent evidence supports a claim, say so. Ask targeted questions for missing receipts; do not repeat stale assumptions.
