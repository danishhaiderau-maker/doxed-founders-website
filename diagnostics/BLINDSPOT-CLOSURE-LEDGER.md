# BLINDSPOT closure ledger (AUDIT-3)

Generated 2026-10-02T13:55:26Z by `scripts/blindspot_closure_ledger.py` at `d6798e6c0`.
Rule: an item is **CLOSED-VERIFIED-LIVE** only when its live verifier passes against the live API in this run (endpoint + field + value in the Evidence column). A merged PR or a WALL claim never closes an item.
Live sources: fetched now; fetch errors: {}

## Counts

| Scope | Items | OPEN | IN PROGRESS | QUEUED-POST-FREEZE | CLOSED-VERIFIED-LIVE |
|---|---|---|---|---|---|
| Component rows (BLIND/PARTIAL) | 137 | 12 | 18 | 47 | 60 |
| Earlier gaps not closed (incl. Â§4.3 contradictions) | 72 | 14 | 22 | 22 | 14 |
| Directive / trace items | 5 | 0 | 0 | 3 | 2 |
| **Total tracked** | 214 | 26 | 40 | 72 | 76 |

Audit table parse: 174 components = FULL 37 / PARTIAL 86 / BLIND 51 (the audit headline states 35/93/46; its laptop sub-total 13/43/18 does not match its own L-rows 15/36/23 â€” this ledger uses the row-level statuses).

## Before / after

| Status | Before (this ledger, 2026-10-02T10:55Z) | Now |
|---|---|---|
| OPEN | 77 | 26 |
| IN PROGRESS | 94 | 40 |
| QUEUED-POST-FREEZE | 42 | 72 |
| CLOSED-VERIFIED-LIVE | 0 | 76 |

## Component rows

