# Deployment Modes — Private · Public · Hybrid

> **Status:** Design spec · Founder OS Phase 7
> **Owner:** Danish + Founder OS core
> **Last updated:** 2026-07-11
> **Question answered:** How does a founder choose where their project runs, and how do we make all three modes feel like "real building"?

---

## TL;DR

Every new project in Founder OS asks one question, exactly once:

> **Where should this project live?**
>
> 🖥 **Private** — On my laptop. Free. Mine.
> ☁️ **Public** — On GitHub + Vercel + Neon. Ready for users.
> 🔀 **Hybrid** — Build privately, auto-mirror to public when I ship.

That single choice drives every downstream default (git remote, database provider, hosting URL, phone route, cost), and **the founder can change it at any time** without rewriting a single line of code. Hybrid is the recommended default because most founders want privacy while brainstorming and a clean "go live" switch when ready.

This document is the authoritative UX + architecture spec for the three modes. Implementation touches the project setup wizard, Founder Node, the Founder Stack installer, and the dashboard. Research backing lives in [LOCAL-FIRST-INFRASTRUCTURE-RESEARCH.md](./LOCAL-FIRST-INFRASTRUCTURE-RESEARCH.md).

---

## 1. The three modes at a glance

| | 🖥 **Private** | ☁️ **Public** | 🔀 **Hybrid** *(recommended)* |
|--|----|----|----|
| **Compute** | Founder's laptop (Founder Node) | Cloud (Vercel + Railway) | Laptop now, cloud on publish |
| **Git** | Local Forgejo at `localhost:3000` | GitHub | Forgejo now, mirror-push to GitHub on publish |
| **Database** | SQLite file (`dev.db`) | Neon Postgres | SQLite now, one-command migrate to Neon on publish |
| **Hosting URL** | `https://<tunnel>.trycloudflare.com` on demand | `https://app.foundersdomain.com` | Tunnel now → Vercel on publish |
| **Phone remote** | Tailscale mesh (direct) | Public URL (via cloud) | Tailscale now, public URL on publish |
| **AI Gateway** | Founder OS cloud (always cloud — it is the moat) | Founder OS cloud | Founder OS cloud |
| **Cost** | **$0/month** forever | Free tier → usage-based | $0 until you publish |
| **Visibility** | Only you | The world | Private now, public when you flip the switch |
| **Best for** | Brainstorming, prototyping, validating, learning | Launched products with real users | The realistic 90% case — build privately, launch publicly |

### Why Hybrid is the recommended default

Most founders' real journey is:

```
Week 1–8:  "I have an idea, let me prototype privately."
Week 9–12: "I want to share it with 5 friends for feedback."
Week 13+:  "I'm ready to launch, put it on a real domain."
```

Hybrid mode matches that curve exactly. The founder never has to "migrate" or "re-platform" — they just press **Publish** and Founder OS runs the Private → Public promotion (git mirror, DB migrate, Vercel deploy) in one orchestrated flow. Nothing is rewritten. Nothing is lost.

---

## 2. The setup-wizard UX

The mode selector appears **once**, at project creation, as a full-screen three-card choice. It is the second step of the project setup wizard, right after naming the project.

### Wireframe

```
┌─────────────────────────────────────────────────────────────────────────┐
│  New Project · "my-saas-app"                                            │
│                                                                          │
│  Where should this project live?                                         │
│  You can change this at any time. Nothing is locked in.                  │
│                                                                          │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐             │
│  │   🖥 Private    │  │   ☁️ Public     │  │   🔀 Hybrid    │             │
│  │                │  │                │  │  ⭐ Recommended │             │
│  │  On your       │  │  On GitHub +   │  │  Build private │             │
│  │  laptop.       │  │  Vercel + Neon.│  │  → publish to  │             │
│  │  Free forever. │  │  Ready for     │  │  cloud when    │             │
│  │  $0/month.     │  │  users.        │  │  ready.        │             │
│  │                │  │                │  │                │             │
│  │  [Choose]      │  │  [Choose]      │  │  [Choose]      │             │
│  └────────────────┘  └────────────────┘  └────────────────┘             │
│                                                                          │
│  What you get in every mode:                                             │
│  ✓ AI Gateway (GLM 5.2 / DeepSeek routing)   ✓ Real git history         │
│  ✓ Real database (SQLite or Postgres)        ✓ Real HTTPS URL           │
│  ✓ Phone remote control                      ✓ Memory Engine            │
│                                                                          │
│  Learn more: Private mode · Public mode · Hybrid mode                    │
└─────────────────────────────────────────────────────────────────────────┘
```

