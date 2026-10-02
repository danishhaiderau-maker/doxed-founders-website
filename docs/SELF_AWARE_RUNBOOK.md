# Self-aware layer runbook

The self-aware layer is a laptop-only daemon on `http://127.0.0.1:9021`. It
answers three questions with evidence: is the system healthy (self-diagnosis),
is the AI any good (AI call log and scorecard), and is there an edge (a
pre-registered screen engine). It never calls Fly, never trades and never
touches the relay or Bitfinex.

- **Code:** `scripts/self_aware/`.
- **Pinned checkout:** `C:\DoxxedCrypto\self-aware-live`.
- **Runtime home:** `C:\DoxxedCrypto\self-aware\`. It holds `selfaware.duckdb`, `state.json`, `repair-journal.jsonl`, `evidence/`, `logs/` and `venv/`.
- **Keeper:** the `DoxxedSelfAware` scheduled task runs `scripts/self-aware-tick.ps1` every 5 minutes.
- **Clients:** `scripts/self_aware_client.py`, plus `health_client.self_aware()`.
- **Alerts:** findings and the hourly digest are written to `laptop-chain/health/alarms.jsonl` as `selfaware.*` events, under the watcher's `tick.lock`. They therefore show in the Alerts section of `:9001`, `:9011` and Fly. The observed text starts with `[self-diagnosis]`.

## Quick start

```powershell
python scripts\self_aware_client.py            # verdict + problems + digest headline
start http://127.0.0.1:9021/                   # HTML overview
Invoke-RestMethod http://127.0.0.1:9021/api/selfaware/health
```

| Endpoint | What it returns |
|---|---|
| `/api/selfaware/health` | Verdict plus findings. Each finding has observed vs expected, ranked probable causes with evidence, a runbook link and drill-down SQL. Also returns signals, repairs and engine job status. |
| `/api/selfaware/findings/history?since=` | Edge-triggered finding transitions (OPENED, CHANGED, CLEARED). |
| `/api/selfaware/uptime` | Uninterrupted run time, interruptions in the last 24 hours, the longest run in 7 days, and the proof window at X of 48 hours. |
| `/api/selfaware/ai/calls?since=&model=&prompt_id=&lane=&labeled=1` | Per-call log: model, prompt id and version, input fingerprint, parsed decision, latency, estimated cost, and forward returns at 1, 5, 15, 30, 60 and 120 minutes. |
| `/api/selfaware/ai/calls/<call_id>` | The same plus the raw response and drill-down SQL into the raw mirror rows. |
| `/api/selfaware/ai/scorecard?window=&horizon=&slice=&strategy=` | Hit rate with a Wilson CI, and after-cost bp with an hour-cluster bootstrap CI. Covers each window × horizon × slice (overall, served model, prompt, session, volatility tertile). Strategies compared: AI, abstain-respecting AI, inverted AI, always-long, random, rule vote and the shadow compact prompt. |
| `/api/selfaware/edges?status=` | Pre-registered screens with walk-forward holdout, BH and Holm correction, minimum N, days and clusters, and a decay check. |
| `/api/selfaware/edges/playbook` | Descriptive regime cells (session × volatility × trend). Not a trading signal. |
| `/api/selfaware/tiles?window=24h|7d|all` | Per-lane closes, win rate, net USD and exit reasons from the V3 execution ledger. |
| `/api/selfaware/receipts` | Deploy runs, proof status and latest row, auto-ff and manual-intervention journals, and the revisions in play. |
| `/api/selfaware/digest?history=N` | The hourly digest. |
| `/api/selfaware/repairs` | The repair journal. |
| `/api/selfaware/tables` | Raw views, result tables and provenance for every result. |
| `/api/selfaware/query?sql=` | Guarded drill-down (see below). |

**Drill-down SQL.** Each query must be a single `SELECT` over the `raw_*` views (custody mirror, analyzer exports, chain files, read in place) and the `res_*` result tables. File-reading functions, path literals, `PRAGMA`, `SET`, `ATTACH`, `COPY` and writes are refused. Results are capped at 5,000 rows and 20 seconds. `raw_order_intent` (over 1 GB) is for ad-hoc use only; no engine job scans it.

**Provenance.** Every `res_*` table has a `provenance` row with:
- source datasets;
- time window;
- code revision;
- `computed_at`;
- `schema_version`;
- row count.

## Operating the daemon

- **Status.** Call `Invoke-RestMethod http://127.0.0.1:9021/api/ping`. It returns the engine revision, PID and per-job `last_ok` and `ms`.
- **Restart.** Stop the `python.exe` whose command line contains `self_aware.engine`. The next tick (within 5 minutes) starts it again. To start it immediately, run `schtasks /run /tn DoxxedSelfAware`.
- **One pass by hand.** Run `cd C:\DoxxedCrypto\self-aware-live\scripts; ..\..\self-aware\venv\Scripts\python.exe -m self_aware.engine --once --no-alarms --no-repair`. Use `--no-alarms` for any run that is not the daemon.
- **Update.** Run `git -C C:\DoxxedCrypto\self-aware-live checkout --detach <merged sha>`, then restart. This checkout is not v2c, so moving it is not a manual intervention for the unattended proof.
- **Footprint.** The process runs at BELOW_NORMAL priority. DuckDB is limited to 2 threads and 768 MB, and jobs run sequentially. The store is about 10 MB and grows with AI calls and edge events. Evidence keeps the newest 500 bundles; history tables keep 7–30 days.
- **Cadence.** Views every 5 minutes, diagnosis every 2, uptime every 5, tiles every 10, AI every 15, edges and data awareness every 30, and the digest every 60.

