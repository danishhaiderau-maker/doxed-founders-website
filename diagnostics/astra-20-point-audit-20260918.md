# Astra independent audit — BTC V3.1 points 1–20

Date: 18 September 2026. Read-only audit; no deploy, delete, restart, ACK,
credential mutation, or Bitfinex action performed.

## Current facts

- Fly revision `f84fff81e290`; `system_ready=true`, real WS/REST/OHLCV
  prerequisites ready, `force_paper_mode=true`, `ADMIN_MANUAL` paused, flat,
  `live_armed=false`, Bitfinex disabled.
- Current AI polling is skipped with `ADMIN_MANUAL_PAUSE`; this does not explain
  every historical “evaluation not reached” row without scan-ID joins.
- Watchdog incident is inactive; historical `WS_TRANSPORT_STALLED` text remains
  as history only.
- Analyzer public summary is `ok=false`, process not running, mirror source
  `9b588c0b5f79` stale.
- Pinned transfer owner PID 21660 is alive. The latest receipt observed at
  11:22 AEST is `fileIndex=8263/16234` (50.93%) with `inProgress=true`,
  `ok=true`, and `revisionParity=MATCH`; the mirror state has 8254 unique
  paths and no missing paths. Progress is not terminal completion or ACK.

## Numbered acceptance matrix

| # | Status | Evidence and missing acceptance |
|---|---|---|
| 1 | PARTIAL | Paper mode and live market prerequisites are verified. After safe resume, prove advancing real-data AI/decision/paper cycles with feed timestamps bound to episodes. |
| 2 | PARTIAL | Conservative-fill code/tests exist, but an inspected assertion says `queue_position_model=NONE`. Trace rest→chase→partial/full fill→exit with depth/cost evidence and state limits honestly. |
| 3 | PARTIAL | Runtime exposes paper order counters and the dashboard separates research verdicts from orders; counters are currently zero. Reconcile order/event/position/closed-trade identities on new episodes and reject synthetic fills. |
| 4 | PARTIAL | Separate conservative/conditional shadow modules and manifests exist; current full-cohort coverage is unproven. Produce opportunity-level long/short/reject/no-fill coverage with common inputs and separate fill-world labels. |
| 5 | PARTIAL | Source warns research evaluations are not account orders; no current did/missed reconciliation is verified. Publish matched opportunities with missing/unverifiable cases explicit and never count hypothetical fills as paper fills. |
| 6 | PARTIAL | Local score-led override preserves raw verdicts and selects a stronger side for disarmed research; current execution is paused. Prove deployed non-tie, raw-REJECT, and true-tie traces through lanes; direction selection does not guarantee a fill. |
| 7 | PARTIAL | Source preserves `raw_decision`, `raw_direction`, and `SCORE_LED_STRONGER_SIDE`; current AI calls are skipped and provider-evidence deployment is unverified. Capture exact provider request/response/error receipts and matched AI/no-AI outcomes. |
| 8 | UNVERIFIED | Prior wipe acceptance and measured space result were not proven. Locate the exact wipe/epoch/retention receipt; do not wipe during this transfer. |
| 9 | CONTRADICTED at checkpoint | Fresh AI-led episodes are not progressing while `ADMIN_MANUAL_PAUSE` is active; market ingestion may continue. Resume only at the approved boundary and prove multiple durable provider/lane/outcome cycles. |
| 10 | PARTIAL | Resumable per-file transfer and checksum code exist, but thousands of tiny HTTP requests are not yet a demonstrated small-package protocol. Finish coverage/hash verification and terminal ACK; later measure bounded packaging/incremental reuse. |
| 11 | CONTRADICTED if read as raw deletion | Governing policy is retain ACKed source and reclaim only eligible superseded transport copies. Require archive proof and measured reclaimed bytes; ACK is not purge permission. |
| 12 | CONTRADICTED operationally | Public analyzer summary says `ok=false`, `live_analyzer_process=false`; current listener/scheduler was not independently proven. After ACK, publish atomically to the verified mirror and capture process/freshness/repeat-run receipts. |
| 13 | PARTIAL | Cost/missing-depth tests exist; no current accepted scorecard. Publish after-cost EV, sample units, uncertainty, fills, missingness, and drawdown on the current cohort. |
| 14 | PARTIAL | Historical contamination warning and exclusion code exist; complete aggregate exclusion is unproven. Add negative fixtures and trace exclusions through every family/dynamic aggregate. |
| 15 | PARTIAL | Dynamic chronological-fold and holdout tests exist but were not run in this audit; no current comparison is published. Use identical opportunities/fill world/cost model, sealed holdout, fixed baseline, and dynamic turnover. |
| 16 | UNVERIFIED / not achieved | No qualified current candidate or freeze/forward receipt. Only after ranking: freeze policy/control hashes, predeclare criteria, and run real-time forward paper. |
| 17 | PARTIAL | Labels/render code exist, but no independent current desktop/mobile navigation/download/error/empty/stale/current QA receipt. Perform both dashboard checks against matching API data. |
| 18 | PARTIAL | Disarmed runtime and paper private-API isolation exist; complete technical limit-order readiness is unproven. Run non-live precision/minimums, idempotency, rate-limit, cancel/reconcile, reconnect, and kill-switch tests. |
| 19 | VERIFIED for current state only | Live latches are disabled and no live action occurred. Preserve explicit “arm Bitfinex live” gating and test fail-closed restart/deploy/control paths; this is not readiness certification. |
| 20 | PARTIAL / not achieved | Transfer is progressing, AI collection is paused, and terminal ACK/current analyzer/ranking/retention cycle is unproven. Close transfer→ACK→publication, resume approved paper, then demonstrate repeated bounded cycles. |

