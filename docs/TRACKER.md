# Ops tracker

## 25 Oct 2026 ~16:31 AEDT (HM) — bootstrap hard-timeout + fly.paused RED (#900 follow-up)

- **Finding:** Run [40161182874](https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/40161182874) still in_progress ~55–70+ min on **Complete receipt bootstrap inside exact-revision maintenance**; resume-paper steps pending. Fly `39358c1` / v729: `execution_paused=true`, `pause_owner=DEPLOY_MAINTENANCE`, `running=false` / uptime `PAUSED_DEPLOY` amber since ~04:30Z; bootstrap still PENDING / `SOURCE_LEDGER_TRUNCATED` / 0 progress / 1800s backoff. Live flat + disarmed.
- **Monitor bug:** Laptop `system_health` already had `fly.paused` **RED** after 45m, but Fly self-check `_system_health_fly_self_checks` always reported **AMBER** while paused, and uptime strip stayed amber — so the live monitor signal never aged to RED.
- **Clear (agents blocked):** `gh run cancel` → **403**; no `BOT_ADMIN_TOKEN` in env. Danish one-shot:
  1. `gh run cancel 40161182874`
  2. `curl -sS -X POST 'https://doxed-btc-bot.fly.dev/api/resume' -H "X-Bot-Admin-Token: $BOT_ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{"clear_admin_manual_pause":true,"owner":"DEPLOY_MAINTENANCE"}'`
  3. Read back `/api/status`: `execution_paused=false`, pause cleared, `live_armed=false`.
- **Code fix (extends #900):** (1) Bootstrap step hard timeout **25 min** / `BOOTSTRAP_TIMEOUT_SEC=1200` raises `RECEIPT_BOOTSTRAP_TIMEOUT`; `BOOTSTRAP_GATE_FAILED=1` on failure-path resume waives bootstrap-complete so paper resume always falls through. (2) `fly.paused` / `PAUSED_DEPLOY` → **RED after 45m** on Fly self-check + uptime colour. Stall fail-loud from prior tip retained. GS-01 / live stay disarmed; no Soft B / Force / wipe / tip / arm.

## 25 Oct 2026 ~05:18 UTC — DEPLOY_MAINTENANCE stuck after Fly v729 (#885 push)

- **Symptom:** `doxed-btc-bot` paper paused by owner `DEPLOY_MAINTENANCE` after machine `48e1403c492578` came up on release **v729** / rev `39358c1bffbe`. AI cadence stale; laptop `SEGMENT_PULL_STALE`. Live remained disarmed (`live_armed=false`, `bitfinex_live_enabled=false`, GS-01 `relay_eligible=false`).
- **Root cause:** Guarded deploy run [40161182874](https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/40161182874) deployed and proved liveness, then stuck in step **Complete receipt bootstrap inside exact-revision maintenance**. Public `/api/status` showed `receipt_bootstrap` required+PENDING with **0** progress while `lifecycle_pipeline.last_outcome=WORKER_FAILED` / `last_error_code=SOURCE_LEDGER_TRUNCATED` and a **1800 s** backoff. Paper resume only runs after bootstrap COMPLETE; `fly_failure_paper_resume` also required COMPLETE, so neither the success path nor the failure-path resume could clear `DEPLOY_MAINTENANCE`. Pending `workflow_dispatch` [40163538971](https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/40163538971) stayed queued behind concurrency group `fly-btc-production`.
- **Clear action (this agent):** Could **not** clear — no `BOT_ADMIN_TOKEN` / `FLY_API_TOKEN` / flyctl in the environment; `gh run cancel` on the stuck job returned **403**. Danish (or Actions with secrets) must:
  1. Cancel stuck run `40161182874` (otherwise a mid-gate re-pause can undo an admin resume).
  2. Then either admin resume:
    `POST https://doxed-btc-bot.fly.dev/api/resume` with `X-Bot-Admin-Token` and body `{"clear_admin_manual_pause":true,"owner":"DEPLOY_MAINTENANCE"}`
     **or** Actions `workflow_dispatch` `mode=resume-paper` with `expected_bootstrap_revision=39358c1bffbe` (after the fix below is on the ref used).
- **Code fix PR:** deploy-flow always fail-loud on terminal bootstrap stall and release `DEPLOY_MAINTENANCE` on healthy paper-only identity (stall waiver in failure/resume-paper path). See open PR from branch `cursor/deploy-maintenance-pause-2a94`.
- **Keep disarmed:** No Soft B / Force / blunt sync / wipe / tip / live arming.