## Auto-repair policy

Auto-repair is laptop-only, journalled and capped. The daemon never restarts the analyzer, the pull loop or the watcher itself. When an owner stops advancing, it runs that owner's existing scheduled task early, which is what the scheduler would do a few minutes later anyway:

| Action | When | Does |
|---|---|---|
| `nudge_supervisor` | `laptop.supervisor` is AMBER or RED and the Fly snapshots are more than 15 minutes old | `schtasks /run /tn DoxxedLaptopChainSupervisor`. The supervisor restarts `:9001`, the pull loop and auto-ff. |
| `nudge_pull_via_supervisor` | The pull last finished more than 15 minutes ago and custody is not GREEN | The same supervisor run, which restarts the pull loop (ACK and copy retry). |
| `nudge_watcher` | The watcher verdict is more than 20 minutes old | `schtasks /run /tn DoxxedSystemHealthWatcher` |
| `trading_needs_human` | `inv.expired_filled` or `inv.fill_close` is RED, or the relay is armed | **FLAG only, never executed.** |
| `start_self_aware` / `restart_hung_self_aware` | The keeper tick finds `:9021` silent | Starts the daemon, or kills only a hung self-aware process. |
| `restart_stale_self_aware` | `:9021` answers but `/health` is older than 20 min or a job's `last_ok` is older than 3x its cadence + 10 min (after the 10 min start grace) | Restarts the daemon at most once an hour; otherwise logs `STALE (restart cooldown)` in the tick log. |

Each AUTO action has a 15-minute cooldown and at most 6 runs a day. Pass `--no-repair` to journal decisions without executing them.

## Findings

Every finding has an id, a severity (`GREEN`, `AMBER`, `RED` or `SKIP`), observed vs expected, ranked probable causes and preserved evidence. Evidence is written to `self-aware\evidence\<time>-<id>.json` when a finding opens or worsens.

**Cause attribution signals:**
- `deploy_maintenance`
- `cpu_saturation` (Fly)
- `laptop_cpu`
- `stale_venue_feed`
- `lock_holder`
- `deepseek_credit`
- `rate_limited` (HTTP 429)
- `shipper_stalled`
- `paper_paused`
- `runtime_snapshot_stale`

<a id="inv-fill-close"></a>
### inv.fill_close: every fill reaches a close

