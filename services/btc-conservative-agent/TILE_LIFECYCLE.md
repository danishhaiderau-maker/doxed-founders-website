# Research tile lifecycle

The execution architecture is frozen; the experiment roster is not. A tile is
active only when it is declared in `ACTIVE_TILE_REGISTRY` in
`combo_pathway_config.py`. Runtime, APIs, dashboards, analyzers and monitoring
must consume that registry or `active_tile_lifecycle_manifest()`.

Changing the roster is an atomic registry migration, never an isolated UI edit.
The migration is not complete until every cross-layer consumer publishes the
same ordered manifest and registry signature at the exact deployed revision.

## Add a tile

1. Add one registry specification with a unique lane, policy ID, ID prefix,
   toggle key, lifecycle state, implementation module and dedicated tests.
2. New experiments start `PAPER_ONLY`, relay-ineligible and default OFF. Only
   an explicit owner request may set `default_enabled=True`, and the registry
   refuses it unless the tile stays paper-only and relay-blocked.
3. Add the policy implementation and its focused tests.
   A tile that owns its model call (`OWN_AI_CALL`) declares its own purpose in
   `TRADING_AI_ALLOWED_PURPOSES`, runs the call on its own execution worker,
   makes no call while OFF, and states the per-day cost in its PR.
4. Wire generic registry consumers; do not add another active-tile roster.
5. Run registry, execution-graph, signal-parity, analyzer-parity and visual QA.
6. Deploy only at the required safe boundary, start a clean signed cohort, and
   prove two advancing collection/analyzer cycles before accepting evidence.

A tile that only changes how an existing side rule enters (for example a
resting maker limit instead of a taker) reuses a generic binding
(`taker_time_exit_binding.py`, `maker_time_exit_binding.py`,
`maker_chase_time_exit_binding.py`, `maker_confirm_market_time_exit_binding.py`)
and adds no runtime branch; its thin
policy module wraps the binding that matches its registry
`entry_policy.mode`, and the binding refuses any other mode. The maker-chase
binding reprices through the generic family chase. A cross-venue clock tile
supplies `make_evaluator()` and its own shadow file; the per-second loop,
capacity and submission throttles are generic. A new shadow file must be
added to the JSONL append literals, research dashboard list, reset
inventory and `scripts/self_aware/schema_registry.json`.

Every specification carries plain-English card metadata (signal, side,
order, chase, time limit, sessions and stand-aside gates; live exits in
first-trigger-wins order; risk: early cut, hard stop, size, max concurrent,
kill rules). `tile_card_sections(lane)` renders the ENTRY / EXIT / RISK
MANAGEMENT sections for both dashboards, and the registry validator fails
if any section is missing. Size is stated as margin and notional
("$0.25 margin @100x ≈ $25 notional"), never as a maximum loss. Tile
numbers are derived from `ACTIVE_TILE_ORDER`; never hard-code "Tile N".

The analyzer cycle freezes the active roster into the forward-tracker
hash chain once per `RESEARCH_STACK_VERSION` (batch
`REGISTRY-<version>`); bump the version with every roster or rule change.

## Baseline benchmark

`FAMILY_CONTINUOUS_AUG_ORIGINAL` is the permanent baseline: paper-only,
relay-blocked, default ON. It is never retired with an experiment roster
change, never a promotion candidate, and every other tile is reported against
it. Replacing it needs an explicit owner request and a new signed lane.

## Retire a tile

1. Suppress entries and cross a verified flat paper/exchange boundary.
2. Remove the tile from `ACTIVE_TILE_REGISTRY` and `ACTIVE_TILE_ORDER`.
3. Add its lane token to `RETIRED_TILE_LANES` for one release.
4. Physically delete its policy module, dedicated API/UI/analyzer/monitoring
   branches and dedicated tests. Do not merely hide or disable the card.
5. Keep generic safety, lifecycle, reconciliation and evidence primitives.
6. Quarantine historical evidence as opaque archive data; never let it revive
   current execution or ranking code.
7. Run the full cross-layer audit and rendered visual QA before deployment.
8. Start the next cohort with a new signed policy/registry identity; archived
   rows remain descriptive and cannot enter the current ranking cohort.

`test_every_policy_module_is_owned_by_one_active_tile` fails when a
`paper_policy_*.py` implementation is orphaned or silently added outside the
registry. This is the garbage-collection guard for retired experiments.

## Completion gate

A tile add or retirement fails closed when any of these remain:

- an unregistered `paper_policy_*.py` module;
- a missing registry-owned policy or dedicated test module;
- a hard-coded active-tile list outside the registry adapter;
- an API, dashboard, analyzer, report, sync, deploy, or monitor receipt with a
  different roster/signature;
- a retired lane on an executable or current-cohort path;
- a hidden-but-still-callable route, flag, toggle, worker, or policy branch;
- evidence from the old roster mixed into the new signed cohort.
