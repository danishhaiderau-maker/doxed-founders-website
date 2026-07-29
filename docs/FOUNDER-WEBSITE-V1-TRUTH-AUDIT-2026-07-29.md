# Founder Website V1 Truth Audit

Date: 2026-07-29

This audit compares the active Founder website source with the locally proven
Founder IDE V1 product. It is a source and automated-contract audit. It does
not claim that the changes are deployed or that signed-in production
connections have been visually proven.

## V1 product boundary

The website must present:

- one desktop application named Founder IDE;
- Founder Node as an embedded background capability, not a second
  founder-facing application;
- one default Founder AI backed by the managed policy;
- Personal AI and local Ollama as optional founder-controlled routes;
- Founder Free, Builder, and Team allowances from live entitlement data;
- truthful device, connection, route, quota, and cost states;
- no savings percentage or currency claim without a named measured baseline.

## Primary Founder routes

Keep these routes as the V1 workflow:

| Route | Purpose | V1 status |
| --- | --- | --- |
| `/founder-os` | Focused workspace overview | Keep |
| `/account` | Profile, security, plan, inbox | Keep |
| `/settings/builder` | Founder IDE, Personal AI, infrastructure, security | Keep and simplify |
| `/settings/ai-usage` | Exact Founder AI usage and named references | Keep |
| `/downloads` | Founder IDE and mobile downloads | Keep |
| `/phone` | Remote device/workspace control | Keep; production E2E open |
| `/founder-id/authorize` | Explicit device approval | Keep |
| `/admin/control` | Admin-only operational controls | Keep behind role checks |

`/settings/integrations` remains a compatibility alias into the consolidated
settings surface. It should not become a duplicate settings implementation.

Community, project, token, agent-marketplace, research, and trading routes are
valid wider-platform products, but they are not allowed to crowd the primary
Founder V1 navigation or be used as evidence that the IDE workflow is
complete.

## Corrections implemented

Source commit: `bf7c34d8`

- Replaced user-facing `Founder Stack` with `Founder IDE`.
- Replaced user-facing `Founder Copilot` with `Founder AI`.
- Renamed the settings AI tab to `Personal AI` while keeping managed Founder
  AI as the default.
- Changed the downloads flow to describe one Windows Founder IDE application
  with embedded Founder Node.
- Moved standalone Founder Node downloads under a collapsed legacy
  compatibility disclosure.
- Removed false macOS/Linux Founder IDE download labels. Those platforms remain
  release gates.
- Labelled the desktop candidate as an unsigned internal beta instead of a
  public-ready release.
- Replaced `Cursor Pro saved` with a named retail reference and an explicit
  statement that it is not guaranteed savings.
- Aligned onboarding, remote, agent, landing, settings, and admin copy with the
  one-app/one-AI product language.
- Removed stale imports exposed by the terminology cleanup.

No API, billing, wallet, token, trading, analyzer, relay, exchange, or
production-gate behavior changed in this slice.

## Automated evidence

- Changed TypeScript/TSX syntax: 27 files passed.
- Full web no-output TypeScript check with incremental writes disabled: passed.
- Founder shell visual-QA predicate tests: 6 passed, 0 failed.
- Founder remote provider-label tests: 3 passed, 0 failed.
- Founder workspace responsive visual harness: 3 screens, 0 failures.
- Downloads responsive visual harness: 2 screens, 0 failures.
- Downloads hydration stability regression: 1 passed, 0 failed.
- Whitespace check: passed.
- Source search for `Founder Stack`, `Founder Copilot`, and
  `Cursor Pro saved`: zero matches in `apps/web/src`.

The first full typecheck correctly found three unused imports after the copy
cleanup. They were removed before the final passing check. The no-output check
disabled incremental metadata writes because this Windows worktree does not
permit writing `tsconfig.tsbuildinfo`; no generated file was deleted or
permission weakened.

## Admin control classification

The current admin workspace contains AI Keys, Builders, Research Dashboard,
Social Messaging, Platform & Treasury, and Moderation.

- **AI Keys:** keep admin-only; a saved key is not route eligibility until an
  authenticated provider/model probe succeeds.
- **Builders:** keep; it is operational account and entitlement administration.
- **Research Dashboard:** preserve under its owning research/trading
  workstream; do not mix it into Founder IDE release evidence.
- **Social Messaging:** preserve as a platform operation, not a core IDE
  setting.
- **Platform & Treasury:** preserve as admin-only; design-complete token
  contracts are not mainnet execution.
- **Moderation:** preserve as admin-only platform safety.

No admin control was deleted during this audit. Removal requires route usage,
API ownership, data-retention, and production-behavior evidence plus explicit
approval.

## Still open

1. Extend the passing Founder workspace and downloads visual proof to account,
   settings, usage, phone, authorization, and admin, including loading, empty,
   error, offline, reduced-motion, and success states. Current evidence:
   `FOUNDER-WEBSITE-VISUAL-QA-EVIDENCE-2026-07-29.md`.
2. Verify plan, quota, managed AI, Personal AI, and device status against the
   production APIs after an approved deployment.
3. Prove GitHub, Vercel, Railway, and Neon connect, health, reconnect, and
   revoke flows with least-privilege credentials.
4. Implement and prove email and Telegram connection flows; source copy must
   not imply they already exist.
5. Complete production website-to-installed-Founder-IDE remote action,
   approval, receipt, revoke, and reconnect evidence.
6. Apply the final Apple-inspired visual system after product truth and route
   hierarchy are frozen. Visual polish must not hide unsupported behavior.

Until these checks pass, Stage 10 has a truthful source baseline but is not
production-complete.
