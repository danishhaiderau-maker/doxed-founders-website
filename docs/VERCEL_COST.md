# Vercel Cost Audit & Savings (2026-07-08)

> Audit of `team_KZnVXkwWtzHaT0EfltPcy3LE` (Pro plan, `danishhaiderau-4138's projects`).
> Period analysed: 2026-06-17 → 2026-07-08 (one full billing month).
> Method: FOCUS v1.3 JSONL from `GET /v1/billing/charges` + deployment history
> from `GET /v6/deployments`.

## TL;DR

- **Current trajectory:** $87.68 effective / $53.68 billed over the period
  ($20 included credit + $53.68 on-demand = $73.68 invoice).
- **Projected at this rate:** ~$141/mo effective, ~$122/mo billed.
- **Fix applied today:** `commandForIgnoringBuildStep` skips builds for
  `chore(founder-os)` and doc-only commits.
- **Expected savings:** ~$22/mo (≈75% of build CPU minutes on both projects).
- **Bigger lever still on the table:** delete the duplicate `_deploy_sync`
  project → another ~$15/mo. **Requires user sign-off.** See
  [Recommended follow-ups](#recommended-follow-ups).

## Where the money went

Top cost drivers, 2026-06-17 → 2026-07-08 (effective cost):

| Service                          | Effective  | Billed     | Notes                                                        |
| -------------------------------- | ----------:| ----------:| ------------------------------------------------------------ |
| **Build CPU Minutes**            | **$70.06** | **$51.46** | Dominant cost. ~50% on `doxed-founders-website`, ~45% on `_deploy_sync`. |
| Pro plan                         |    $14.00  |     $0.00  | Base subscription (covered by included credit).              |
| Observability Events             |     $3.48  |     $2.13  | 2.9M events. Real production logging.                       |
| Edge Requests - Additional CPU   |     $0.05  |     $0.03  | Negligible.                                                  |
| Fluid Active CPU                 |     $0.04  |     $0.02  | Negligible.                                                  |
| Everything else                  |    < $0.05 |    < $0.05 | ISR reads, function invocations, bandwidth — all sub-cent.   |

**Build CPU Minutes = 80% of the bill.** That's the lever.

## Why build minutes are so high

Last 100 deployments on `doxed-founders-website` (2026-07-06 → 2026-07-08):

```
By author:
  73  danishhaiderau-maker       (mostly READY)
  27  Cursor Agent (doxxedcrypto) (24 BLOCKED, 3 READY)

By commit subject:
  56  chore(founder-os): sync memory   ← 1-line tasks.json changes
  13  other (real code changes)
   7  ERROR
  24  BLOCKED (Cursor Agent commits — Vercel commit-attribution rule)

Build time analysis:
  chore(founder-os) READY builds:  56 deploys, 60.28 minutes total
  non-chore READY builds:          13 deploys, 14.52 minutes total
                                                  ^^^^^^^^^^^^^^^
                                       → 75%+ of build time is memory-sync noise
```

Every `chore(founder-os)` commit is the founder-os agent pushing a one-line
`tasks.json` state file. That file is **never read by the Next.js build or
runtime**, so every one of those builds produces identical output. Vercel
still bills for the build slot.

Worse: there's a **duplicate project** (`_deploy_sync`, `prj_NgEyORBHgaUVs867LtHN6RtU8DUq`)
hooked to the same GitHub repo + `master` branch. Every push triggers a
build on BOTH projects. `_deploy_sync` serves `deploysync.vercel.app` (a
test domain) and burns ~$30/mo on identical builds to the main site.

## What was applied

### Fix A: `commandForIgnoringBuildStep` (live as of 2026-07-08)

New file `scripts/vercel-ignore-build.sh` is wired as the project's
Ignored Build Step on **both** projects:

- `PATCH /v9/projects/doxed-founders-website`
- `PATCH /v9/projects/_deploy_sync`

Both with body `{"commandForIgnoringBuildStep":"bash scripts/vercel-ignore-build.sh"}`.

The script logic:

```text
1. No VERCEL_GIT_COMMIT_SHA env?    → BUILD (manual / API deploy)
2. Root commit (no parent)?         → BUILD (safety)
3. Commit subject contains
   "chore(founder-os)" or "sync memory"?  → SKIP
4. git diff HEAD^ HEAD shows NO changes under
   apps/ packages/ prisma/ public/ next.config.* package*.json
   tsconfig*.json railway.* etc.?           → SKIP
5. Otherwise                         → BUILD
```

Verified live 2026-07-08:

- Pushed a docs-only commit (`docs: ignore-build probe`, SHA `ba5ea005`).
- API-deployed it via `node scripts/vercel-deploy.mjs --sha ba5ea005 --wait`.
- Deployment transitioned `INITIALIZING → CANCELED` in 16s with help link
  `vercel.com/docs/platform/projects#ignored-build-step`.
- Reverted the probe commit (`101ebcc9`) to keep master clean.

Result: every memory-sync and doc-only commit will now be skipped at
build time, on both projects. Build CPU minutes billed → ~25% of prior.

API-triggered deploys (`scripts/vercel-deploy.mjs`) are still evaluated
against the ignore step, because they pass `gitSource.sha`. That's
correct: memory-sync commits deployed via the bypass path are also
skipped, so we don't accidentally re-introduce the noise via the
deploy script.

### Fix B: add-ons

Audited every add-on the user thought was burning money:

| Add-on                | Status in metadata | Actual spend (FOCUS)   | Action     |
| --------------------- | ------------------ | ---------------------- | ---------- |
| Speed Insights        | enabled, hasData:false | **$0.00** (0 data points consumed) | None — already free in practice. `<SpeedInsights>` component not imported in `apps/web/src/app/layout.tsx` or anywhere else, so no data points are collected. |
| Web Analytics         | enabled            | **$0.00** (0 events consumed) | None — same reason. `<Analytics>` component not imported. |
| Observability Plus    | entitlement active | $3.48 effective / $2.13 billed | **Left enabled.** Real production logging (2.9M events). User is using it deliberately for monitoring. |
| Observability Base    | enabled            | $0.00 (free base)      | None. |

None of the add-ons the user worried about are actually generating
spend — they're "enabled" in project metadata but the client-side
collection code was never installed, so Vercel has nothing to bill.

If the user wants to tidy up the dashboard, they can manually disable
Speed Insights and Web Analytics from the Vercel UI (no API endpoint
exists for this — confirmed via web search and direct PATCH attempts;
the property is rejected). This is a cosmetic fix only, **zero savings**.

## Estimated monthly savings

Build CPU Minutes over the last 7 days (Jul 1-7):

| Project                 | Effective | Billed   |
| ----------------------- | ---------:| --------:|
| `doxed-founders-website`| $14.58    | (projected similar) |
| `_deploy_sync`          | $14.80    |          |
| **Total build cost**    | **$29.37**|          |

Fix A eliminates ~75% of these (the chore(founder-os) + doc-only builds):

| Saving                              | Effective/mo | Notes |
| ----------------------------------- | -----------:| ----- |
| Main site, chore/docs builds        | $10.94       | 75% of $14.58 |
| `_deploy_sync`, chore/docs builds   | $11.10       | 75% of $14.80 |
| **Total Fix A**                     | **$22.04/mo**|       |

Equivalent in billed terms: roughly **$16/mo** off the next invoice.

Combined with the GitHub Actions `paths-ignore` change from earlier
(saves ~$14/mo on Actions), the founder-os noise problem is now
closed off on both build systems.

## Recommended follow-ups

User-only decisions. Do **not** apply these without explicit sign-off.

### 1. Delete the `_deploy_sync` project — another ~$15/mo

`_deploy_sync` (`prj_NgEyORBHgaUVs867LtHN6RtU8DUq`) is hooked to the
**same GitHub repo + `master` branch** as the main site. Every push
triggers a duplicate build. It serves `deploysync.vercel.app` which
appears to be a leftover from an early deploy test.

To delete:

```powershell
$env:VERCEL_TOKEN = "<token>"
Invoke-WebRequest `
  -Uri "https://api.vercel.com/v9/projects/_deploy_sync?teamId=team_KZnVXkwWtzHaT0EfltPcy3LE" `
  -Method DELETE `
  -Headers @{ Authorization = "Bearer $env:VERCEL_TOKEN" }
```

Risk: if anything is pointing at `deploysync.vercel.app`, it will go
dark. Quick check (run before deleting):

```powershell
# Should return traffic to the main domain, not deploysync.vercel.app
Invoke-WebRequest "https://doxed-founders-website.vercel.app/" -MaximumRedirection 0
```

After Fix A, `_deploy_sync` is already saving ~$11/mo; deleting it
saves the remaining ~$3.70 of code-build cost plus eliminates the
duplicate-build queue contention.

### 2. Consider Pause Projects (Spend Management circuit breaker)

Currently the team has:

- On-Demand Budget: $53.68 / $200 (27%)
- Notifications: ON
- Pause Projects: **OFF**

Enabling Pause Projects would halt all non-manual deployments when
the on-demand budget hits 100%, instead of charging overage. This is
conservative — it could block a critical production fix at month-end.
Worth enabling once you're confident monthly burn is stable.

To enable via UI: **Settings → Billing → Spend Management → Pause Projects → On**.

No public REST API for this toggle as of 2026-07.

### 3. Don't downgrade to Hobby (yet)

The user mentioned considering Hobby ($0/mo). Reasons not to, today:

- Pro is needed for the **commit-attribution block** that's currently
  protecting production from random Cursor Agent pushes. Hobby doesn't
  enforce it.
- Pro includes the $20 credit, which absorbs ~25% of current effective
  spend. Net cost difference is smaller than it looks.
- Commercial use of Hobby is against Vercel ToS — this site has ads
  and revenue, so Hobby isn't an option.

Revisit once Fix A + (optional) `_deploy_sync` deletion stabilise the
monthly burn under $20 effective.

## Verification commands

Re-run this audit any time:

```powershell
$env:VERCEL_TOKEN = "<token>"
$teamId = "team_KZnVXkwWtzHaT0EfltPcy3LE"

# Pull FOCUS billing data for the current period
$from = "2026-06-17T00:00:00.000Z"
$to   = ([DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddT00:00:00.000Z"))
& curl.exe -sS -N -H "Authorization: Bearer $env:VERCEL_TOKEN" `
  "https://api.vercel.com/v1/billing/charges?from=$from&to=$to&teamId=$teamId" `
  -o charges.jsonl

# Sum by service
Get-Content charges.jsonl | ForEach-Object { $_ | ConvertFrom-Json } |
  Group-Object ServiceName |
  ForEach-Object { [PSCustomObject]@{
    Service = $_.Name
    Effective = ($_.Group | Measure-Object EffectiveCost -Sum).Sum
  }} | Sort-Object Effective -Descending | Select-Object -First 5
```

## Change log

| Date       | Change                                                              | Commit     |
| ---------- | ------------------------------------------------------------------- | ---------- |
| 2026-07-08 | Added `scripts/vercel-ignore-build.sh` + configured on both projs   | `707b784b` |
| 2026-07-08 | Verified live: docs-only commit correctly CANCELED by ignore step   | (probe `ba5ea005`, reverted `101ebcc9`) |