- **What it means.** A `fill_id` in `v3/ledgers/execution.jsonl` has had no close row for more than 8 hours. Younger unclosed fills are open positions and stay GREEN.
- **Do.** Run the drill SQL, then compare with Fly `/api/state` positions (owner read, rate limited) and the Trades table.
- **Typical causes.** A position is still legitimately held (long runner). A close was lost in shipping (check `inv.custody`). A lifecycle bug.
- **Rule.** Never force-close real exposure from here. Flag only.

<a id="inv-expired-filled"></a>
### inv.expired_filled: no order is both expired and filled (RED)

- **What it means.** Lifecycle rows are TTL-expired or no-fill yet carry a `fill_id`. This is the fill/expiry race that the fill-guard should prevent.
- **Do.** Preserve the evidence bundle and post it on the WALL. A Fly fix waits for the post-freeze batch. Keep Bitfinex DISARMED.

<a id="inv-ai-response"></a>
### inv.ai_response: every AI call has a response row

- **What it means.** An `ai_input_log` row has no tranche (outcome) row, and it is older than the newest tranche row.
- **Causes.** A deploy boundary cut the call. DeepSeek errors or credit (`deepseek.balance`). CPU saturation.

<a id="inv-custody"></a>
### inv.custody: every Fly segment has a verified laptop copy

- **AMBER.** ACK lag exceeds 6 sequences, or the pull is stale with lag, or parity is more than 3 hours old.
- **RED.** `acked > applied`, meaning an ACK without a local copy. Stop and preserve evidence; this breaks custody before pruning.
- **Do.** Check `segment-pull.status.json` and the puller log. The supervisor restarts the pull loop (`nudge_pull_via_supervisor`).

<a id="inv-revision-parity"></a>
### inv.revision_parity: the analyzer runs the revision Fly runs

- **What it means.** v2c HEAD and the latest analyzer run must equal the Fly `git_rev`. Between runs, the `:9001` dashboard must equal it too.
- **Do.** v2c follows Fly only through the supervised auto-ff after a successful guarded deploy. During the freeze, **never move v2c by hand**, because the unattended proof counts that as a manual intervention. Wait for auto-ff, or note on the WALL.

<a id="inv-dashboards-agree"></a>
### inv.dashboards_agree: dashboards agree

- **What it means.** The `:9001` and `:9011` verdicts must agree. The Fly runtime roster, the ON toggles and the analyzer export roster must agree too.
- **Note.** A roster or signature difference right after a deploy clears after the next analyzer generation.

<a id="inv-mirror-ai-lag"></a><a id="inv-mirror-tape-lag"></a><a id="inv-mirror-cross-venue-lag"></a><a id="inv-mirror-decision-lag"></a>
### inv.mirror_*_lag: the laptop mirror keeps up with Fly

- **What it means.** The newest row of a stream on the laptop mirror is older than 30 minutes (AMBER) or 3 hours (RED). For AI, the lag is measured against Fly `last_ai_success_at`.
- **Causes.** The shipper is batching or stalled. The pull is stuck. Fly is paused (no new rows).

<a id="prog-ai-cadence"></a>
### prog.ai_cadence: AI decisions keep coming

- **What it means.** The last AI success is more than 15 minutes old while paper is running, or the mirror shows fewer than 8 decisions an hour. When Fly reports no success time (for example right after a boot), the age of the newest mirror AI row is used instead. AMBER also fires when a `DEPLOY_MAINTENANCE` pause outlives 45 minutes after boot.
- **Causes.** A deploy pause, DeepSeek credit, rate limiting or CPU saturation.
- **Repair.** Flag only. Resuming paper after a deploy belongs to the deployer or operator.

<a id="prog-tile-orders"></a>
### prog.tile_orders: ON tiles keep producing order-eligible decisions

- **What it means.** An ON tile has had no order-eligible decision for more than 6 hours. This can be a quiet market; check the decision-ledger reasons in the drill SQL.

<a id="prog-feeds"></a>
### prog.feeds: venue feeds are fresh

- **What it means.** The Bitfinex WebSocket age, the cross-venue collector and the XVL evaluator health, all from the Fly runtime snapshot.

<a id="prog-counts-advancing"></a>
### prog.counts_advancing: counts advance

