# Research tile lifecycle

The execution architecture is frozen; the experiment roster is not. A tile is
active only when it is declared in `ACTIVE_TILE_REGISTRY` in
`combo_pathway_config.py`. Runtime, APIs, dashboards, analyzers and monitoring
must consume that registry or `active_tile_lifecycle_manifest()`.

## Add a tile

1. Add one registry specification with a unique lane, policy ID, ID prefix,
   toggle key, lifecycle state, implementation module and dedicated tests.
2. New experiments start `PAPER_ONLY`, relay-ineligible and default OFF.
3. Add the policy implementation and its focused tests.
4. Wire generic registry consumers; do not add another active-tile roster.
5. Run registry, execution-graph, signal-parity, analyzer-parity and visual QA.

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

`test_every_policy_module_is_owned_by_one_active_tile` fails when a
`paper_policy_*.py` implementation is orphaned or silently added outside the
registry. This is the garbage-collection guard for retired experiments.
