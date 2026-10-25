# Ops tracker

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