- **What it means.** Shipped and acked sequences, decision rows and tape rows must keep changing. A live process or an HTTP 200 is not progress.

<a id="prog-analyzer"></a>
### prog.analyzer: analyzer cycles complete on time

- **AMBER.** The last success is more than 45 minutes old, or a run has lasted more than 25 minutes, or the last run FAILED, or `:9001` is unreachable.
- **RED.** The last success is more than 3 hours old.
- **Causes.** `laptop_cpu`, `lock_holder` or a post-deploy promotion or migration.
- **Do.** Read `analyzer-run.status.json` and the cycle status. The supervisor owns restarts.

<a id="fly-reachability"></a>
### fly.reachability: Fly is reachable (HTTP 429 means rate-limited)

- **What it means.** HTTP 429 from Fly's public 60/min/IP bucket is **AMBER "rate-limited"**, not RED. It becomes RED only after 15 minutes without a fresh runtime snapshot. The fix (an owner-token bucket) is queued for the post-freeze Fly batch.

<a id="self-engine"></a>
### self.engine: self-aware jobs succeed

- **What it means.** A job failed. The error and traceback are in `state.json` under `job_errors`, and in `logs/daemon.err.log`. A crashed check appears as `self.check_error.*` instead of hiding the other checks.

<a id="hourly-digest"></a>
## Hourly digest

Every hour the daemon writes one digest to `res_digests`, `/api/selfaware/digest` and the Alerts section (`selfaware.digest`). It covers:
- new and recovered self-diagnosis problems;
- watcher alert events;
- changes in Fly revision, pause, roster, toggles, live-armed, the v2c checkout, deploy runs and manual interventions;
- AI headline numbers;
- edge CANDIDATEs and HINTs;
- uptime.

It is AMBER while something broke, a RED is open or an edge candidate appeared. It clears (`AMBER_CLEAR`) when the next digest is quiet.

<a id="edge-candidate"></a>
## Edge candidates

`scripts/self_aware/edges_registry.json` holds the pre-registered screens. Each has a hash, and the registry has a registration time. Only screens in the registry can become a candidate.

**Status guards:**
- **CANDIDATE.** Holdout (last 22%, 1-hour embargo) N ≥ 300, at least 5 days and 40 hour clusters, BH q ≤ 0.10 and Holm p ≤ 0.10, positive train and holdout net bp, and a holdout second half ≥ 0.
- **HINT.** Holdout hit ≥ 55% with positive after-cost bp but not every guard passes.
- **Other statuses.** WATCH, REJECTED and INSUFFICIENT.

A candidate raises `selfaware.edge_candidate` (AMBER). **It never toggles a tile or creates a relay-capable tile.** Promotion needs Danish's approval plus the tile-lifecycle procedure in `TILE_LIFECYCLE.md`.

To change the screens, add a new registry version with a new `registered_at`. Never edit a spec in place after seeing results.

**Gated screens.** A spec with `requires: <question id>` is pre-registered but stays `QUEUED_DATA`, outside the BH/Holm family, until that research question is READY in `/api/selfaware/data/sufficiency`. Its `gate` restricts the screen to a regime:
- `tercile` gates on the 15-minute realized-vol tercile. The cut points are fixed on the training window only.
- `abs_min` and `abs_max` gate on the 60-minute trend z.

<a id="data-awareness"></a>
## Data awareness

The `data` job runs at start and every 30 minutes, taking about 15 s and 12 CPU-seconds. It walks the mirror tree (`fly-mirror-segments\tree`) and reads a bounded tail of each stream: 1 MB, or 8 MB for minute streams, capped at 3,000 rows. The Bitfinex tape is measured exactly by DuckDB over `raw_tape_1s`. It makes no Fly calls. View: `http://127.0.0.1:9021/data`.

