
## LIVE-FIX-1501 Clock (HM 23:42, observability) - PR open, shipped-unverified
- Root cause: Fly VM runs chronyd (stratum 2, ref 216.239.35.8 Google). `chronyc -c tracking` 23:44 AEDT: system offset +0.058 ms, root delay 0.75 ms, dispersion 0.24 ms => max error ~0.67 ms. Cloudflare cdn-cgi/trace `ts` probe reported +267..+992 ms: probe artifact, not real skew.
- Change: remove adjtime slew (it fought chrony); retire Cloudflare probe (CLOCK_OFFSET_PROBE_ENABLED default 0); clock_sync_v2 source=chrony, offset_ms = chrony max error bound; /live/clock adds `fly.chrony` and `railway` estimate +/-100 ms (bounded from order_sent->fill_mts).
- Metric: /live/clock fly.source=chrony and |offset| < 5 ms after deploy; clock-skew check GREEN.
- FLAG: every latency/lag number computed while CLOCK_SLEW_ENABLED=1 was live (since the #634/#637 deploy, ~12 Oct 21:xx AEDT, until this deploy) used a Fly clock that the slew may have pushed off chrony's time. Treat Fly-side timestamps from that window (fly_intent_emitted, fly_received, paper-lag, the "-335 ms correction") as suspect +/- slew applied; Railway-only spans (ack->stop ack, etc.) are unaffected.

## LIVE-FIX-1502 Reporter run log (HM 23:42 (b) step 1, observability) - PR open
- Change: every reporter tick (executor kick, website 5 s interval, trailing rerun) logs `[LIVE-COPY-REPORTER-RUN] {json}` with process, trigger, collect_ms, collected/unacked/attempted/skipped_backoff/sent/failed, per-report delay (event_at -> Fly ack) and error; last 200 kept in memory, last 50 on the ops reporter status. No send-path change.
- Metric: across the next deploy, the Railway logs show the run gap and the delay for every fill report; this gives the baseline for the outbox (target p95 < 5 s).
- Outbox (step 2): plan only, comes back for review before build.