### Design principles

1. **No toy language.** Every mode says "real database," "real git history," "real HTTPS URL." The word "playground" never appears. The word "demo" never appears. This is professional infrastructure that happens to run in different places.
2. **No grayed-out buttons.** Every feature works in every mode. The difference is *where* it runs, not *whether* it runs.
3. **The recommendation is explicit.** Hybrid has a "⭐ Recommended" badge because most founders want it. We do not pretend the three modes are equal — we guide toward the right default.
4. **"Change at any time" is repeated.** The biggest fear with choosing a mode is "what if I pick wrong?" The answer is always: you can flip it later. This is literally true because of the standards-based stack (git, Prisma, standard web frameworks).

---

## 3. The project dashboard — mode badge + flip switch

Once a project is created, its mode is shown as a persistent badge in the project dashboard header. Clicking the badge opens the **mode panel** where the founder can see what's running, switch modes, or trigger a publish.

### Mode badge

```
┌─────────────────────────────────────────────────────────────────┐
│  my-saas-app                                  🔀 Hybrid →       │
├─────────────────────────────────────────────────────────────────┤
│  ...rest of dashboard...                                         │
└─────────────────────────────────────────────────────────────────┘
```

The badge color encodes the mode:
- 🖠 gray/blue = Private
- ☁️ green = Public
- 🔀 purple = Hybrid (with a tiny "→ publish" hint)

### Mode panel (opened by clicking the badge)

```
┌─────────────────────────────────────────────────────────────────┐
│  Deployment Mode · my-saas-app                              [×]  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Current mode: 🔀 Hybrid (Private, not yet published)            │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ What's running right now                                 │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  Git        Forgejo @ localhost:3000    ● online         │    │
│  │  Database   SQLite (dev.db, 2.1 MB)     ● online         │    │
│  │  Hosting    Tunnel off                  ○ on demand      │    │
│  │  Phone      Tailscale direct            ● ready          │    │
│  │  AI         Founder OS Gateway          ● routed         │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Switch mode                                              │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  ○ 🖠 Private   — everything on laptop, $0/mo            │    │
│  │  ● 🔀 Hybrid    — private now, publish when ready        │    │
│  │  ○ ☁️ Public    — flip to full cloud now                  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Publish to public cloud                                  │    │
│  │                                                          │    │
│  │ Mirrors your Forgejo repo → GitHub                       │    │
│  │ Migrates SQLite → Neon Postgres                          │    │
│  │ Deploys to Vercel                                        │    │
│  │ Assigns your domain                                      │    │
│  │                                                          │    │
│  │ Your code, history, and data all move with you.          │    │
│  │ Nothing is rewritten.                                    │    │
│  │                                                          │    │
│  │ Connected accounts:                                      │    │
│  │   GitHub  ● @your-handle connected                       │    │
│  │   Vercel  ● doxxedcrypto.digital connected               │    │
│  │   Neon    ● ep-fragrant-unit... connected                │    │
│  │                                                          │    │
│  │              [ 🚀 Publish my-saas-app ]                  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### What each button does

| Button | What happens |
|--|--|
| **Tunnel on/off** | Spins up `cloudflared tunnel --url http://localhost:3000` (Private/Hybrid only). Returns a public HTTPS URL on demand. |
| **Switch mode** | Changes the project's mode flag. If switching to Public, shows the Publish panel. If switching from Public → Private, warns that the production deploy will stop but data is preserved. |
| **Publish** | Runs the orchestrated Private → Public flow: (1) add GitHub remote + push, (2) `prisma migrate` SQLite → Neon, (3) trigger Vercel deploy, (4) verify health, (5) flip project to Public mode. Shows live progress. |

---

## 4. What each mode actually wires up (backend truth)