| Endpoint | What it returns |
|---|---|
| `/api/selfaware/data` | Summary: mirror head, stale critical streams, watched-field alarms, tape and minute-stream fill, capacity and question status. |
| `/api/selfaware/data/catalog?stream=&catalogued=1` | Every collected stream (curated plus auto-discovered): what it is, venue, cadence, expected and observed rows per hour, Fly path (`/app/data/...`), laptop path, size, estimated rows, bytes per day, retention tier, schema, first and last timestamp, lag vs the mirror head, and status (FRESH, IDLE, STALE or MISSING). Pass `?stream=` to add the hourly buckets and gaps. |
| `/api/selfaware/data/completeness?stream=` | Expected vs actual rows per hour (last 48 hours), plus gaps. Each gap is attributed to a Fly interruption when they overlap. |
| `/api/selfaware/data/fields?stream=&status=&watched=1` | Field liveness: n, null %, distinct, zero %, constant value and status (OK, DEAD_NULL, DEAD_ZERO, CONSTANT or MISSING). |
| `/api/selfaware/data/capacity` | Laptop bot data vs the 50 GB cap (from the retention status), growth in GB/day and days to 90% of cap. Fly volume free and hours to full (parsed from the watcher's `disk.space`), ingest in GB/day, and Tier A datasets. |
| `/api/selfaware/data/sufficiency` | For each research question: screen status, full-answer status, blockers, short samples, ETA, and whether its gated screens are released. |

**Adding a stream or question.** Add a `StreamSpec` (with `watch` fields that must vary) or a `Question` in `scripts/self_aware/data_awareness.py`. Gated screens go in `edges_registry.json` with `requires`. Uncatalogued files still appear as auto-discovered streams, and their fields are profiled without raising alarms.

<a id="data-freshness"></a>
### data.freshness: critical streams are fresh

- **What it checks.** The critical streams are the tape, cross-venue, market context and AI calls. Each must be within 3 min, 10 min, 10 min and 6 h respectively of the mirror head. The head is the newest critical row, so the mirror's own lag (`inv.mirror_*_lag`) is not double-counted.
- **If it fires.** A collector on Fly stopped writing, or the stream was renamed. Check `/api/status.collection` in the next owner read, and the catalog row's `last_ts`.

<a id="data-completeness"></a>
### data.completeness: every time slot is filled

- **What it checks.** The tape must be at least 98% filled over 24 hours, excluding Fly interruptions. No single unexplained gap may reach 300 s. Minute streams must be at least 95% present over 24 hours.
- **If it fires.** Look at `largest_gaps` in the evidence. A gap with `explained_by` null that is not a deploy or restart points to a WS stall or Fly CPU saturation.

<a id="data-dead-fields"></a>
### data.dead_fields: watched fields are alive

- **What it checks.** Every watched field must vary and be populated. A watched field that is always null, always 0 (for a numeric measure) or constant across 50 or more rows raises AMBER.
- **First run (2026-10-02).** The first pass caught:
  - `ai_input_log.context.ret_1m/ret_5m/delta_change` = 0: the live prompt sends dead returns;
  - `adaptive_entry_decisions.ai_feature.win_prob` = 0;
  - counterfactual `epoch_id` and `opportunity_id` null, so the rows cannot be joined;
  - `market_context.regime.label` = WARMUP;
  - `pre_entry_features.captured_at_ts` null.
- **Fixing.** These are producer fixes on Fly, so they are post-freeze. The finding clears by itself when the field comes alive.

<a id="data-capacity"></a>
### data.capacity: room to keep collecting

- **Thresholds.** Laptop: AMBER under 7 days to 90% of the 50 GB cap, RED under 2. Fly volume: AMBER under 72 hours to full, RED under 24.
- **Growth basis.** Until 6 hours of history exist, laptop growth is the sum of the per-stream ingest rates excluding Tier B (reconstructible, pruned by retention). After that it is the measured net slope of retained bytes.

<a id="data-sufficiency"></a>
### data.sufficiency: research questions (informational)

Each question can be in one of three states:
- **READY.** Its fields are alive and there are enough independent samples (days, hour clusters, N). Its gated screens are released.
- **ACCUMULATING.** Fields are alive but samples are short; see `eta_ready`.
- **BLOCKED.** A required field is missing, dead or constant.

`full_question_status` tracks the inputs needed for a real-outcome answer, such as skipped-signal outcomes, ADX and maker fills. It never raises an alarm.
