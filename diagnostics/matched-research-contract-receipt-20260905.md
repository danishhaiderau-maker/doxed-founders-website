# Matched research contracts — 5 September 2026

Previous turn classification: PROGRESS (committed analyzer/UI changes plus executable tests). Current turn: PROGRESS plus verified live transfer observation.

## Implemented

- Upstream causal normalizer rejects boolean/nonfinite signal, capture and observation timestamps; rejects malformed/nonfinite feature values; preserves other valid dimensions and blocks aggregate completeness. Missing opportunity identity cannot join on empty strings.
- Report epoch-filter fixture now completes the existing-store idempotency bootstrap before appending. The prior append returned EMERGENCY_IDEMPOTENCY_INDEX_INCOMPLETE; missing input was not evidence of an epoch-selector defect. No production index gate was weakened.
- Discovery scorecard schema v2 replaces unconditional paper/shadow equality with per-policy/cell independent episode intersections, unmatched IDs, duplicate exclusion and explicit identity blocker counts. Schedule IDs and hashes remain distinct, as do tape IDs/hashes. Positive quantity, generation, opportunity, direction, config, cost-model and simulation-model identity are mandatory for comparison. World PnLs remain separate; differing models are calibration only; invalid PnL remains unknown.

## Executable receipt

From canonical services/btc-conservative-agent with BTC_AGENT_DATA_DIR explicitly canonical:

`python -m pytest -q test_discovery_cohort_scorecard.py test_research_v3_report.py test_policy_evidence_evaluator.py test_research_dynamic_entry_policy.py test_entry_baseline_replay.py`

Result: **119 passed in 6.22s**. This verifies component contracts, not current production strategy qualification.

## Next integration and runtime gates

- The scorecard truthfully reports upstream_input_wiring_complete=false. Integrate through the existing root analyzer_research_engine_v62.py write_report_manifest pipeline after conservative-result persistence and before atomic manifest publication. Report Explorer can then consume the normal manifest report entry.
- Use signed entry_baseline_replay materialization for opportunities without paper intents. Do not weaken the terminal execution candidate path or synthesize absent schedule, policy, tape or cost signatures.
- Saved local evaluator and current manifest hashes differ; do not combine those files into a purported current scorecard. Wait for the verified mirror and generate all report inputs together.
- Authenticated production sample remained df45887, force-paper, disarmed, ADMIN_MANUAL-paused, flat 0/0; new AI/evaluation cycles are not progressing under that pause. Resume remains an explicit ordered recovery action, not a conclusion from HTTP 200.
- Sync owner PID16576 was verified live/responding. At 2026-09-05T00:00:37Z it had advanced to file72/34433, market_microstructure_1s.jsonl.9, 19,922,944/20,971,524 bytes. Canonical parity was still MISMATCH. No terminal ACK or source cleanup was claimed.
- Dashboard process-control restriction from the preceding turn remains; no workaround attempted and no post-change visual pass claimed.
- Cleanup audit found ~26.1 MiB disposable caches; tiny unique forensic receipts were retained. No deletion or recovered disk bytes claimed.
