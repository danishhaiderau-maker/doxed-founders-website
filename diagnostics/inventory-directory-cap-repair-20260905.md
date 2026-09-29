# Inventory directory bound repair

## Incident

Authenticated manifest at 2026-09-05T05:59:33Z classified the production failure
as DIRECTORY_ENTRY_LIMIT_EXCEEDED. Installed revision cb7745e827e1 has a
10,000-entry per-directory ceiling; progress stopped at 2,038 files / 327
directories. No current inventory SHA or completed transfer was established.

## Bounded repair

Raise only the per-directory count ceiling to 100,000 in the canonical worker
and identical signal-engine dependency. Preserve generation entry limits,
SQLite spool limits, nonce/identity/lease checks and publication rules. Exclude
the unrelated dirty dead-PID lease and timing changes.

The directory snapshot still sorts a finite in-memory list and commits one
SQLite transaction. Its nominal slice deadline is checked outside that operation;
the outer subprocess deadline remains the hard time bound. RSS is telemetry,
not a separately enforced memory limit. Actual production RSS and latency must
therefore be observed after deployment; this is not a throughput receipt.

## Exact-source verification

Exported only the staged index into .qa-inventory-cap-20260905, excluding all
unrelated working-tree changes. Full signal parity passed, including identical
inventory worker hash ba3c13c29b8f and bot/engine hash c2f14e2f1e31.

From the clean export's services/btc-conservative-agent:

`python -m pytest test_data_sync_inventory_worker_contract.py test_data_sync_cadence_throttle_contract.py test_data_sync_bundle_runtime_topology.py test_collector_signal_snapshot.py test_research_v3_signal_snapshot.py test_lifecycle_signal_snapshot_dependency.py test_analyzer_signal_snapshot_projection.py test_data_sync_signal_snapshot.py -q`

Result: 244 passed in 50.42 seconds. Independent cap-only review: 8 passed,
37 deselected; these overlap and must not be added. Tests include exact 100,000
admission / 100,001 rejection, >10,000 freeze/resume, generation ceiling and
atomic spool-limit rollback. CI additionally runs snapshot and runtime-root
regressions before production maintenance/deployment.

## Runtime boundary before dispatch

Public status at 2026-09-05T06:14:10Z: installed cb7745e827e1, PID 662,
paper-only, unpaused / ALLOWED, Bitfinex disabled, relay disarmed, pending/open
0/0. AI/evaluation advancing. Post-AI submitted/completed counter 26, but one
reversal-study dead letter and HOOK_TIMEOUT are retained as an open evidence
gap, not counted as complete evidence. DoxxedFlyMirrorSync remains disabled.

No data was deleted, acknowledged or newly downloaded by this repair. Guarded
deployment, advancing current inventory, verified batch transfer, atomic analyzer
publication, strategy qualification and visual acceptance remain required.
