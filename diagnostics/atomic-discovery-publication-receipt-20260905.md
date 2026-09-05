# Atomic discovery report integration — 5 September 2026

## Scope

Local source only. No Fly deployment, trading change, sync restart, cleanup or analyzer run.

- Existing atomic analyzer publisher now includes `discovery_cohort_scorecard_report.json` as Matched Paper and Shadow Research.
- Baseline replay is materialized in the same invocation and fenced by unchanged canonical manifest bytes. No saved prior baseline is substituted after a failure.
- Adapter validates eight generation identity fields, artifact path containment, SHA-256, row shape and count. The exact compressed bytes hashed are those decoded.
- Original row identity is retained; foreign generations are counted, not relabeled as current. Raw ADX is not silently converted into a bucket.
- Opportunity/decision provenance conflicts become explicit UNKNOWN reasons. Terminal observed execution/cost models are separate from conservative models.
- Missing inputs produce a fresh UNKNOWN report; no stale winner is reused. This integration does not itself supply missing fills/costs, materialize every strategy, or qualify a live winner.

## Executable receipt

Canonical BTC_AGENT_DATA_DIR set; Python pytest command:

```text
python -m pytest test_discovery_cohort_scorecard.py test_discovery_scorecard_publication.py test_discovery_scorecard_manifest.py test_policy_evidence_evaluator.py test_research_v3_report.py test_analyzer_atomic_publication.py test_analyzer_mobile_access_contract.py test_policy_cycle_snapshot.py -q
154 passed in 10.31s
```

Root publisher also passed Python compilation. Independent agents reviewed the adapter, publication contract and evaluator provenance within disjoint file ownership.

## Runtime boundary

Heartbeat 2026-09-05T00:14:46Z: inProgress=true, chunk_complete, lifecycle.jsonl, fileIndex=157/34433, fileBytes=remoteBytes=39478333. PID16576 responding. Observed/deployed revision df45887e1526; retained mirror 9b588c0b5f79; revisionParity=MISMATCH. No terminal ACK claimed.

The manifest count is not the number of changed files. An earlier throughput estimate based on an untracked old script was rejected; no completion ETA is supported.

Actual current-cohort publication and post-change desktop/mobile visual QA remain pending. Prior dashboard restart was policy-rejected; no alternate restart attempted. No data was deleted. Cleanup remains exact-path, active-reference and retention checked, with measured before/after bytes required.
