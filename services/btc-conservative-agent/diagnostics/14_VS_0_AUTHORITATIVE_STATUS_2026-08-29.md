# Authoritative 14-vs-0 status

Generated: `2026-08-29T10:57:16.7151433+10:00`

Policy: `OFFSET_0.20_CHASE_w234_s50_i180|CHANDELIER_2.5`

Status: `UNKNOWN_UNVERIFIABLE_EXACT_EPISODES`

Production trading behavior changed: **no**.

## Source and dataset identity

| Field | Verified value |
| --- | --- |
| Local source revision | `04b1f4c5c552390c06db47e05b4fc253443117dc` |
| Production-published revision | `04b1f4c5c552` |
| Revision parity | `MATCH` |
| Dataset epoch | `epoch-4661cc4ae5c1cb6648b82685` |
| Tile/config signature | `87ec52b3df04d50580e8fcd632de2a5996f253c0ae55bbe5be2eb12945dabafd` |
| Last committed analyzer snapshot | 601 files; 684,070,488 bytes; 4,636 counted rows; 206 opportunities |
| Analyzer completion | `2026-08-29 09:22:10 AEST` |
| Analyzer manifest SHA-256 | `44dc2d11d2e9e33058c5a24e2bfe8c085456f2072f493d901afeda37ddecc863` |
| Analyzer revision/epoch/tile parity | `MATCH_FOR_LAST_SUCCESSFUL_SYNC_SNAPSHOT` |
| Present-time canonical parity | **NOT PROVEN**: the later 696-file sync downloaded the data but its canonical manifest commit failed because sync-state metadata drifted from an atomically replaced hot file. |

The last committed canonical snapshot is internally valid. It is not permissible to claim that it represents the newest Fly volume state until a fresh manifest commit succeeds.

## Meaning of `OOS episodes = 14`

It means **14 distinct candidate opportunities in the chronological final 30% holdout** used by that historical policy-grid generation.

It does **not** mean 14 ideal entries, 14 conservative fills, or 14 complete hypothetical entry/exit lifecycles.

## Exact episode accounting

| Required result | Authoritative value |
| --- | --- |
| Candidate OOS opportunities | `14` |
| The 14 episode IDs | `UNAVAILABLE` |
| Ideal entries | `UNAVAILABLE` |
| Conservative full fills | `0` in the surviving aggregate |
| Conservative partial fills | `0` in the surviving aggregate |
| Unknown/missing-data cases | `UNAVAILABLE` |
| True no-fills | `UNAVAILABLE` |
| Per-episode rejection codes | `UNAVAILABLE` |

Precise blocker: the transient policy row's episode-level receipts were not retained, and the policy is absent from the current generation, current mirrored report, and retained recent bundles. The surviving aggregate cannot identify the 14 opportunity IDs or divide zero fills into `NO_FILL` versus `UNSUPPORTED`. Any such list or split would be invented.

## Partial-fill conclusion

Current conservative-engine behavior is verified as follows:

- A positive accepted quantity below the request is classified as `PARTIAL_FILL`.
- It increments supported and partial-fill metrics and is displayed separately from full fills.
- High-precision positive partial quantities are not rounded to zero by the conservative evaluator.
- Displayed depth is not accumulated across seconds or cancel/reprice generations.
- The actual-paper pre-fill gate is all-or-nothing and does not accept partial depth; changing that would alter production behavior and was not done.

For the historical 14 opportunities, whether quantities were rounded, dropped, overwritten, blocked by minimum lot/notional, or lost during aggregation is **unverifiable** because the per-episode receipts and upstream quantity provenance are missing.

## Root-cause verdict

`0/14` is **not proven legitimate and not proven to be an execution bug**. The proven defect is evidence retention/report provenance: the aggregate was published without durable episode IDs and conservative receipts, making the result unauditable.

Positive ideal-touch PnL is possible without a conservative fill because ideal touch uses a simulated candle/price-path touch and subsequent path. It does not establish fresh BBO, executable depth, queue position, requested quantity, or a venue-executable entry.

## Regression verification

Fresh run at `2026-08-29 10:57 AEST`:

- `56 passed, 3 subtests passed, 0 failed` in 2.31 seconds.
- Covered buy and sell crossing, trade-through behavior, partial fill, high-precision partial quantity, genuine no-fill, missing/incomplete market data, cohort aggregation, and UI separation of full/partial/no-fill/unsupported classifications.
- Missing required market data is classified as `UNSUPPORTED`/unknown, not `NO_FILL`.

Until the exact receipts are recovered, this historical policy must remain diagnostic and unqualified.