The mode is stored on `Project.deploymentMode` (`PRIVATE` | `PUBLIC` | `HYBRID`). A `ProjectDeploymentConfig` model stores the concrete connection details per project.

### Private mode wiring

```typescript
// On project creation in Private mode:
{
  deploymentMode: 'PRIVATE',
  gitBackend: 'forgejo',
  gitUrl: 'http://localhost:3000/founder/my-saas-app.git',
  dbProvider: 'sqlite',
  dbUrl: 'file:./projects/my-saas-app/dev.db',
  hostingType: 'tunnel-on-demand',
  hostingUrl: null, // null until founder clicks "Share"
  phoneRoute: 'tailscale',
  aiGateway: 'founder-os-cloud', // always
}
```

Founder Node is responsible for:
- Initializing the Forgejo repo (or plain local git if Forgejo isn't installed)
- Creating the SQLite file + running `prisma db push`
- Starting the dev server on `localhost:<port>`
- Spinning up `cloudflared` on demand when the founder clicks "Share"
- Advertising itself on Tailscale so the phone can find it

### Public mode wiring

```typescript
{
  deploymentMode: 'PUBLIC',
  gitBackend: 'github',
  gitUrl: 'git@github.com:founder/my-saas-app.git',
  dbProvider: 'postgresql',
  dbUrl: 'postgresql://...', // Neon connection string (secret)
  hostingType: 'vercel',
  hostingUrl: 'https://my-saas-app.vercel.app',
  phoneRoute: 'public-url',
  aiGateway: 'founder-os-cloud',
}
```

Public mode is what we already do today for `doxxedcrypto.digital` itself: push to GitHub → Vercel deploys → Neon holds the data. The only new piece is that the same project could have come from Private mode and carries its git history forward.

### Hybrid mode wiring

```typescript
// Hybrid starts as Private, with a "publish plan" attached:
{
  deploymentMode: 'HYBRID',
  // current runtime = Private config:
  gitBackend: 'forgejo',
  gitUrl: 'http://localhost:3000/...',
  dbProvider: 'sqlite',
  dbUrl: 'file:./projects/.../dev.db',
  hostingType: 'tunnel-on-demand',
  hostingUrl: null,
  phoneRoute: 'tailscale',
  aiGateway: 'founder-os-cloud',
  // publish plan = Public config, applied on Publish:
  publishPlan: {
    targetGithubRepo: 'founder/my-saas-app',
    targetNeonProject: 'my-saas-app-prod',
    targetVercelProject: 'my-saas-app',
    targetDomain: 'my-saas-app.foundersdomain.com',
  },
}
```

Hybrid is Private + a pre-filled publish plan. When the founder clicks Publish, Founder Node orchestrates the migration using the publish plan so there are zero decisions to make at launch time.

---

## 5. The Publish flow (Hybrid → Public)

This is the single most important flow in Phase 7. It must feel magical.

### Step-by-step (what the founder sees)

```
┌─────────────────────────────────────────────────────────────────┐
│  Publishing my-saas-app to the public cloud                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ✓ Step 1 of 4 — Mirroring git history to GitHub                │
│    Pushed 247 commits to founder/my-saas-app                    │
│                                                                  │
│  ✓ Step 2 of 4 — Migrating database to Neon                     │
│    Converted SQLite → Postgres, applied migration, loaded data  │
│    12 tables, 4,318 rows transferred                            │
│                                                                  │
│  ● Step 3 of 4 — Deploying to Vercel                            │
│    Building... (this takes ~60 seconds)                          │
│                                                                  │
│  ○ Step 4 of 4 — Verifying health                               │
│                                                                  │
│  ─────────────────────────────────────────────────              │
│  Your code, history, and data all moved with you.               │
│  Nothing was rewritten.                                          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Step-by-step (what actually happens)

1. **Git mirror.** `git remote add github git@github.com:...` → `git push github main`. If Forgejo is installed, optionally configure auto-mirror-push so future commits flow through.
2. **DB migrate.**
   - Update `schema.prisma` `provider = "postgresql"`.
   - `npx prisma migrate dev --name init_postgresql` against the local SQLite to generate migration SQL.
   - Apply migration to Neon: `npx prisma migrate deploy` with `DATABASE_URL` pointed at Neon.
   - Transfer data: use `pgloader ./dev.db $NEON_URL` or a Prisma-based row copier for founders who don't have pgloader installed.
3. **Vercel deploy.** Triggered via the Vercel API (we already have `scripts/vercel-deploy.mjs`). Wait for `READY`.
4. **Health verify.** Hit the production URL's `/api/health/live`. If green, flip `Project.deploymentMode` to `PUBLIC` and update the dashboard badge.

### Rollback

If any step fails, the project stays in Hybrid/Private mode and the dashboard shows a clear error with a retry button. The local Forgejo repo and SQLite file are untouched — the founder can always continue building privately and try publishing again later.

If the founder wants to *unpublish* (go back to Private after going Public), the flow is:
1. Stop the Vercel deploy (or leave it running as staging).
2. Reverse-migrate the DB (optional; most founders keep the Neon DB as a backup).
3. Flip `deploymentMode` back to `PRIVATE` or `HYBRID`.
4. The local repo is still there with full history.

---

## 6. Phone remote — how it adapts per mode

| Mode | How the phone reaches the laptop |
|--|--|
| **Private** | Tailscale mesh. Phone opens `http://founder-laptop.tailnet.ts.net:7002/phone`. Direct WireGuard, no cloud in the path. Works from anywhere on earth. |
| **Public** | Phone opens the public Vercel URL's `/phone` route. Auth via the cloud JWT. |
| **Hybrid** | Tailscale now (same as Private). After publish, both routes work — phone can use either. |

The phone UI auto-detects the project's mode and shows the right URL. The founder never has to configure this manually.

---

## 7. The Founder Stack installer — mode-aware setup

The Founder Stack installer (`packages/founder-ide/installer/founder-stack.iss`) gains a **mode selection** screen that mirrors the project setup wizard. This determines which optional components get installed.

### Installer screen

```
┌─────────────────────────────────────────────────────────────────┐
│  Founder Stack Setup                                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Which mode will you primarily use?                             │
│  (You can change this per-project later)                        │
│                                                                  │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐     │
│  │   🖠 Private    │  │   ☁️ Public     │  │   🔀 Hybrid    │     │
│  │                │  │                │  │  ⭐ Default    │     │
│  │  Installs:     │  │  Installs:     │  │  Installs:     │     │
│  │  • Forgejo     │  │  • Nothing     │  │  • Forgejo     │     │
│  │  • cloudflared │  │    extra       │  │  • cloudflared │     │
│  │  • Tailscale   │  │    (you        │  │  • Tailscale   │     │
│  │    (optional)  │  │    already     │  │    (optional)  │     │
│  │                │  │    have these) │  │                │     │
│  │  Disk: +155 MB│  │  Disk: +0 MB   │  │  Disk: +155 MB│      │
│  │  RAM: +600 MB │  │  RAM: +0 MB    │  │  RAM: +600 MB │      │
│  └────────────────┘  └────────────────┘  └────────────────┘     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

The installer remembers the choice as a default for new projects, but every project can override it in the setup wizard.

---

## 8. Honest limitations (we tell founders upfront)

### Private mode limitations

- **Your laptop must be online for the tunnel URL to work.** If your laptop sleeps, the public URL goes down. This is fine for sharing work-in-progress; it is not fine for production traffic. That is exactly what Public mode is for.
- **Single-writer database.** SQLite allows many concurrent readers but only one writer at a time. For a solo founder this never matters. For a team, it can. Public mode (Postgres) has no such limit.
- **No Postgres extensions.** If your app needs `pgvector`, `PostGIS`, or stored procedures, you need Postgres (Public mode or local Postgres). SQLite does not have these.
- **No branching.** Neon's database branching (clone a DB for a preview environment) is a cloud feature. Private mode has one linear database.

### We do NOT hide these

The mode panel shows an "Honest limitations" expandable section. Being upfront about what Private mode *cannot* do is how we earn trust. Hiding limitations is what toy platforms do.

---

## 9. Why this design works (the strategic argument)

### For founders

- **Zero friction to start.** Default to Hybrid, start building in 30 seconds, $0 cost.
- **No re-platforming.** The standards-based stack (git, Prisma, web framework) means Publish is a configuration change, not a rewrite.
- **Honest about cost.** The dashboard always shows the current monthly cost. Private = $0. Public = whatever the cloud providers charge (pulled from their billing APIs).
- **Real infrastructure.** Forgejo, SQLite, Cloudflare Tunnel are the same tools indie hackers run in production. Founder OS just bundles them.

### For Founder OS (the business)

- **The moat holds in every mode.** The AI Gateway, Memory Engine, Routing Engine, and Flight Recorder are always cloud-side. Even in Private mode, the founder's AI runs through our Gateway. The intelligence compounds on our servers; only the *compute and storage* moves.
- **Private mode is a funnel to Public mode.** Founders build privately for free, hit "this is great, let me launch," and click Publish. The DEX fees on token launch (Phase 8) are where we monetize — Private mode just gets them to that moment without burning cloud budget.
- **Hybrid mode is our differentiated UX.** No competitor offers a single-click Private → Public publish flow that preserves code, history, and data. Replit locks you to their cloud. Bolt/Lovable lock you to their stack. Founder OS lets you own your infrastructure and choose where it runs.

---

## 10. Implementation scope (Phase 7)

This doc is the spec. The engineering work splits into three slices:

### Slice 7.1 — Backend: `Project.deploymentMode` + config models

- Prisma: add `deploymentMode` enum to `Project`, add `ProjectDeploymentConfig` model
- `apps/api/src/deployment-modes/` — service to read/update mode, generate publish plan
- Founder Node: endpoints to (a) report current Private-mode runtime status (Forgejo/SQLite/tunnel), (b) trigger Share (tunnel on), (c) trigger Publish

### Slice 7.2 — Frontend: mode selector + dashboard badge + publish flow UI

- `apps/web/src/components/deployment-modes/mode-selector.tsx` — the three-card wizard step
- `apps/web/src/components/deployment-modes/mode-badge.tsx` — persistent dashboard badge
- `apps/web/src/components/deployment-modes/mode-panel.tsx` — the full panel with switch + publish
- `apps/web/src/components/deployment-modes/publish-progress.tsx` — the live 4-step progress

### Slice 7.3 — Founder Node: Private-mode runtime orchestration

- Founder Node gains a `DeploymentModeService` that, for Private/Hybrid projects:
  - Initializes Forgejo repo (or plain git if Forgejo missing)
  - Creates SQLite file + runs `prisma db push`
  - Manages `cloudflared` process lifecycle (start/stop tunnel)
  - Reports runtime status to the cloud API for the dashboard panel
- For Publish, Founder Node drives the migration via the existing GitHub/Vercel/Neon credentials in the founder's vault

### Slice 7.4 — Installer: mode-aware component bundling

- Update `packages/founder-ide/installer/founder-stack.iss` with the mode selection screen
- Conditionally download + install Forgejo binary, cloudflared binary, Tailscale (optional)
- Register Forgejo as a Windows service bound to `localhost:3000`

---

## 11. Non-goals (Phase 7 does NOT do)

- **Coolify/Dokploy bundling.** Those are for VPS, not laptop. They are a future "Private+ mode" for founders who outgrow the laptop.
- **Local AI models.** The AI Gateway is always cloud-side in every mode. Running local LLMs is a separate BYO_AI feature, not a deployment-mode concern.
- **Team collaboration features.** Multi-user Private mode (shared Forgejo, shared DB) is a later phase. Phase 7 is solo-founder Private mode.
- **Mobile app packaging.** The phone remote is a web UI, not a native app. Native apps are later.

---

## 12. Related docs

- [LOCAL-FIRST-INFRASTRUCTURE-RESEARCH.md](./LOCAL-FIRST-INFRASTRUCTURE-RESEARCH.md) — the research backing every tool choice here
- [FOUNDER-IDE-VOID-FORK-PLAN.md](./FOUNDER-IDE-VOID-FORK-PLAN.md) — the editor this all runs inside
- [RAISE_ROOM_LAUNCH_FLOW.md](./RAISE_ROOM_LAUNCH_FLOW.md) — Phase 8, where the Public-mode founder launches their token
- [PRODUCT.md](./PRODUCT.md) — "Founder OS exists to compound founder intelligence over time"

---

*Product engineering context only. Not legal or investment advice.*