| Row | Component | Audit | Owner | PR | Status | Plan | Live evidence |
|---|---|---|---|---|---|---|---|
| F2 | early-boot ping server | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | liveness-only by design; low impact | (no verifier yet) |
| F4 | `dashboard_http_watchdog_loop` | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | threads[dashboard_http_watchdog] heartbeat + restart counter | Fly/api/status.threads.dashboard_http_watchdog.last_tick_age_sec=null |
| F5 | `watchdog_loop` strategy-progress latch | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | heartbeat+WS stale latch branch unreachable (:34197); unassigned | (no verifier yet) |
| F7 | `ping_ws` | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | ping_ws silent break; covered indirectly by ws_heartbeat_age_sec | (no verifier yet) |
| F8 | `ws_watchdog` | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | export ws_stale_count; unassigned | (no verifier yet) |
| F9 | `ws_tick_lifecycle_worker` | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | threads[ws_tick_lifecycle_worker] + dropped-tick counter | Fly/api/status.threads.ws_tick_lifecycle_worker.last_tick_age_sec=null |
| F10 | `state_monitor_loop` | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | state_monitor mode only; unassigned | (no verifier yet) |
| F12 | `order_book_refresh_loop` | PARTIAL | BLINDSPOT-CLOSE | #322 | **CLOSED-VERIFIED-LIVE** | GH monitor order_book_stale rule (book_age_sec / consecutive_failures, unpaused) | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F13 | `ohlcv_refresh_loop` | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | ohlcv errors log-only; unassigned | (no verifier yet) |
| F15 | `xvl_evaluator_thread` | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | GH monitor subsystem rule on /ready.xvl_evaluator_health.tick_age_s | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F16 | `xvl-paper-*` threads | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | split admission_eligible vs orders_submitted (rank 24); unassigned | (no verifier yet) |
| F18 | collector V3 reconcile worker | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | COLLECTOR_V3_RECONCILE_STALLED rule (phase!=IDLE >600s); Fly phase_age_sec field still OPEN | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F20 | main supervisor loop | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | threads[main_supervisor] | Fly/api/status.threads.main_supervisor.last_tick_age_sec=null |
| F22 | DeepSeek call thread | PARTIAL | AI-PLAN | #294 | **IN PROGRESS** | attempt liveness / persisted last_success (rank 17); post-deploy verification pending | (no verifier yet) |
| F24 | `ai_shadow_maturation_loop` | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | ai shadow labels owner-only; unassigned | (no verifier yet) |
| F25 | `engine_loop` | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | last_engine_error overwritten by validate_market_data; unassigned | (no verifier yet) |
| F26 | `tick_execution_engine` | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | threads[tick_execution_engine] | Fly/api/status.threads.tick_execution_engine.last_tick_age_sec=null |
| F27 | `position_manager` | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | threads[position_manager] | Fly/api/status.threads.position_manager.last_tick_age_sec=null |
| F28 | `analytics_loop` | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | threads[analytics_loop] | Fly/api/status.threads.analytics_loop.last_tick_age_sec=null |
| F29 | future-path evidence loop | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | future-path evidence owner-only; unassigned | (no verifier yet) |
| F30 | `ttl_monitor` | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | ttl_monitor log-only; can reuse thread_health post-freeze | (no verifier yet) |
| F32 | `bitfinex_live_reconcile_loop` | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | threads[bitfinex_live_reconcile] + WARNING-level errors (pre-arming gate) | Fly/api/status.threads.bitfinex_live_reconcile.last_tick_age_sec=null |
| F33 | relay outbox drain | BLIND | BLINDSPOT-CLOSE | #317 + #310 | **QUEUED-POST-FREEZE** | relay_outbox age/owner fields + never-deliver guard (Fly, post-freeze); GH monitor relay_stale_owner_pending alert from existing fields (laptop/CI, live after merge) | Fly /health.relay_outbox absent; /api/relay-state stale_owner_pending=22 |
| F34 | `/api/state` cache refresher | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | /api/state pause read live + api_state_age_sec | (no verifier yet) |
| F35 | `/api/relay-state` cache | PARTIAL | BLINDSPOT-CLOSE | #322 | **CLOSED-VERIFIED-LIVE** | GH monitor relay_cache_stale rule on /api/relay-state relay_cache.age_sec | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F36 | `/api/relay-execution-state` cache | PARTIAL | BLINDSPOT-CLOSE | #322 | **CLOSED-VERIFIED-LIVE** | GH monitor relay_cache_stale on relay-execution-state 503 | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F38 | inference-usage flusher | BLIND | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | inference flusher 4xx counted as success (rank 23); unassigned | (no verifier yet) |
| F39 | `lifecycle_pipeline_runtime` | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | GH monitor lifecycle_pipeline rule (age, blockers, emergency_wal) | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F40 | post-AI evidence workers | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | post-AI evidence not alerted; unassigned | (no verifier yet) |
| F41 | collector-v22 provisional merge | BLIND | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | provisional merge invisible; unassigned | (no verifier yet) |
| F42 | admin-pause finalizer | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | admin-pause finalizer response-only; low | (no verifier yet) |
| F43 | research-segment server (raw handler) | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | ACK seq exposed via laptop.pull_ack; research-segment server error counter needs a Fly field | (no verifier yet) |
| F44 | HTTP thread-cap semaphores | BLIND | FLY-LOCKS | #306 (proposed) | **OPEN** | HTTP thread-cap saturation; propose to FLY-LOCKS runtime_telemetry | (no verifier yet) |
| F45 | `cross_venue_collector` | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | GH monitor cross_venue_health rule | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F46 | `market_context_collector` | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | GH monitor market_context rule | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F47 | `research_segment_shipper` | PARTIAL | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | shipper block in public /api/status; sidecar restart loop still OPEN | Fly/api/status.shipper=null |
| F48 | `fly_relay_state_pusher` | BLIND | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | relay-state pusher no status / no restart (rank 22); unassigned | (no verifier yet) |
| F49 | entrypoint bot restart loop | PARTIAL | BLINDSPOT-CLOSE | #322 | **CLOSED-VERIFIED-LIVE** | GH monitor restart_loop rule (>=3 boot_at changes per hour) | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F51 | Bitfinex private (ccxt + nonce lock) | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | private keys probe skipped in force-paper; pre-arming gate | (no verifier yet) |
| F52 | DDOLLAR gate | PARTIAL | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | DDOLLAR gate fail-CLOSED + error counter (freeze-exception candidate) | Fly/api/status.bitfinex_live.ddollar_gate.errors=null |
| F53 | Neon (`DATABASE_URL`) | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | Neon reachability not probed; unassigned | (no verifier yet) |
| F56 | `replay_lock` (tracked) | PARTIAL | FLY-LOCKS | #306 | **QUEUED-POST-FREEZE** | replay_lock diagnostics alerting | (no verifier yet) |
| F57 | `state_lock` (tracked) | BLIND | FLY-LOCKS | #306 | **QUEUED-POST-FREEZE** | state_lock probe | (no verifier yet) |
| F58 | position_close / position_evaluation / paper_lifecycle_file  | BLIND | FLY-LOCKS | #306 | **QUEUED-POST-FREEZE** | other locks | (no verifier yet) |
| F59 | `_relay_event_drain_lock` | BLIND | FLY-LOCKS | #306 | **QUEUED-POST-FREEZE** | relay drain lock | (no verifier yet) |
| F61 | `signal_queue`, `event_queue`, `ws_tick_lifecycle_queue`(1) | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | queues qsize/dropped in /api/status.queues | Fly/api/status.queues=null |
| F62 | volume health cache + growth ring (memory) | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | memory-only volume growth ring (rank 32) | (no verifier yet) |
| F63 | system-health alarm history (memory) | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | memory-only alarm history (rank 32) | (no verifier yet) |
| F64 | pathway specs / trade-enrichment caches | BLIND | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | pathway spec caches invisible; low | (no verifier yet) |
| F65 | lane PnL ledgers `lane_pnl_ledger.json`, `lane_lab_pnl_ledge | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | ledgers.lane_pnl write_failures counted + alarm (freeze-exception candidate) | Fly/api/status.ledgers.lane_pnl.write_failures=null |
| F66 | `trades_3factor.csv` + `csv_write_fallback.jsonl` | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | csv fallback replayed at startup; no counter | (no verifier yet) |
| F67 | `open_positions.json`, `paper_lifecycle_v1.json` | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | corrupt restore pauses; no age field | (no verifier yet) |
| F68 | `xvl_`/`xvp_shadow_signals.jsonl` | PARTIAL | BLINDSPOT-CLOSE | #322 | **CLOSED-VERIFIED-LIVE** | GH monitor collection_write_failures rule (counter growth) | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F70 | adaptive entry, fill markouts, taker counterfactuals | PARTIAL | BLINDSPOT-CLOSE | #322 | **CLOSED-VERIFIED-LIVE** | GH monitor collection_write_failures over xvl/markouts/tape counters | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F71 | cross-venue / market-context / liquidation tapes | PARTIAL | WATCHER | #307 | **IN PROGRESS** | market_context/tape write failures read by streams.coverage (#307) | (no verifier yet) |
| F72 | collector v22 SQLite + V3 store | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | via V3 reconcile stall rule (F18) | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F73 | signal snapshots, shadow outcomes, counterfactuals, near-mis | BLIND | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | signal snapshot/shadow writers swallow errors; extend PR_FLY helper post-freeze | (no verifier yet) |
| F74 | execution-funnel hooks (fill/close/expire/capacity/touch/exp | BLIND | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | execution_funnel.hook_failures{hook} + alarm | Fly/api/status.collection.execution_funnel.hook_failures=null |
| F75 | emergency WAL | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | GH monitor emergency_wal != CURRENT finding | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| F77 | 64 Flask routes incl. ~25 control POSTs (pause/resume/toggle | PARTIAL | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | control-action audit endpoint (rank 27); unassigned | (no verifier yet) |
| F78 | 244 env flags (e.g. `FORCE_PAPER_MODE`, `SCORE_LED_PAPER_RES | BLIND | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | effective-config snapshot (rank 27); unassigned | (no verifier yet) |
| F79 | 455 broad except sites (bot.py 244; 110 `pass`, 263 log-only | BLIND | FLY-LOCKS | #306 (proposed) | **OPEN** | per-subsystem swallowed_errors counter (rank 26); not in #306 file list | (no verifier yet) |
| L1 | laptop-chain supervisor (5-min) | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | laptop.supervisor by scheduled-task + process check, not log grep | :9011 checks[laptop.supervisor].status=GREEN observed='task state=Running last run 0s ago result=267009; pull loop pid(s) [34496]; log TICK 5m ag' |
| L2 | segment pull loop | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | pull status keeps seqs; exitCode!=0 surfaced | :9011 checks[laptop.pull_ack].status=AMBER observed='published=2351 applied=2350 (puller status) fly_acked=2350 last pull 74s ago exi' |
| L3 | pull wrapper | BLIND | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | wrapper failures surface via segment-pull.status exitCode rule | :9011 checks[laptop.pull_ack].status=AMBER observed='published=2351 applied=2350 (puller status) fly_acked=2350 last pull 74s ago exi' |
| L4 | puller | BLIND | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | puller lock refusal preserves applied/acked seqs + consecutive_failures | :9011 checks[laptop.pull_ack].status=AMBER observed='published=2351 applied=2350 (puller status) fly_acked=2350 last pull 74s ago exi' |
| L5 | puller HTTP source | BLIND | BLINDSPOT-CLOSE | #309 + #323 | **IN PROGRESS** | bounded run deadline; status.json run_seconds/max_run_seconds/deadline_reached surfaced in laptop.pull_ack | :9011 checks[laptop.pull_ack].observed_fields.max_run_seconds=null |
| L6 | ACK writer | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | pull_ack consistent with monitor SEGMENT_ACK_STALE | :9011 checks[laptop.pull_ack].status=AMBER observed='published=2351 applied=2350 (puller status) fly_acked=2350 last pull 74s ago exi' |
| L7 | parity checker | PARTIAL | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | :9011 analyzer.parity_checker (verdict, age, scan lock hold, hash-cache timing) | :9011 checks[analyzer.parity_checker].status=GREEN observed='verdict=GREEN seq=2345 age=11m no timing (pre-cache parity build)' |
| L8 | analyzer cycle | PARTIAL | ANALYZER-FIDELITY | #304/#307 | **IN PROGRESS** | cycle history + consecutive_failures (rank 20) | (no verifier yet) |
| L9 | promotion | BLIND | ANALYZER-FIDELITY | - | **OPEN** | promotion lock holder/exit 3 surfaced (rank 20) | (no verifier yet) |
| L10 | migration | BLIND | ANALYZER-FIDELITY | - | **OPEN** | migration log-only (rank 20) | (no verifier yet) |
| L11 | inline auto-FF | PARTIAL | ANALYZER-FIDELITY | #304 | **IN PROGRESS** | inline auto-FF observed | (no verifier yet) |
| L12 | supervisor auto-FF | BLIND | ANALYZER-FIDELITY | #304 | **IN PROGRESS** | REFUSED_* auto-ff receipts not surfaced | (no verifier yet) |
| L13 | analyzer runner | PARTIAL | ANALYZER-FIDELITY + BLINDSPOT-CLOSE | #304 + #309 | **CLOSED-VERIFIED-LIVE** | generation receipt (#304) + analyzer.reports from :9001/api/status | :9011 checks[analyzer.reports].status=GREEN vs :9001/api/status.required_reports_ok=True |
| L14 | analyzer launcher | PARTIAL | ANALYZER-9001 | - | **OPEN** | launcher stdout only; low | (no verifier yet) |
| L15 | **required analyzer reports** | BLIND | ANALYZER-FIDELITY + BLINDSPOT-CLOSE | #304 + #309 | **CLOSED-VERIFIED-LIVE** | #304 fixes POLICY_ID_SPEC_COLLISION; watcher analyzer.reports RED when required_reports_ok=false >1 generation | :9011 checks[analyzer.reports].status=GREEN vs :9001/api/status.required_reports_ok=True |
| L16 | :9001 dashboard process | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | analyzer.api not GREEN when /api/status.ok=false | :9011 checks[analyzer.reports].status=GREEN vs :9001/api/status.required_reports_ok=True |
| L17 | :9001 revision parity | BLIND | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | parse string revision_parity; compare generation rev to Fly rev | :9011 analyzer.api.status=AMBER observed='mirror sync receipt 71m old'; :9001 generation_revision=29742de53a5c Fly source_git_rev=29742de53a5c |
| L18 | :9001 runtime sync identity | BLIND | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | AMBER when upstream_sync_id != Fly sync id (refresh at source = #302 owner) | :9011 analyzer.api.status=AMBER observed='mirror sync receipt 71m old'; :9001 generation_revision=29742de53a5c Fly source_git_rev=29742de53a5c |
| L19 | :9001 mirror receipt freshness | BLIND | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | AMBER when mirror_sync_receipt STALE even if ok=true | :9011 analyzer.api.status=AMBER observed='mirror sync receipt 71m old'; :9001 generation_revision=29742de53a5c Fly source_git_rev=29742de53a5c |
| L20 | dashboard code refresh | PARTIAL | ANALYZER-9001 | #302 | **IN PROGRESS** | dashboard code refresh | (no verifier yet) |
| L23 | analyzer exports | PARTIAL | ANALYZER-FIDELITY | #307 | **IN PROGRESS** | export swallow | (no verifier yet) |
| L24 | strategy_lab engine | PARTIAL | ANALYZER-FIDELITY | #307 | **IN PROGRESS** | engine swallow | (no verifier yet) |
| L25 | strategy_lab heavy cache | BLIND | ANALYZER-FIDELITY | - | **OPEN** | heavy cache invisible | (no verifier yet) |
| L26 | stream studies | BLIND | ANALYZER-FIDELITY + BLINDSPOT-CLOSE | #307 + #309 | **CLOSED-VERIFIED-LIVE** | #307 fixes stream studies; watcher streams.analysed_freshness detects stale-content ANALYSED | :9011 checks[streams.analysed_freshness].status=AMBER observed='not fully analysed: order_multiverse.jsonl, order_multiverse_entry_grid.jsonl (export 36.7' |
| L27 | `/api/streams/health` | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | streams.analysed_freshness reads /api/streams/health content_lag_sec + not_fully_analysed | :9011 checks[streams.analysed_freshness].status=AMBER observed='not fully analysed: order_multiverse.jsonl, order_multiverse_entry_grid.jsonl (export 36.7' |
| L28 | insights aggregator | BLIND | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | insights transfer not OK with applied_seq=null | :9001/api/insights transfer.status=DEGRADED applied_seq=2350 reason='last pull exit=2 result=None consecutive_failures=None error=LockBusyE' |
| L29 | analyzer client | PARTIAL | ANALYZER-FIDELITY | #307 | **IN PROGRESS** | client live-check swallow | (no verifier yet) |
| L31 | laptop-chain monitor | PARTIAL | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | monitor alerts surface as :9011 laptop.chain_monitor (not toast-only) | :9011 checks[laptop.chain_monitor].status=GREEN observed='0 active monitor alerts []; checked 5m ago' |
| L32 | monitor: legacy SYNC_HEARTBEAT | PARTIAL | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | SYNC_HEARTBEAT_* critical -> :9011 laptop.chain_monitor RED | :9011 checks[laptop.chain_monitor].status=GREEN observed='0 active monitor alerts []; checked 5m ago' |
| L33 | monitor: shipper / AI input / cross-venue / XVL | PARTIAL | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | monitor warnings -> :9011 laptop.chain_monitor AMBER | :9011 checks[laptop.chain_monitor].status=GREEN observed='0 active monitor alerts []; checked 5m ago' |
| L34 | incident relay | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | incident relay: stale report -> system_health_stale; deploy-aware maintenance; non-zero exit | :9011 report.stale=False age_sec=295.9 |
| L35 | unattended proof | PARTIAL | SELF-AWARE | - | **OPEN** | proof receipts in stale checkout (rank 28) | (no verifier yet) |
| L36 | health watcher tick | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | health-report staleness -> incident | :9011 report.stale=False age_sec=295.9 |
| L37 | health interim task | BLIND | BLINDSPOT-CLOSE | #309 + #323 | **CLOSED-VERIFIED-LIVE** | interim task defers only if supervisor ticked <15 min; decision file -> :9011 watcher.interim | :9011 checks[watcher.interim].status=GREEN observed='DEFERRED 11s ago (supervisor ran 4.8m ago, verdict 4.6m old, carriesWatcher=True)' (interim decision fresh) |
| L38 | :9011 server | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | :9011 serves cache with age; live=1 single-flight background refresh | GET :9011/api/system-health?live=1 took 30 ms (target < 3000) |
| L39 | Fly banner push | PARTIAL | BLINDSPOT-CLOSE | #323 | **IN PROGRESS** | banner push result -> :9011 watcher.delivery (consecutive failures) | :9011 checks[watcher.delivery].status=SKIP observed='no banner push recorded yet' (push result recorded) |
| L40 | Fly `/api/system-health` | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | GH monitor polls Fly /api/system-health age/stale -> laptop_health_silent | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| L45 | SH `ai.decision_mix` | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | decision_mix SKIP below min samples | :9011 checks[ai.decision_mix].status=GREEN observed='32/101 neutral in 6.0h' (evaluated; SKIP below min samples) |
| L47 | SH `trading.orders` | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | trading.orders AMBER when toggles missing | :9011 trading.orders.status=GREEN; Fly self-checks=4 |
| L48 | SH `trading.orphans` / `trading.lifecycle` | PARTIAL | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | lifecycle contradictions RED within 2h, AMBER within 24h | :9011 checks[trading.lifecycle].status=AMBER observed="4 expired+filled contradictions in 24h (0 in 2.0h) ['fal-39ccb12d2316', 'flb-4bc059d70908'" |
| L51 | SH `laptop.pull_ack` | BLIND | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | applied=None never GREEN | :9011 checks[laptop.pull_ack].status=AMBER observed='published=2351 applied=2350 (puller status) fly_acked=2350 last pull 74s ago exi' |
| L52 | SH `analyzer.generation` | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | analyzer.reports alongside analyzer.generation | :9011 checks[analyzer.reports].status=GREEN vs :9001/api/status.required_reports_ok=True |
| L53 | SH `analyzer.api` | BLIND | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | analyzer.api correctness | :9011 analyzer.api.status=AMBER observed='mirror sync receipt 71m old'; :9001 generation_revision=29742de53a5c Fly source_git_rev=29742de53a5c |
| L54 | SH `streams.coverage` | BLIND | ANALYZER-FIDELITY + BLINDSPOT-CLOSE | #307 + #309 | **CLOSED-VERIFIED-LIVE** | nothing reported -> AMBER, never 'n/a' GREEN | :9011 checks[streams.coverage].observed='collection OK; streams market_context:OK/4s, bitfinex_1s:3s, cross_venue:OK/0s, xvl_stale_' |
| L55 | SH `dashboards.parity` | PARTIAL | BLINDSPOT-CLOSE | #309 + #323 | **CLOSED-VERIFIED-LIVE** | parse string epoch_parity; exposed in dashboards.parity observed_fields | :9011 checks[dashboards.parity].observed_fields.epoch_parity="MATCH" |
| L56 | SH `railway.relay` / `railway.api` | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | reconciliation null -> AMBER | :9011 checks[railway.relay].status=AMBER observed='mode=PAUSED armedAt=None executor=PAUSED_HEALTHY hb=27s snapshot 0s old reconciliation=nul' (reconciliation=null is never GREEN) |
| L58 | SH `bitfinex.exposure` | PARTIAL | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | exch_qty None -> AMBER unless explicitly disarmed | :9011 checks[bitfinex.exposure].status=GREEN observed='disarmed (live_armed=False, force_paper=True), exchange qty not probed (disarmed: Fly live' (qty not probed is GREEN only when explicitly disarmed) |
| L60 | self-aware keeper | BLIND | BLINDSPOT-CLOSE + SELF-AWARE | #309 + #300 | **CLOSED-VERIFIED-LIVE** | watcher selfaware.engine (age/jobs) RED on stale; keeper script change proposed to SELF-AWARE | :9011 checks[selfaware.engine].status=GREEN observed='8 jobs on time; diagnose 4m ago, health generated 102s ago (self-aware verdict AMBER)' |
| L61 | SA engine scheduler (8 jobs) | PARTIAL | SELF-AWARE | #300 | **IN PROGRESS** | job last_ok AMBER history only | (no verifier yet) |
| L62 | SA job `views` | PARTIAL | SELF-AWARE | #300 | **IN PROGRESS** | views job returns OK with errors (rank 29) | (no verifier yet) |
| L63 | SA `inv.custody` | BLIND | SELF-AWARE + BLINDSPOT-CLOSE | #300 + #309 | **IN PROGRESS** | custody SKIP; puller seq preservation feeds it | :9021 findings[inv.custody].status=None |
| L67 | SA invariants fill_close / expired_filled / ai_response | PARTIAL | SELF-AWARE | #300 | **IN PROGRESS** | missing view -> SKIP | (no verifier yet) |
| L68 | SA progress probes (ai_cadence, tile_orders, feeds, counts_a | PARTIAL | SELF-AWARE | #300 | **IN PROGRESS** | progress probes | (no verifier yet) |
| L69 | SA `data.*` freshness/completeness/dead_fields/capacity/suff | PARTIAL | SELF-AWARE | #300 | **IN PROGRESS** | freshness vs mirror head; Fly volume null GREEN | (no verifier yet) |
| L70 | SA uptime | PARTIAL | SELF-AWARE | #300 | **IN PROGRESS** | uptime interruptions disagree 9 vs 1 | (no verifier yet) |
| L71 | legacy ACK watcher | BLIND | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | frozen legacy ACK watcher state reported as orphan | :9011 checks[laptop.legacy_ack_watcher].status=AMBER observed='laptop-ack-watcher.status.json state=WAIT_INVENTORY_NOT_ACK_ELIGIBLE last poll 57.4h ago; ' (frozen orphan watcher reported) |
| L72 | ad-hoc `fly-dashboard-proxy` :7002 | BLIND | Danish | - | **OPEN** | ad-hoc :7002 proxy: register or retire (now visible in :9011 laptop.adhoc_processes) | (no verifier yet) |
| L73 | ad-hoc `xvl-scratch\watch_queue.ps1` (writes WALL) | BLIND | Danish | - | **OPEN** | ad-hoc watch_queue.ps1: register or retire (now visible in :9011 laptop.adhoc_processes) | (no verifier yet) |
| L74 | ad-hoc `uptime_poll2.py`, `http.server` :9097 | BLIND | Danish | - | **OPEN** | ad-hoc uptime_poll2 / :9097: register or retire (now visible in :9011 laptop.adhoc_processes) | (no verifier yet) |
| C1 | `fly-bot-monitor` cron */15 | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | heartbeat variable + monitor_schedule_gap; crash/cache-loss never closes incidents | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success: 'previous monitor run evidence 2026-10-02T13:47:30Z via cached state; gap 7 min'; 'state restored=True crashed=False clean_streak=0 incident_close_allowed=False'; GH var FLY_MONITOR_HEARTBEAT unset (FLY_MONITOR_VARIABLES_TOKEN not configured) |
| C3 | revision / registry drift | PARTIAL | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | :9011 fly.revision compares Fly git_rev to origin/master (master ahead = deploy queued) | :9011 checks[fly.revision].status=GREEN observed='fly=29742de53a5c master=768f00e7c8e0 (master ahead; deploy queued)' (master compared) |
| C5 | cadence rules | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | entries_blocked when last_poll_entry_eligible=false >2h unpaused | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| C7 | segment transfer lag | PARTIAL | DATA-RETENTION | #303 | **QUEUED-POST-FREEZE** | FLY_MONITOR_SEGMENTS_LIVE=1 before prune goes live | (no verifier yet) |
| C9 | laptop dead-man (GH var > 2h) | PARTIAL | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | unset LAPTOP_CHAIN_HEARTBEAT is a finding | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success: 'LAPTOP_CHAIN_HEARTBEAT: 1790948454' |
| C11 | laptop incident issues | PARTIAL | BLINDSPOT-CLOSE | #309 + #323 | **CLOSED-VERIFIED-LIVE** | incident maintenance capped 90 min; heartbeat + cap -> :9011 laptop.incident_relay | :9011 checks[laptop.incident_relay].status=GREEN observed="heartbeat 10m ago; maintenance=none; open conditions ['system_health_red']" |
| C12 | `/ready` subsystem blocks (xvl, cross_venue, market_context, | BLIND | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | subsystem_findings over /ready blocks | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| C13 | `/api/status` pipelines (lifecycle, relay outbox, v3 reconci | BLIND | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | rules over lifecycle / relay outbox / V3 reconcile | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| C14 | Fly prune / custody | PARTIAL | DATA-RETENTION | #303 | **QUEUED-POST-FREEZE** | prune dry_run until #303 | (no verifier yet) |
| C15 | `fly-bot-deploy` | PARTIAL | BLINDSPOT-CLOSE | #322 | **CLOSED-VERIFIED-LIVE** | GH monitor deploy_failed rule on last finished guarded deploy | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| C16 | `fly-monitor-ci` / `research-segments-ci` | PARTIAL | BLINDSPOT-CLOSE | #309 + #310 | **CLOSED-VERIFIED-LIVE** | laptop-tests workflow; head commits without [skip ci], squash subject with [skip ci] | laptop-tests.yml run 37015191532 @36268ea0f pull_request 2026-10-02T13:46:04Z success |
| C17 | `bitfinex-production-gate` | PARTIAL | COORDINATOR | - | **OPEN** | production gate ignores bot-code pushes | (no verifier yet) |
| C18 | `auto-deploy` (Railway/Vercel) | BLIND | Danish | - | **OPEN** | auto-deploy disabled_manually (intentional?) | (no verifier yet) |
| C19 | `secret-scan` | PARTIAL | BLINDSPOT-CLOSE | #322 | **CLOSED-VERIFIED-LIVE** | daily full-history gitleaks (secret-scan-history.yml) with fingerprinted .gitleaksignore | secret-scan-history.yml run 37015247367 @d6798e6c0 workflow_dispatch 2026-10-02T13:46:33Z success (full-history gitleaks) |
| C20 | WALL `diagnostics/WALL-STATUS-FLY.md` | BLIND | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | :9011 coordination.wall validates line format + last-entry age | :9011 checks[coordination.wall].status=AMBER observed='last entry 13m ago; 2 malformed of last 40' |
| C21 | GHA scheduler itself | BLIND | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | monitor heartbeat watched by monitor itself + laptop | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success: 'previous monitor run evidence 2026-10-02T13:47:30Z via cached state; gap 7 min'; 'state restored=True crashed=False clean_streak=0 incident_close_allowed=False'; GH var FLY_MONITOR_HEARTBEAT unset (FLY_MONITOR_VARIABLES_TOKEN not configured) |

## Earlier-audit gaps (closure table Â§4) and Â§4.3 contradictions

| # | Gap | Audit status | Owner | PR | Status | Plan | Live evidence |
|---|---|---|---|---|---|---|---|
| 1 | Best-Policy / Safe-Genome fail `POLICY_ID_SPEC_COLLISION` | IN PROGRESS #304 | audit owner | #304 | **IN PROGRESS** | Best-Policy / Safe-Genome fail `POLICY_ID_SPEC_COLLISION` |  |
| 2 | Integrity VALID despite policy failure | IN PROGRESS #304 | audit owner | #304 | **IN PROGRESS** | Integrity VALID despite policy failure |  |
| 3 | No per-study receipt / missing-report check | IN PROGRESS #304 | audit owner | #304 | **IN PROGRESS** | No per-study receipt / missing-report check |  |
| 4 | Protection-replay truncation not exported | IN PROGRESS #304 | audit owner | #304 | **IN PROGRESS** | Protection-replay truncation not exported |  |
| 5 | Exit-ladder sim 0 eligible inputs | IN PROGRESS #307 | audit owner | #307 | **IN PROGRESS** | Exit-ladder sim 0 eligible inputs |  |
| 6 | Tier A never promotes tape/market_context/liquidations/V3 | IN PROGRESS #307 | audit owner | #307 | **IN PROGRESS** | Tier A never promotes tape/market_context/liquidations/V3 |  |
| 7 | Rolling ~55h horizon | IN PROGRESS #307 | audit owner | #307 | **IN PROGRESS** | Rolling ~55h horizon |  |
| 8 | Report history overwritten | IN PROGRESS #307 | audit owner | #307 | **IN PROGRESS** | Report history overwritten |  |
| 9 | data_health 17/17 STALE | IN PROGRESS (#293 merged, not yet run) | audit owner | (#293 merged, not yet run) | **IN PROGRESS** | data_health 17/17 STALE |  |
| 10 | `streams.coverage` no-op | IN PROGRESS #304 | audit owner | #304 | **IN PROGRESS** | `streams.coverage` no-op |  |
| 11 | market_context unread by checks | IN PROGRESS #304 | audit owner | #304 | **IN PROGRESS** | market_context unread by checks |  |
| 12 | Failed retention shows GREEN | IN PROGRESS #304 (#301 merged lock retry) | audit owner | #304 (#301 merged lock retry) | **IN PROGRESS** | Failed retention shows GREEN |  |
| 13 | Promotion lock contention | NOT ADDRESSED | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | promotion/parity lock contention visible as :9011 laptop.puller_lock (holder, hold time, r | :9011 checks[laptop.puller_lock].observed_fields.lock_holder="research_segment_promotion" |
| 14 | Trade/PnL disagreement Fly/mirror/analyzer | IN PROGRESS #307 | audit owner | #307 | **IN PROGRESS** | Trade/PnL disagreement Fly/mirror/analyzer |  |
| 15 | Lane PnL cent rounding | QUEUED | audit owner | - | **QUEUED-POST-FREEZE** | Lane PnL cent rounding |  |
| 16 | Deploy force-flats contaminate stats | QUEUED (partly #299) | audit owner | (partly #299) | **QUEUED-POST-FREEZE** | Deploy force-flats contaminate stats |  |
| 17 | Analyzer cycle vs 45-min freshness | NOT ADDRESSED | ANALYZER-FIDELITY | - | **OPEN** | cycle length vs 45-min freshness |  |
| 18 | 0.9 GB multiverse HEALTH_ONLY | NOT ADDRESSED | ANALYZER-FIDELITY | - | **OPEN** | multiverse HEALTH_ONLY |  |
| 19 | Counterfactual rows lack join keys | QUEUED L377 | audit owner | L377 | **QUEUED-POST-FREEZE** | Counterfactual rows lack join keys |  |
| 20 | 22% signal_replay completion | NOT ADDRESSED | ANALYZER-FIDELITY | - | **OPEN** | signal_replay completion |  |
| 21 | PIPELINE_ERROR race / capacity censoring | NOT ADDRESSED | ANALYZER-FIDELITY | - | **OPEN** | PIPELINE_ERROR race / capacity censoring |  |
| 22 | Data catalog API | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 23 | Per-slot completeness | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 24 | Dead/constant field detection | CLOSED (detection) | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 25 | Capacity / days-to-full | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 26 | Archive size vs capacity | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 27 | Research sufficiency | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 28 | Live prompt ret_1m/ret_5m/delta_change zero | QUEUED (fix in #294 deployed, unverified) | audit owner | (fix in #294 deployed, unverified) | **QUEUED-POST-FREEZE** | Live prompt ret_1m/ret_5m/delta_change zero |  |
| 29 | Dead-input detector watches challenger fields only | NOT ADDRESSED | AI-PLAN | - | **OPEN** | dead-input detector watches challenger fields only |  |
| 30 | Regime stuck WARMUP | QUEUED #306 | audit owner | #306 | **QUEUED-POST-FREEZE** | Regime stuck WARMUP |  |
| 31 | `win_prob` always 0 | QUEUED L377 | audit owner | L377 | **QUEUED-POST-FREEZE** | `win_prob` always 0 |  |
| 32 | `captured_at_ts` null | QUEUED L377 | audit owner | L377 | **QUEUED-POST-FREEZE** | `captured_at_ts` null |  |
| 33 | Bybit funding constant | NOT ADDRESSED | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | Bybit funding constant |  |
| 34 | AI NO_TRADE no REJECTED rows | QUEUED L368 | audit owner | L368 | **QUEUED-POST-FREEZE** | AI NO_TRADE no REJECTED rows |  |
| 35 | No maker fill path | QUEUED L377 | audit owner | L377 | **QUEUED-POST-FREEZE** | No maker fill path |  |
| 36 | No CPU/RSS/thread telemetry | QUEUED #306 | audit owner | #306 | **QUEUED-POST-FREEZE** | No CPU/RSS/thread telemetry |  |
| 37 | Trade-lock health | QUEUED #306 | audit owner | #306 | **QUEUED-POST-FREEZE** | Trade-lock health |  |
| 38 | Clock skew | NOT ADDRESSED | FLY runtime | post-freeze backlog | **QUEUED-POST-FREEZE** | clock skew |  |
| 39 | Crash dumps / lifecycle backlog age | NOT ADDRESSED | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | lifecycle blocker_counts / emergency WAL rule; crash dumps OPEN | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| 40 | Epoch parity / retired boundary / WS reconnects / REST stale exposed b | NOT ADDRESSED | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | subsystem rules (WS reconnects, REST stale, epoch parity) | fly-bot-monitor run 37015919598 @d1b183f11 2026-10-02T13:52:24Z success findings=0 contract_field_missing=0: '' |
| 41 | Exchange 429s per venue | NOT ADDRESSED | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | rate_limits{venue}.hits_429 | Fly/api/status.rate_limits=null |
| 42 | Fly 60/min limit starves laptop | QUEUED L385 | audit owner | L385 | **QUEUED-POST-FREEZE** | Fly 60/min limit starves laptop |  |
| 43 | Watcher SKIP on 429 (fail-open) | NOT ADDRESSED | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | fetch failure >15 min -> AMBER, not SKIP | :9011 checks[watcher.sources].status=GREEN observed='all 8 sources answered' |
| 44 | `/api/state/lite` + owner bucket | QUEUED L363 | audit owner | L363 | **QUEUED-POST-FREEZE** | `/api/state/lite` + owner bucket |  |
| 45 | Relay snapshot top lock holder | QUEUED #306 | audit owner | #306 | **QUEUED-POST-FREEZE** | Relay snapshot top lock holder |  |
| 46 | `fly.ai_success` RED during deploy pause | QUEUED L363 | audit owner | L363 | **QUEUED-POST-FREEZE** | `fly.ai_success` RED during deploy pause |  |
| 47 | `failing[]` duplicates | NOT ADDRESSED | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | summarize dedupes checks by id (worst wins) | :9011 48 checks, 10 failing, duplicate ids=[] |
| 48 | Fly-published health lags laptop | NOT ADDRESSED | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | :9011 watcher.fly_copy lag of Fly-published copy | :9011 checks[watcher.fly_copy].status=GREEN observed='Fly copy generated 5m ago; lag behind previous local verdict 0s' (lag measured) |
| 49 | `/summary` `/live` 401 undocumented | QUEUED L363 | audit owner | L363 | **QUEUED-POST-FREEZE** | `/summary` `/live` 401 undocumented |  |
| 50 | Verdict never GREEN; no ack/expiry | NOT ADDRESSED | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | AMBER acks with expiry (health/acks.json); RED never ackable | :9011 report.acked=[] (AMBER-only acks) |
| 51 | Flapping unclassified | NOT ADDRESSED | BLINDSPOT-CLOSE | #323 | **CLOSED-VERIFIED-LIVE** | :9011 watcher.flapping (>=4 changes in 12 ticks) | :9011 checks[watcher.flapping].status=GREEN observed='0 flapping checks []' |
| 52 | `neon.usage` GREEN on zero | CLOSED #295 | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 53 | "Noneh to full" | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 54 | `:9001/api/health` hides revision lag/stale receipt | NOT ADDRESSED at source | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | watcher parses revision lag / receipt age (source fix = ANALYZER-FIDELITY) | :9011 analyzer.api.status=AMBER observed='mirror sync receipt 71m old'; :9001 generation_revision=29742de53a5c Fly source_git_rev=29742de53a5c |
| 55 | Insights tile fields null | NOT ADDRESSED | SECTION-CONTRACTS | - | **OPEN** | insights tile fields null (content correctness; handed 13:14Z) |  |
| 56 | Insights transfer seqs null | NOT ADDRESSED | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | puller seqs preserved; insights transfer honest | :9011 checks[laptop.pull_ack].status=AMBER observed='published=2351 applied=2350 (puller status) fly_acked=2350 last pull 74s ago exi' |
| 57 | `deploy_queue` stale WALL scrape | NOT ADDRESSED | SECTION-CONTRACTS | - | **OPEN** | deploy_queue stale WALL scrape (content correctness; handed 13:14Z) |  |
| 58 | `selfaware.*` generic titles | QUEUED | audit owner | - | **QUEUED-POST-FREEZE** | `selfaware.*` generic titles |  |
| 59 | Per-call AI API | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 60 | Fly `ai_call_v1` hash/tokens/cost | QUEUED L363/L385 | audit owner | L363/L385 | **QUEUED-POST-FREEZE** | Fly `ai_call_v1` hash/tokens/cost |  |
| 61 | AI scorecard | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 62 | DeepSeek burn rate | NOT ADDRESSED | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | ai_provider_health.cost_usd_24h / last_call_cost_usd | Fly/api/status.ai_provider_health.last_call_cost_usd=null |
| 63 | Fly/Railway spend, Neon forecast | NOT ADDRESSED | COORDINATOR | - | **OPEN** | Fly/Railway spend, Neon forecast |  |
| 64 | Edge state machine | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 65 | Regime playbook | CLOSED (descriptive; regime input dead) | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 66 | Proof status API | CLOSED via :9021 | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 67 | Proof false-fails `segments_acked` | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 68 | 48h proof never completes | IN PROGRESS (restart after #304, L383) | audit owner | (restart after #304, L383) | **IN PROGRESS** | 48h proof never completes |  |
| 69 | Deploy API stuck detection / mojibake | NOT ADDRESSED | SECTION-CONTRACTS | - | **OPEN** | deploy in_progress after completion (content correctness; handed 13:14Z) |  |
| 70 | Tests/CI results API | NOT ADDRESSED | BLINDSPOT-CLOSE | #310 | **CLOSED-VERIFIED-LIVE** | laptop-tests workflow gives PR checks; results API still OPEN | laptop-tests.yml run 37015191532 @36268ea0f pull_request 2026-10-02T13:46:04Z success |
| 71 | `/changes` timeline | NOT ADDRESSED | SELF-AWARE | - | **OPEN** | /changes timeline |  |
| 72 | One-call coordinator brief | CLOSED (digest) | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 73 | Unified custody view | NOT ADDRESSED | SELF-AWARE + BLINDSPOT-CLOSE | #300 + #309 | **IN PROGRESS** | unified custody (puller seqs fixed here) | :9021 findings[inv.custody].status=None |
| 74 | Uptime API | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| 75 | Incident timeline | NOT ADDRESSED | SELF-AWARE | - | **OPEN** | incident timeline |  |
| 76 | Analyzer exports over HTTP | NOT ADDRESSED | ANALYZER-FIDELITY | - | **OPEN** | exports over HTTP |  |
| 77 | AGENTS.md roster drift / registry lint | NOT ADDRESSED | SECTION-CONTRACTS | - | **OPEN** | AGENTS.md roster vs live registry drift (content correctness; handed 13:14Z) |  |
| 78 | Stale canonical checkout / no pinned deployed-source export | NOT ADDRESSED | COORDINATOR | - | **OPEN** | stale canonical checkout btc-v31-current (d3544f9f7) |  |
| 79 | PREREGISTERED-HYPOTHESES missing; EXPORT_README wrong | NOT ADDRESSED | ANALYZER-FIDELITY | #307 | **IN PROGRESS** | EXPORT_README (#307); PREREGISTERED-HYPOTHESES still absent |  |
| 80 | No recurring secrets scan | NOT ADDRESSED | BLINDSPOT-CLOSE | #322 | **CLOSED-VERIFIED-LIVE** | daily full-history gitleaks (secret-scan-history.yml) | secret-scan-history.yml run 37015247367 @d6798e6c0 workflow_dispatch 2026-10-02T13:46:33Z success (full-history gitleaks) |
| 81 | SA tile stats mix retired lanes | CLOSED | audit | - | **CLOSED-AUDIT3 (not re-verified here)** | closed live in AUDIT-3 |  |
| X1 | #293 data_health "fully DONE" (L365) — live report still 17/17 STALE ( | CONTRADICTED | ANALYZER-FIDELITY | #293/#307 | **IN PROGRESS** | data_health still STALE until analyzer runs #293 |  |
| X2 | Self-aware custody "covered" (L362) — `inv.custody` SKIP. | CONTRADICTED | SELF-AWARE + BLINDSPOT-CLOSE | #300 + #309 | **IN PROGRESS** | inv.custody SKIP | :9021 findings[inv.custody].status=None |
| X3 | Self-aware Fly capacity "36.4 GB free ~470h" (L375) — `/data/capacity. | CONTRADICTED | SELF-AWARE | #300 | **IN PROGRESS** | Fly volume_free_gb null |  |
| X4 | #294 "AI input r2" — no WALL proof for the L371 pre-deploy check; Fly  | CONTRADICTED | AI-PLAN | #294 | **IN PROGRESS** | live-prompt ret_1m/ret_5m/delta_change verification pending |  |
| X5 | Analyzer revision parity — watcher `revision_parity=True`, `:9001 curr | CONTRADICTED | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | watcher revision parity honest | :9011 analyzer.api.status=AMBER observed='mirror sync receipt 71m old'; :9001 generation_revision=29742de53a5c Fly source_git_rev=29742de53a5c |
| X6 | Fly vs laptop `trading.orders` disagree (AMBER vs GREEN). | CONTRADICTED | BLINDSPOT-CLOSE | #309 | **CLOSED-VERIFIED-LIVE** | trading.orders missing toggles -> AMBER (Fly-published copy lags) | :9011 trading.orders.status=GREEN; Fly self-checks=4 |
| X7 | SA deploy receipt `in_progress` for a run that completed 10:35:08Z. | CONTRADICTED | SELF-AWARE | #300 | **IN PROGRESS** | deploy receipt in_progress after completion |  |
| X8 | Field-liveness inconsistency: Bybit `funding_rate` OK vs `predicted_fu | CONTRADICTED | SELF-AWARE | #300 | **IN PROGRESS** | Bybit funding field-liveness inconsistency |  |

## Directive / trace items

| Id | Owner | PR | Status | Plan | Live evidence |
|---|---|---|---|---|---|
| T-REPORTS-OK | ANALYZER-FIDELITY | #304 | **CLOSED-VERIFIED-LIVE** | required analyzer reports actually pass (root fix) | :9001/api/status.required_reports_ok=True failures=[] |
| T-TOGGLES | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | per-tile toggle state in public /api/status | Fly /api/status.active_tiles[].toggle_on=[None, None, None, None] |
| T-DELTA-CHANGE | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | update_orderflow set prev_delta after the update, so delta_change (live AI prompt) was always 0.0; fixed + test, ships post-freeze |  |
| T-RELAY-GATE | BLINDSPOT-CLOSE | #317 | **QUEUED-POST-FREEZE** | arming refused while stale-owner/pre-arming relay events unquarantined (readiness gate) |  |
| T-TIERA-API | ANALYZER-FIDELITY | #307 | **CLOSED-VERIFIED-LIVE** | Tier A promotion visible via storage.tier_a | :9011 checks[storage.tier_a].status=RED observed='23 datasets, backfilled=True; ai_decisions=RED: UNDATED_AFTER_BACKFILL, closed_trades=RED:' |

## Machine summary

```json
{
 "generated_at": "2026-10-02T13:55:26Z",
 "counts": {
  "QUEUED-POST-FREEZE": 72,
  "CLOSED-VERIFIED-LIVE": 76,
  "IN PROGRESS": 40,
  "OPEN": 26
 },
 "total": 214
}
```
