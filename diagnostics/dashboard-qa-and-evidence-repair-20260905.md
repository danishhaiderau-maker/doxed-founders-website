# Dashboard QA and evidence-reader repair — 5 September 2026

## Scope and outcome

Primary inspected the local analyzer with browser screenshots at the normal in-app width and 390x844. An independent agent inspected the existing Edge main-bot dashboard read-only. No trading, pause, tile, sync, deletion or live controls were exercised. Browser viewport override was reset. This is a partial QA receipt, not a readiness declaration.

The earlier navigation audit covered 25 analyzer subtab views. This pass visually sampled Overview, Chase attribution, Ladder Simulator, Genome, Entry & Regime Evidence, Downloads, Report Explorer and Runtime Incidents. Main bot covered Overview, Decisions and Data plus visible pathway, activity and safety sections. All filter permutations, exports, detail links and main-bot mobile layout are NOT verified.

## Findings

- Analyzer is an old September 2 generation, source 9b588c0, while Fly is df45887. It correctly blocks qualification; its header incorrectly says fresh collection. No current best strategy is established.
- Raw integrity JSON and a second freshness banner occupy over half a narrow first screen. Four primary navigation groups are reasonable; long diagnostic/legacy details should be progressively disclosed rather than erased.
- Entry & Regime Evidence and Report Explorer show empty bodies without actionable messages. Downloads imply a verified current ZIP despite stale data. These are presentation defects, distinct from absent upstream data.
- Existing ladder view correctly explains that 247 paths have no eligible matched current execution cohort. Do not populate results by dropping identity requirements.
- Main bot is responsive and Bitfinex-disarmed, but ADMIN_MANUAL-paused. Session AI calls and submitted orders are zero. A live websocket and old restored AI history do not establish progressing paper cycles.
- Main bot critical state/funnel sits below tall strategy cards. Recommended next UI work: compact top status/funnel, last-AI age, exposure counts, source revision and mirror/analyzer freshness; collapse detailed tile history and raw payloads. No main-bot source change in this patch.
- Paper/shadow equality currently is not proven: evidence-world naming differs, candidate admission depends on terminal intents, scorecard sets are not genuinely matched, and fill/cost models differ. Preserve both sources with equal research eligibility, but require matched causal opportunity, schedule, quantity, tape and cost-model receipts rather than pooling results.

## Implemented local changes

- Consume validated normalized pre-entry bucket features with timestamps and provenance. Preserve valid dimensions from partial receipts; invalid/missing dimensions and overall qualification remain UNKNOWN. Reject fatal identity/timestamp/nonfinite defects. Do not substitute bucket evidence for raw measurements.
- Correct an old test fixture that omitted the committed-required config_signature; no production provenance gate was relaxed.
- Compact freshness/integrity details, correct stale header and forensic export language, show visible research-design and report-manifest errors/empty states, remove advice to launch a duplicate analyzer.
- Update recovery goal with matched paper/shadow research, visual QA, independent tests and safe workspace hygiene.

## Verification and remaining publication blocker

- Primary combined evaluator/dashboard/provenance/download suite: 62 passed in 4.74s.
- Primary atomic-publication and mobile-table source contracts: 18 passed in 2.01s.
- These 80 tests are executable source/contract checks, NOT post-change rendered-browser acceptance.
- Dashboard-only reload was rejected by execution policy before the command ran. Original listener PID13804 remained on 127.0.0.1:9001. No alternate mechanism was attempted. The browser still serves old source; post-change desktop/mobile visual verification is OPEN.
- Sync is advancing independently. At 2026-09-04T23:53:18Z it reached file21/34433 (bot_runtime.log); the 99,332,096-byte lifecycle index finished transferring. Canonical parity remains MISMATCH until whole-generation verification and promotion. No terminal ACK or deletion receipt claimed.
- No research data or obsolete files were deleted in this pass; no disk recovery is claimed.
