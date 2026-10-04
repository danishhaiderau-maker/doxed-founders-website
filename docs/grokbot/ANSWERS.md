# Answers for Grokbot

As of **2026-10-03T14:45Z**. Fly is in a deploy freeze until about **2026-10-04T11:10Z**; everything
marked "after post-freeze deploy" is written, tested and waiting in draft
[#395](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/395) (label `grokbot-handoff`).
Base URL for the bot: `https://doxed-btc-bot.fly.dev`.

## Endpoints

| Endpoint | Auth | Status | What it gives you |
|---|---|---|---|
| `GET /ready` | public | **live now** | Readiness, `active_tiles`, `xvl_evaluator_health.lanes[*]`, market-context feed health |
| `GET /api/system-health` | public | **live now** | Last laptop-watcher verdict, counts, failing checks, open alarms |
| `GET /api/ready` | public | after post-freeze deploy (401 today) | Same handler and payload as `/ready` |
| `GET /api/monitor/summary` | public | after post-freeze deploy | `monitor_summary_v1`, under 8 KB: safety flags, relay state as known, pause state/owner, WS/AI/evaluation ages, `boot_id` + since-boot counters, per-tile ON/OFF + closes/net/latency p50, open alarm ids with `first_seen`, epoch id, git rev, registry signature |
| `GET /api/monitor/lanes` | public | after post-freeze deploy | `monitor_lanes_v1`, under 10 KB: per active lane closes, wins/losses, net USD, long/short net, mean bp, true peak-to-trough drawdown, last trade time, signal-to-fill latency p50/p90/n. `503 TRADE_LOCK_BUSY` with `Retry-After` when the bot is busy; retry |
| `GET /api/monitor/digest` | `Authorization: Bearer <MONITOR_READ_TOKEN>` | after post-freeze deploy **and** secret set | `monitor_digest_v1`, at most 64 KB: the redacted laptop digest (watcher, self-aware, uptime, 24h tiles, analyzer verdicts, decision readiness, fees, capacity, AI scorecard), `stale=true` if older than 15 min |

All monitor payloads carry `boot_id` and `generated_at`. Summary and lanes are cached for 15 s, so
polling faster than that returns the same document.

## 1. When will the `MONITOR_READ_TOKEN` / `/api/monitor/digest` Fly half be drafted?

Drafted now in [#395](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/395). The laptop
watcher already knows how to attach the redacted digest to its existing push to Fly, but that is
**off** until the Fly side is deployed. After the post-freeze deploy the operator sets the Fly secret
and turns the laptop flag on; you will receive the token through the secure secret box only (never
chat, issues or this repo). Until the secret is set the route returns 404. The token works on that one
route only and is never accepted as an admin credential.

## 2. `/api/ready` vs `/ready` and #367 latency

`/ready` is public and is the same handler as `/api/ready`; `/api/ready` returns 401 today only because
it was missing from the read-only allowlist. #395 adds it. Once #367 is deployed, per-lane latency is
at `xvl_evaluator_health.lanes[<lane>].latency` on both paths (schema `xvl_signal_latency_v1`,
`signal_to_fill` p50/p90/n). Until then, read `/ready`. The new `/api/monitor/lanes` and
`/api/monitor/summary` also expose the p50.

## 3. Do `active_tiles` expose `pre_registration`?

After the post-freeze deploy, yes: every `/ready` `active_tiles[*]` entry gains `pre_registration`
(`declared`, `hypothesis_id`, `status`, `registered_at`, `registered_cohort`, `evidence_world`,
`ci_method`, `honest_label`, `promote`, `kill`). It is read from the single tile registry, so it can
never disagree with the roster. Tiles without a pre-registration show `declared: false`.

## 4. A light public per-lane P&L summary?

`GET /api/monitor/lanes` (after post-freeze deploy), described above. Drawdown is peak-to-trough of the
cumulative net within the current showcase session, starting from zero. Mean bp is total net divided by
total notional.

## 5. Which agents are still pushing, and where?

As of this update every claimed agent from the 2026-10-03 batch has posted DONE except:

- **GROKBOT-APIS** (this work): draft [#395](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/395)
  (`feat/grokbot-monitor-apis-postfreeze`, post-freeze) and a laptop-only docs/watcher PR on `master`.
- **MIX-MATCH-SEARCH**: merged [#394](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/394)
  (laptop analyzer only) and is finishing its analyzer refresh. No Fly change.

All Fly-runtime changes are draft PRs labelled `POST-FREEZE`, collected in the integration PR
[#351](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/351), whose body holds the
verified merge order. Nothing merges to Fly before the freeze ends. Laptop-only merges use `[skip ci]`
and trigger no deploy.

## 6. Will `analyzer.studies` integrity INVALID clear only at the clean epoch?

Yes, plus the #420 fix. The INVALID verdict comes from pre-existing lifecycle codes in pre-epoch
historical evidence, which is immutable and is not repaired in place. The clean epoch
`ce-20261004-v31-final-e` has been live since 2026-10-04T01:40:18Z (12:40 AEDT; #351/#336 shipped).
However, the laptop's first clean-epoch analyzer generation still admitted 37,224 pre-epoch rows
(#420). Most were files Fly retired at the boundary reset that the laptop shadow tree keeps as custody
copies (`post_exit_replay.jsonl` and others), which the analyzer kept reading.

After the #420 fix:
- Those retired custody copies leave the promotion view.
- They are moved (never deleted) out of the canonical store into `migration/retired/`.
- Ops ledgers no longer count as evidence.
- The three evidence streams the reset keeps (`taker_signal_counterfactuals.jsonl`,
  `adaptive_entry_decisions.jsonl` and `expired_orders_3factor.csv`) are filtered by every analyzer reader.

Until the next analyzer generation reports `data_epoch.pre_epoch_rows_admitted == 0` and
`clean_epoch_certify` passes, the watcher shows **AMBER with the declared blocker
`CLEAN_EPOCH_PENDING`**, not RED. Escalate if it is still present after the epoch is certified, or if
the blocker expires (2026-10-06) first.

## 7. `liq_okx` stale: known or a gap?

A known **false** alarm, fixed in #395. The OKX liquidation feed stays connected (only 3 reconnects
observed), but liquidations are sparse and OKX answers keepalive pings with a plain-text `pong` that
the collector discarded before updating liveness. So `liq_okx` age swings between about 20 s and 65 s
and the feed flaps between OK and DEGRADED with no data loss. After the deploy, keepalives count as
liveness for liquidation feeds only (new `alive_age_sec`); `age_sec` still means "time since the last
real message". Until then, treat `liq_okx` DEGRADED as expected unless the reconnect count rises or the
age exceeds a few minutes.

## Known false alarms

- `liq_okx` DEGRADED/flapping: see 7 (until the post-freeze deploy).
- `analyzer.studies` AMBER `CLEAN_EPOCH_PENDING`: see 6 (until the #420 fix has run one analyzer
  generation and `clean_epoch_certify` passes on `ce-20261004-v31-final-e`).
- `data.compat` stream AMBER "pre-epoch/foreign rows on disk, rejected by every analyzer reader" on
  `taker_signal_counterfactuals.jsonl`, `adaptive_entry_decisions.jsonl` or `expired_orders_3factor.csv`
  is expected after a boundary reset (#420). Those rows stay until `clean_epoch_wipe`; every analyzer
  reader filters them and the receipt lists them under `pre_epoch_rows_read_guarded_by_stream`. The
  same rows in any other stream are RED and real.
- `relay_outbox_quarantine.jsonl`, `relay_outbox_retired.jsonl`, `runtime_telemetry_1m.jsonl`,
  `retired_tile_boundary_receipts.jsonl` and `pre_entry_evidence_handoffs.jsonl` carry the note "ops
  stream, not analyzer evidence". Their pre-epoch or undated rows are by design and never count
  toward purity (`data_epoch.NON_EVIDENCE_BASES`).
