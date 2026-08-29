# 14 OOS vs 0 conservative fills audit

Policy: `OFFSET_0.20_CHASE_w234_s50_i180|CHANDELIER_2.5`

Status: `UNKNOWN_UNVERIFIABLE_EXACT_EPISODES`

Refreshed at: `2026-08-29T09:38:14.7118579+10:00`

Current local and production source revision: `04b1f4c5c552390c06db47e05b4fc253443117dc` (production publishes the short form `04b1f4c5c552`).

Current mirrored dataset epoch: `epoch-4661cc4ae5c1cb6648b82685`.

Canonical migration parity: `MATCH` for manifest entry `14eff0a43046ab418f3d11c2621c334090ea84954cf2960b9d193cee9a52a8b7` (601 files, 684,070,488 bytes, 4,636 counted rows, 206 opportunities). The authority is the Fly persistent volume and sync direction is Fly-to-local only.

The canonical analyzer completed at `2026-08-29 09:22:10 AEST` against that synchronized snapshot. Its report manifest is SHA-256 `44dc2d11d2e9e33058c5a24e2bfe8c085456f2072f493d901afeda37ddecc863`, and the append-first canonical manifest advanced to entry `630177cbfcecc4905a77c9a7d7ef9b25e085f686e972f172af8402cd5fa90f3e`. Revision, epoch, and tile-signature parity for that analyzer generation are `MATCH`.

Current Fly parity is **not freshly verified**: after the completed snapshot, the Fly data-sync manifest returned HTTP 503 while the dashboard was restoring. The sync heartbeat therefore retains the last successful `09:10:45 AEST` snapshot and marks its current poll failed. This does not alter the historical-policy conclusion below, but it prevents claiming present-time Fly parity.

This was a read-only diagnostic. No paper, relay, or production trading behavior was changed.

## Finding

The previously rendered policy row is not present in the current analyzer generation, the current mirrored report, or the retained recent research bundles inspected on 2026-08-29. Its exact 14 episode IDs and per-episode receipts therefore cannot be reconstructed without inventing evidence.

Authoritative counts for that historical row are consequently:

| Field | Authoritative value |
| --- | --- |
| OOS candidate opportunities | 14 |
| Exact episode IDs | `UNAVAILABLE` |
| Ideal entries | `UNAVAILABLE` |
| Conservative full fills | 0, as displayed by the old aggregate |
| Conservative partial fills | 0, as displayed by the old aggregate |
| Unknown/missing-data cases | `UNAVAILABLE` |
| True no-fills | `UNAVAILABLE` |
| Per-episode rejection codes | `UNAVAILABLE` |

The aggregate zero-fill display is insufficient to divide the 14 opportunities between unsupported and genuine no-fill cases.

`OOS episodes = 14` means 14 distinct candidate opportunities in the final 30% of the policy's chronological opportunity rows. It does not mean 14 ideal entries, 14 conservative fills, or 14 complete entry/exit lifecycles.

Ideal-touch PnL is calculated in a separate diagnostic world from a simulated touch timestamp and subsequent price path. It is coherent as a hypothesis without a venue-executable entry, but it is not execution evidence.

Conservative execution has four distinct classifications:

- `FULL_FILL`
- `PARTIAL_FILL`
- `NO_FILL`, only when complete fresh evidence proves no fill
- `UNSUPPORTED`, when required market evidence is missing, stale, invalid, mismatched, duplicated, or incomplete

Consequently, `0 conservative fills` alone cannot prove that all 14 were genuine no-fills. Until their receipts are recovered, the exact row must be described as unknown/unverifiable.

## Partial-fill accounting

The conservative evaluator bounds accepted quantity by contemporaneous executable top-of-book depth. Any positive accepted quantity below the request is emitted as `PARTIAL_FILL`; it increments partial-fill and supported-episode counts and is displayed separately from full fills. It does not accumulate displayed depth across seconds or cancel/reprice generations.

The current actual-paper pre-fill depth gate is intentionally all-or-nothing: depth below the full requested quantity is rejected before the simulated fill resolver. Therefore actual paper entries do not currently create accepted partial quantities at that gate. Changing this would be a production-behavior change and was not part of this audit.

The conservative candidate receipt does not preserve upstream raw quantity, lot-size rounding, minimum-lot, and minimum-notional provenance. Those fields therefore cannot be truthfully supplied for the missing 14-row artifact.

## Verification

Focused tests cover LONG and SHORT crossing, thin-depth partial fill, high-precision quantity preservation, incomplete or missing market evidence, cohort partial aggregation, and dashboard separation of full, partial, no-fill, and unsupported evidence.

Fresh focused rerun at `2026-08-29 09:37 AEST`: **41 tests and 3 subtests passed in 2.14 seconds; 0 failed**. The broader earlier audit run remains **56 tests and 3 subtests passed in 2.56 seconds; 0 failed**.

## Required future evidence

Future exact-policy diagnostics must retain each OOS opportunity ID and its conservative receipt, including requested quantity, proven executable quantity, remaining quantity, trigger BBO timestamp, side-correct quote, visible depth, chase interval/generation, evidence bucket IDs, and exact negative reason codes. Missing required market evidence must remain `UNSUPPORTED`/unknown, never `NO_FILL`.