## Provider-boundary refresh (11:21–11:23 AEST)

- Normalized `bot.py`/`engine.py` parity is PASS at
  `2133be1fa3a411030305c6ff93a16430573c0c09f175ed356a0720055ec42719` and
  `manifest.json` matches the prefix.
- Cassette replay is explicitly `CASSETTE` with `synthetic_response=true`;
  downstream exceptions preserve provider `SUCCEEDED` and add a separate
  evaluation error; provider failure is recorded before guarded logging.
- Source/AST/helper checks and the latest focused suite (`25 passed`) pass;
  explicit provider status/evaluation-error fields are now included in the
  state snapshot and AI error export, and provider metadata is recorded before
  `API_OK` telemetry. A call-ID-matched annotation now mirrors post-provider
  evaluation failures into the status snapshot without rewriting provider
  identity. Executable evaluate-branch failure injection and durable-I/O
  receipts are still absent. Nothing is deployed.

## Dashboard and test corrections

- The current watchdog warning is historical; the incident is inactive.
- The current pause explains current skipped AI cycles, not every historical scan
  row; join scan IDs to provider/lane/error receipts.
- Historical PNL contamination warnings must remain until accounting and
  qualification exclusions are reconciled; hiding the warning is not a fix.
- `test_dashboard_research_status_clarity.py` originally placed assertions in
  `main()` only. It now exposes a pytest test as well: direct execution passed
  and pytest reports `1 passed`.
- A true visual desktop/mobile QA receipt is still required.

## Immediate order

1. Preserve PID 21660 and finish the pinned transfer; do not edit its consumed
   scripts or start a second owner.
2. Run independent provider/lane historical joins, release preparation, and
   dashboard QA while transfer runs.
3. At terminal state verify unique coverage, bytes, hashes, SQLite/opaque/
   forensic special cases, FINALIZE identity fences, then ACK.
4. Publish the analyzer only from the ACK mirror, then produce honest rankings or
   explicit blockers.
5. Resume disarmed fresh paper, prove repeatability and eligible-copy reclaim,
   then forward-paper and technical-readiness gates. Never arm Bitfinex.

## Model routing verdict

Astra is appropriate for this safety/identity/acceptance audit. Use one bounded
Sol implementation owner, independent Terra read-only QA, and Luna only for
repetitive evidence. Unchanged transfer polls do not need Astra.
