# Founder OS North Star

Founder OS is **not** a status dashboard, GitHub viewer, or integration launcher.

It is the **primary command center** for founders: think, research, plan, build, deploy, publish, and market from **one browser tab**.

## Architecture layers (build order)

| Layer | Status | Purpose |
|-------|--------|---------|
| **Context Assembly** | P0 shipped (v1) | Commits, PRs, deploys, themes, mission graph → every Founder Brain prompt |
| **Commit Intelligence** | P0 shipped (v1) | Group commits by initiative; surface outcomes not sync noise |
| **Mission State Engine** | P0 shipped (v1) | Dynamic initiative, blocker, impact, next step, confidence |
| **Agent Runtime** | P1 | Stream builder steps in-OS; Cursor as worker not destination |
| **Desktop Bridge** | P2 | Optional awareness of local IDE tabs / unsaved work |
| **Money Feed** | Enforced | `/feed` = capital flow only; builds/commits in Founder OS & Discover |

## Founder Brain

- **User sees:** one chat — Founder Brain.
- **System routes:** research · build · content · strategy (no manual agent picker required).
- **Never answer** from `tasks.json` titles alone when GitHub context exists.

## Builder Agent

- Stream status in chat: plan → branch → PR → deploy.
- External Cursor link = optional diff view only.
- “Take full control” → **Run platform autopilot sync** until true agent runtime exists.

## Layout

- **Left (~70%):** Command Center chat + quick actions.
- **Right (~30%):** Mission intelligence, mission state, deploy/agent status.

## Feed

Feed answers: **Where is money flowing?**

Not: what commit happened (those live in Founder OS / project build log / Social Hub).

See `packages/utils/src/money-feed.ts` and `docs/SPRINT_7B_FEED.md`.

## Full command-center plan

OS differentiators (Agent Bus, Founder Queue, Attention Center, Timeline, Deploy Intelligence):  
**[FOUNDER_OS_COMMAND_CENTER_ARCHITECTURE.md](./FOUNDER_OS_COMMAND_CENTER_ARCHITECTURE.md)**
