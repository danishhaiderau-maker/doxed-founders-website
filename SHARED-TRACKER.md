## URGENT PR3 r5_segment_pass never-delete (HM-mapped)
- Remove auto-unlink (`shipper` r5_segment_pass / `execute_r5_segment_prune` R22-deleted) + hard-coded prune arm; fail-closed on missing status; R5a eligibility via `validate_custody`+`analyzer_consumed_seq`+parity GREEN+2 ACK cycles+min(custody,ack,analyzer); tripwires (−500 seq / 2 h ACK); stop-write RED; PR4 for lifecycle/v22 deleters; R44+R31 before `RESEARCH_SEGMENTS_ENABLED=1`. **Success:** zero auto-unlinks; greps clean; two-cycle R31 read-back; live OFF. Plan `90f0a5491850`; rules `10f0b8daa510`; base #951 `f269ce852`.

## URGENT PR2 remaining deleters (31 Oct 2026) — PARTIAL until #949
- Stop rotation_cap (no env gate; next deletes ~09:00–14:30 AEDT) + market_segments + analyzer keep-3 + runtime-log/doRollover/_trim/maintain_fresh_collection_files + R22-delete reset_all_csv; #949 = partial 1/5 (ruling 44556caf8f25) — may deploy before #949 but PARTIAL until merge; sweeper already 111 files / 2,407,788,012 B (24 Oct 19:26–31 Oct 02:32); laptop 102 OBSERVED / 9 INFERRED lost (cd6960e96f23). Order PR2→PR4→PR3. Success: R31 ≥2 full rotation_cap sweeper cycles; no new rotation_cap delete receipts; .N retained; 70/85 on GET; greps/mutations green; live OFF; DONE only after #949.

