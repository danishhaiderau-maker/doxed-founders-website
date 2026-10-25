# Ops tracker

## 25 Oct 2026 ~07:40 UTC — R8 URGENT paper down ~3h8m (Boss HM 18:32)

- **Live:** `4ebcf91d1754` (#889), still `execution_paused=true` / `DEPLOY_MAINTENANCE`; bootstrap PENDING / `SOURCE_LEDGER_TRUNCATED`; live disarmed.
- **Hung:** [40169802400](https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/40169802400) on **Complete receipt bootstrap** since ~07:33Z; #900 deploy [40172488019](https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/40172488019) pending behind it. Retry deploy will not help.
- **Cancel:** agents get **403** on `gh run cancel` / API. **Danish must Cancel workflow** in Actions UI — see `docs/HANDOFF.md` (store). After cancel: agent `BOOTSTRAP_GATE_FAILED=1` failure-resume; #900 deploy brings 25m timeout live; land [#904](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/904) sticky skip when CI green. No Soft B / Force / wipe / tip / arm.

## 25 Oct 2026 ~07:25 UTC — sticky DEPLOY_MAINTENANCE re-assert after clear resume (fast-follow)

- **Live:** still `execution_paused=true`, `pause_owner=DEPLOY_MAINTENANCE`, `_pause_priority=200` on rev `b57014c0eb28`; bootstrap `PENDING` / `SOURCE_LEDGER_TRUNCATED` / 0 progress; live disarmed.
- **Root cause:** Run [40166110026](https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/40166110026) bootstrap gate failed with `receipt bootstrap lost exact-revision flat maintenance` when Danish's deploy-owned `/api/resume` cleared the pause mid-gate (~07:02Z). Failure-path **Best-effort preserve** immediately re-POSTed `/api/pause` (durable `manual_admin_pause`+`pause_intent` lease on the Fly volume). Guaranteed paper resume then waited 12m on `bootstrap_complete_if_required` and died without releasing. Not an in-flight Actions pause loop after 07:14Z — the sticky lease is the persisted config flag plus preserve-after-clear-resume.
- **#900:** merged `f99681380` (bootstrap hard-timeout + stall/`BOOTSTRAP_GATE_FAILED` waiver + fly.paused RED). Does **not** include preserve skip-if-already-resumed.
- **Fast-follow:** `cursor/sticky-deploy-maintenance-lease-86dd` — `scripts/fly_preserve_maintenance.py` skips `/api/pause` when paper is already safely resumed; once-clear workflow on `scripts/once/clear-deploy-maintenance.flag` (push path; no `workflow_dispatch` required). Live stays disarmed; no Soft B / Force / wipe / tip / arm.
- **Release plan:** after deploys [40169802400](https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/40169802400) (#889) and [40172488019](https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/40172488019) (#900) settle → merge fast-follow (once-clear + failure-resume) or run `BOOTSTRAP_GATE_FAILED=1 python scripts/fly_failure_paper_resume.py`; prove `execution_paused=false` stable ≥10m.

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