## 30 Oct 2026 — R8 pure revert #929+#930 (open, do not merge)
- See `docs/TRACKER.md`. PR [#940](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/940) `cursor/revert-929-930-12bb`. Trades since 19:59 AEDT non-countable; R21 dry-run package prepared only (no delete).


## 25 Oct 2026 ~07:25 UTC — sticky DEPLOY_MAINTENANCE after clear resume
- See `docs/TRACKER.md`. Preserve re-asserted pause after mid-bootstrap `/api/resume`; Guaranteed resume blocked on bootstrap stall. #900 merged (`f99681380`) without preserve-skip. Fast-follow `cursor/sticky-deploy-maintenance-lease-86dd` + once-clear flag. Live stays disarmed.

## 25 Oct 2026 ~16:31 AEDT — bootstrap hard-timeout + fly.paused RED (HM / #900)
- See `docs/TRACKER.md`. #900 **merged** `f99681380`: bootstrap 25m hard timeout → `BOOTSTRAP_GATE_FAILED` waiver; Fly `fly.paused` RED@45m. Sticky re-assert after clear resume is a separate fast-follow (preserve skip). GS-01/live disarmed.

## 25 Oct 2026 ~05:18 UTC — DEPLOY_MAINTENANCE stuck after v729
- See `docs/TRACKER.md`. Root cause: receipt bootstrap terminal stall (`SOURCE_LEDGER_TRUNCATED`, 0 progress, 30 min backoff) blocked paper resume after healthy v729 deploy run 40161182874. Fix branch `cursor/deploy-maintenance-pause-2a94` (fail-loud stall + release pause on failure/resume-paper). Clear requires Danish cancel+admin `/api/resume` or Actions `resume-paper` (no secrets in cloud agent). Live stayed disarmed.

## LIVE-FIX-1501 Clock (HM 23:42, observability) - PR open, shipped-unverified
- Root cause: Fly VM runs chronyd (stratum 2, ref 216.239.35.8 Google). `chronyc -c tracking` 23:44 AEDT: system offset +0.058 ms, root delay 0.75 ms, dispersion 0.24 ms => max error ~0.67 ms. Cloudflare cdn-cgi/trace `ts` probe reported +267..+992 ms: probe artifact, not real skew.
- Change: remove adjtime slew (it fought chrony); retire Cloudflare probe (CLOCK_OFFSET_PROBE_ENABLED default 0); clock_sync_v2 source=chrony, offset_ms = chrony max error bound; /live/clock adds `fly.chrony` and `railway` estimate +/-100 ms (bounded from order_sent->fill_mts).
- Metric: /live/clock fly.source=chrony and |offset| < 5 ms after deploy; clock-skew check GREEN.
- FLAG: every latency/lag number computed while CLOCK_SLEW_ENABLED=1 was live (since the #634/#637 deploy, ~12 Oct 21:xx AEDT, until this deploy) used a Fly clock that the slew may have pushed off chrony's time. Treat Fly-side timestamps from that window (fly_intent_emitted, fly_received, paper-lag, the "-335 ms correction") as suspect +/- slew applied; Railway-only spans (ack->stop ack, etc.) are unaffected.

## LIVE-FIX-1502 Reporter run log (HM 23:42 (b) step 1, observability) - PR open
- Change: every reporter tick (executor kick, website 5 s interval, trailing rerun) logs `[LIVE-COPY-REPORTER-RUN] {json}` with process, trigger, collect_ms, collected/unacked/attempted/skipped_backoff/sent/failed, per-report delay (event_at -> Fly ack) and error; last 200 kept in memory, last 50 on the ops reporter status. No send-path change.
- Metric: across the next deploy, the Railway logs show the run gap and the delay for every fill report; this gives the baseline for the outbox (target p95 < 5 s).
- Outbox (step 2): plan only, comes back for review before build.

## 13 Oct 00:40 AEDT: HM 00:28 audit answers (read-only) + proposed new order
- #645 verified: 8 DISLOC trades, all fields filled; split steps sum to the total exactly (diff 0 ms). Caveat: the parts and the total come from the same stamps, so the sum check proves completeness, not independent accuracy. Probe 0 found the fill in 7/8 (1 at probe 5 via WS trade). Ids: dsl-1791808910-7 (first, 23:41:52), -8, -9, -10, dsl-1791810796-2, -3, dsl-1791811065-4, dsl-1791811138-5.
- Q1 dsl-1791810796-2 closed at +16 s: Bitfinex history (read key) shows entry LIMIT +0.0003 @82708 at 00:13:18.426; correct stop -0.0003 @82438 at 00:13:20; a SECOND reduce-only STOP @82879 (above market for a long) created at 00:13:21 and executed at once at 00:13:22.670 @82702. Executor log 00:13:22: "Hire stop retry ... cycle=cmv59vxr30043np01amp57b3t @ 82879.12", then "S6b adopted orphan position ... cycle=cmv59vxr... entry 82987 stop=82879.12 (MIRROR_DISASTER_STOP)". That cycle is a stale lot from dsl-1791809159-10 (23:46, entry 82987), still OPEN in the DB after its TIME_60S close at 23:47:05. The stop-retry loop put its old stop on the new position. Close source = stop-retry on a stale DB lot (netting/attribution bug). Not the deploy (run started 00:13:58), not A2, not the sweeper (not deployed).
- Q2: RELAY_TIME_DEADLINE_EXIT is unset on Railway; master default is 'on' (#633 code), so A2 v1 is live: deadline = signal + 60 s, pre-arm 1.5 s, scheduled twice per trade. done_after_deadline 4.7-7.5 s (n=7), measured to recorded close, so the exit path itself (claim, preflight reads, submit, confirm) takes ~5 s. It has no per-step marks yet.
- Q3: between the entry ack and probe 0 the code does recordHireExecutionEvent('ORDER_PLACED') (DB write) then signalCycleParticipant.findUnique (DB read); probe 0 then does loadExecutionMeta (DB). That's 2 DB round trips, about 450-800 ms. Not the nonce lane.
- Q4: executor is in Railway us-west2 (1 replica). DATABASE_URL = Neon (docs/railway-deploy.md); host/region not yet read from the executor. DB RTT, Bitfinex TCP/TLS/REST and Fly sin->us-west2 are NOT measured yet; this needs a read-only diagnostic route (observability), pending.
- Q5: #643 sweeper rule is literally count == 1 per position (multi-lot positions are skipped, not alerted). It must change to "stops == open lots and sum of stop qty == |net|" before merge.
- Q6: SUBSCRIBER_MAX_CONCURRENT_SIGNALS unset -> code default via resolveMaxConcurrentCopySignals; correlated-exposure boundary 0.0009 (9 bp). Q1 shows a phantom second lot can exist today.
- Netting fields that already exist: correlated_cluster_evidence on ORDER_PLACED (same-direction count, aggregate qty), dedupe stats (#639), protection_state sweep events. Missing: exchange net vs ledger qty diff, stop qty sum vs net, exit.close_source / close_order_id, concurrent_open_lots_at_entry, per-trade deployed_rev, effective max-concurrent/flag config on checks.
- Proposed new order: (0) fix/close stale OPEN lots and stop the stop-retry acting on unattributed positions [behaviour, needs HM/Danish]; (1) DB: measure, then cut round trips on the stop path; (2) F4 + #646; (3) stop path; (4) A2 v2 (#641) with exit marks; sweeper (#643) rewritten for netting. F1/WS/#641/#643 on hold.

## 13 Oct 00:44 AEDT: #647 deployed; DISLOC held OFF (Decision A step 1 / option B)
- What paused live: output history `by=disarm reason=ADMIN_PAUSE:DEPLOY_MAINTENANCE ua:Python-urllib/3.12` from GitHub runner IP 20.15.229.145 at 00:23:48, 00:31:15 and 00:31:23 AEDT, i.e. deploy run 38254249429 (#647). Not Danish.
- #647 run 38254249429 succeeded; restore re-armed DISLOC at 00:43:17. I disarmed DISLOC live-orders at 00:43:41 (HM/Boss Decision A: no re-arm before the stale-lot cleanup). Exchange flat: 0 positions, 0 orders, 0 stops. No trade in the 24 s window.
- Clock after-numbers (00:41, slew removed, source=chrony): system offset +0.029 ms, last offset -0.036 ms, rms 0.134 ms, max error 0.689 ms, stratum 2, Normal. Before: Cloudflare probe 15.7 ms (slew enabled), earlier +267..+992 ms. Railway estimate +/-100 ms (order_sent->fill_mts min 97 ms, n=11).

## Stale-lot fix design (HM agreed; Decision A)
1. Cleanup before re-arm: for each OPEN/PENDING participant on the live account, mark it CLOSED (reason STALE_LOT_CLEANUP, keep every row/event) if a terminal exit event exists for it, OR if Bitfinex shows no exposure it can own (account flat now, so every OPEN lot is stale). Log each id. Needs an executor-side admin path (no DB creds on the box), so it ships as a tiny one-shot route/flag on the relay. Live stays OFF until it's done.
2. Stop-side guard (small PR): refuse a sell stop >= bid or a buy stop <= ask at submit; log + alert STOP_SIDE_REFUSED.
3. Pollution tag: dsl-1791810796-2 (and any trade whose stop came from another cycle) gets `polluted: STALE_LOT_STOP`, excluded from live-vs-paper stats, data kept.
4. Alerts: STOP_EXECUTED_FAST (stop executes < 2 s after creation); STOP_UNATTRIBUTED (exchange stop not tied to an open lot).
5. Full fix: lot closed atomically in the same transaction as the exit completion; stop-retry and reconcile-adopt refuse positions they can't attribute to the lot's own entry order/fill (qty + order id), and alert instead of adopting; tests: stale lot + new entry, restart mid-exit; exit-path timing marks.
- Metric: 5+ DISLOC trades with zero unattributed stops, zero STOP_EXECUTED_FAST, zero early closes vs paper.

## LIVE-FIX-1503 stale-lot cleanup + executor diagnostics (Decision A step 1) - includes #650
- Cleanup: on the first executor tick after deploy, for each live Bitfinex account: authenticated position + active-orders read; if flat, every OPEN/PENDING_ENTRY lot untouched for 5 min becomes CLOSED (conditional updateMany, idempotent), with a STALE_LOT_CLEANUP event (reason, previous status, polluted=true) and a log line per id. A read failure or non-flat account closes nothing. Kill switch RELAY_STALE_LOT_CLEANUP=off|dry.
- Diagnostics (logs only, every 30 min, `[EXECUTOR-DIAG]`): DB host (no creds) + pooler flag + server addr, SELECT 1 p50/p95 x20, no-op updateMany x10, server exec time, first-query time; Bitfinex public ticker x10, DNS/TCP/TLS to api and api-pub, signed active-orders x3.
- Metrics: log shows `[STALE-LOT-CLEANUP] ... open_lots=N cleaned=N account_flat=true`, then 0 OPEN lots on the next boot; EXECUTOR-DIAG line present with numbers.
- Not in this PR (next small PR): stop-side guard, fast-stop / unattributed-stop alerts, Fly-side polluted exclusion.

## LIVE-FIX-1505 stop-side guard + cleanup gaps + lot evidence (13 Oct ~01:10 AEDT)
- Stop-side guard: every reduce-only STOP submit checks the public ticker first (executor p50 18 ms); sell stop >= bid or buy stop <= ask -> refused (STOP_SIDE_REFUSED thrown, `[STOP-SIDE-GUARD] REFUSED` error log, kept in stopSideRefusals). Ticker failure never blocks a stop (logged). Metric: 0 wrong-side stops reach Bitfinex; any refusal is RED.
- Cleanup: periodic every 5 min (was once per process), plus `[STALE-LOT-ALERT]` when non-terminal lots sit on a flat account, plus `[STALE-CYCLE-CHECK]` for signalCycle OPEN/PENDING_ENTRY rows whose participants are all terminal.
- Evidence: `[EXECUTOR-DIAG]` logs lot_evidence for cycle cmv59vxr30043np01amp57b3t (participant + cycle status, createdAt, envelope trade/signal id, event list with times) and non-terminal lot counts by status (INTENT included).
- CAUSE UNDER REVIEW: the 00:40 "stale lot from dsl-1791809159-10" attribution is challenged by HM (cmv59 prefix matches the 00:13 trade's own records). Corrected once the lot_evidence line is in.
- Outbox note: the website reporter re-sent 432 already-delivered reports in one 151 s run after restart; delivered state is in memory only. The outbox must persist delivered/acked state.

## 24 Oct 2026 — realism-v2/integration fix commit (RK PASS / HM AGREE), CI-only via draft #859
- M1: book-walk tape carries per-row WS_FRAME receive_ts; fills price on the first row received at or after the trigger.
- M2: one label set — paper_fill.delay_stamp and live_ack_pool both use r17_fields.MEASURED_DELAY_PATTERN (N>=10). Sampler lane_* labels are now DEFAULT/unverified (bot.py DEFAULT_DELAY path consequence).
- M3: exit-fill DATA_ERROR now R17_UNPROVEN / EXIT_FILL_UNPROVEN; never green in candidates.
- FAIL-OPEN: book_limit_entry FILL/NO_FILL receipt evidence COMPLETE only with measured receipt; r17 entry proofs include BOOK_RECEIVE_TS_MISSING.
- Exit timing: exit_fill uses paper_fill RESIDENT_LEG with STOP_RULE_NO_POOL, stamps trigger_ts/book_row_ready_ts.
- Lock v5: not located in repo/tracker/#859 — UNRESOLVED.

## 24 Oct 2026 22:30 AEDT — R1 root cause: R17_STAMPED on all 23 REALISM_V2 rows; WS_TICK; watcher RED 5 (read-only, master 63b462ed)
- Q1 CAUSE: bot.py:32986-32987 writes non_countable=bool(_non_countable_reasons()) + plural non_countable_reasons; bot.py:33173 stamp_on_write -> r17_exclusion.py:273-274 sees non_countable truthy, singular non_countable_reason absent -> label R17_STAMPED, and proof_failures is skipped. Underlying reason (Fly csv, 23/23): DEFAULT_DELAY_ENTRY (+DEFAULT_DELAY_EXIT 21, FORCE_FLATTENED 3); delay_source ..._default_until_n10_n0 (bot.py:8219-8234; live_ack_pool n<10 rule). LEGIT non-countable, MISLABELLED. Full proof_failures on the 23: DELAY_NOT_MEASURED 23, EXIT_DELAY_NOT_MEASURED 21, LEVEL_CROSS_UNKNOWN 8, ENTRY_NOT_BOOK_WALKED/ENTRY_PRICE_AGE_MISSING/BOOK_RECEIVE_TS_MISSING 4.
- Can count only when pool key n>=10 of REAL current-epoch live acks (live_ack_pool.py docstring) -> unmeetable while live disarmed, except EXCHANGE_RESIDENT_NO_DELAY exit legs.
- Q2: WS_TICK = wake-up source only (WS trade print, bot.py:29796 -> 30437 -> 30463); the tick price is discarded; mark = get_mark_price (bot.py:30479, 7466-7475) -> book_exit_mark -> quote_trigger.book_exit_price on bfx_book_10l rows (7450-7463); ticker fallback refused. Not an R3 ticker trigger.
- Q3: /api/system-health 11:25Z: RED analyzer.generation/reports/cycle, laptop.chain_monitor (ANALYZER_NO_COMPLETION), ledger.reconciliation, fly.exit_latency; mostly stale analyzer stuck RUNNING 3h on rev 346ef2bf vs Fly 63b462ed (deploy 21:43 AEDT) + in-memory 1231 cache.
## URGENT PR4 r5_retention_loop stop (rebase on #950)
- R22-remove lifecycle/transfer/v22 deletes only; **must not touch `cap_market_segments` (PR2/#950)**; no rotate-discard; v22 70/85; wipe_stale_locks pin (1 h + older-than-boot); ≥512 MiB v22 fixture; book10 safety; order PR2→PR4→PR3. **Success:** greps clean — no delete reachable from `r5_retention_once` except `wipe_stale_locks`; live OFF. Plan `51dde5f11446`; rules `10f0b8daa510`.
