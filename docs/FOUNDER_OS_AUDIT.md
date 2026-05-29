# Doxxed Crypto / Founder OS — Security & Architecture Audit Report

**Last updated:** May 2026  
**Scope:** Repository architecture, product vision, security posture, UX direction  
**Audience:** Founders, contributors, Cursor agents, external reviewers  

---

## Executive Summary

Founder OS has evolved from a founder verification platform into a **startup operating system** for crypto-native founders.

Current vision includes:

- Founder verification (trust without oversharing)
- Build-in-public workflow (GitHub → translate → publish everywhere)
- AI provider orchestration (BYOK + OpenHands remote agent)
- Community engagement & reputation graph
- Simulated fundraising (Raise Room)
- Proof of Conviction (public reasoning on paper trades)
- Agent marketplace (ecosystem feature, not homepage hero)
- Launch preparation & demand validation

**Overall assessment**

| Dimension | Score | Notes |
|-----------|-------|-------|
| Architecture | 9/10 | Provider abstraction, GitHub-centric workflow, modular API |
| Security | 7.5/10 | Credential encryption exists; JWT hardening & OAuth GitHub still needed |
| Scalability | 9/10 | Neon Postgres, Railway API, event hooks in place |
| Product vision | 9.5/10 | Differentiated thesis: build → validate → launch |
| User experience | 7/10 → improving | Homepage/nav simplified; mission control shipped |
| Long-term moat | 9/10 | Reputation graph + build history + demand validation |

**Strongest moat:** Not AI alone — it is the **reputation graph**, **build history**, **community trust**, **demand validation**, and **founder transparency**.

**Biggest remaining challenge:** Making the product immediately understandable to first-time visitors. Vision in the codebase is ahead of what casual users see on first load.

---

## Product Thesis

> Build first. Prove execution publicly. Validate demand. Raise later.

Founder OS should communicate in five seconds:

1. **Build** — GitHub, Copilot, agents, publish everywhere  
2. **Validate** — Raise Room, scout markets, Proof of Conviction  
3. **Launch** — Verified founders, launch readiness, live tokens  

Everything else (trade terminal, points, leaderboard, watchlist) belongs **inside dashboards**, not primary navigation.

---

## Architecture (Current Direction)

```
Founder
  ↓
Founder OS (orchestration + project memory)
  ↓
Provider layer (OpenAI, Anthropic, Gemini, OpenHands, rule-based)
  ↓
GitHub · Vercel · Railway · Neon · X
  ↓
Community · Raise Room · Launch infrastructure
```

**Correct decisions**

- Removed desk copy-paste providers (Cursor, Claude Code, etc.) from primary UX
- OpenHands as true remote agent via URL + API key
- Founder Brain + Scout Markets on project rooms
- Story-style Proof of Conviction shares (thesis, catalyst, target, horizon)
- Platform activity feed on homepage (real actions vs vanity metrics)

---

## Critical Findings

### C-1 — JWT Secret Fallback

**Risk:** HIGH  

If `JWT_SECRET` is missing, some builds may fall back to a development secret → predictable signing, session forgery.

**Recommendation:** Fail application startup when `JWT_SECRET` is unset in production. No fallback values in production builds.

---

## High Findings

### H-1 — Credential Hub Risk

Founder OS stores:

- OpenAI / Anthropic / Gemini API keys  
- GitHub tokens  
- OpenHands agent credentials  

**Status:** Keys encrypted at rest via `CredentialsCryptoService` (AES-GCM pattern).  

**Recommendation:** Key rotation support, encryption key separate from DB infrastructure, audit access to decrypt paths.

### H-2 — Security Center

**Status:** Partially built — `/settings/security` includes passkeys, TOTP, wallet, recovery codes.

**Missing:** Session management, device list, login history, prominent Security Center entry in founder journey.

### H-3 — GitHub Personal Access Token UX

**Status:** Manual PAT entry today.

**Recommendation:** GitHub OAuth App — scoped permissions, easier onboarding, less support burden.

---

## Medium Findings

### M-1 — Virtual Economy Farming

Economy includes Founder Credits, paper dollars, reputation, daily lottery, scout votes.

**Risk:** Activity farming (comments, vote rings, follow exchanges).

**Recommendation:** Shift weight toward **quality score** — helpful marks, founder endorsements, correct predictions, long-term participation.

### M-2 — Founder Copilot Persistent Context

**Status:** Implemented — `/copilot/memory`, `/copilot/resume`, mission control UI with “Continue where I left off”.

**Recommendation:** Deepen event-driven updates so memory refreshes on every GitHub commit and deploy without manual sync.

### M-3 — Event Bus Maturity

Integrations exist; not fully event-driven across feed, reputation, notifications, launch readiness.

**Recommendation:** Central events — `GITHUB_COMMIT`, `DEPLOY_SUCCESS`, `RAISE_ALLOCATION`, `AGENT_RUN`, `COMMUNITY_MILESTONE` — fan out to feed, analytics, notifications.

---

## Low Findings

### L-1 — Homepage Information Density

**Status:** Addressed in May 2026 — Build / Validate / Launch pillars, live activity feed, reduced CTAs.

### L-2 — Navigation Complexity

**Status:** Addressed — primary nav: Discover, Projects, Build feed, Founder OS, Raise Room, Agents. Secondary items in **More** menu.

---

## UX Roadmap (Implemented vs Next)

| Item | Status |
|------|--------|
| Homepage Build → Validate → Launch | ✅ Shipped |
| Live activity feed | ✅ Shipped |
| Nav simplification | ✅ Shipped |
| Raise Room public page | ✅ Shipped |
| Stage color system (blue/yellow/green/purple) | ✅ Shipped |
| Founder OS mission control | ✅ Shipped |
| Copilot “Continue where I left off” | ✅ Shipped |
| Founder Brain “Ask this project” on every project | ⚠️ On project room; surface more prominently |
| Event bus full realization | 🔲 Planned |
| GitHub OAuth | 🔲 Planned |
| JWT fail-fast | 🔲 Planned |

---

## Mission Control Layout (Founder OS)

Default screen at `/founder-den`:

1. **Current project** — stage, progress %, last commit, stack status, demand, followers  
2. **Founder Copilot** — welcome back, current goal, progress, remaining tasks, suggested next step, **Continue where I left off**  
3. **Continue building** — open tasks from build queue  
4. **Founder feed** — recent build posts  
5. **Raise Room** — allocation snapshot  
6. **Agents** — link to workspace + marketplace  
7. **Founder inbox** — build queue, agent results, community, funding  

---

## Strategic Priorities

1. **Security hardening** — JWT fail-fast, GitHub OAuth, session management  
2. **Event bus** — single pipeline for commits → feed → reputation  
3. **Founder Brain** — “Ask this project” as default project entry point  
4. **Raise Room** — auto-resolve scout markets, weekly stipend cron (if product confirms)  
5. **Agent marketplace** — ecosystem feature, not homepage comparison to agent launchpads  
6. **Prediction markets** — Phase 7+ scout markets expansion  

---

## Security Messaging (Product)

**Trust without oversharing** — verify founders without exposing passport images, home addresses, or KYC document dumps. Position against identity database leak risk in crypto ecosystems.

We verify:

- ✅ Verified identity (public profile)  
- ✅ Verified human (video / interview)  
- ✅ GitHub & build proof  

We do **not** publish:

- ❌ Private KYC documents  
- ❌ Home address  
- ❌ Wallet seed phrases  

---

## Final Verdict

Founder OS is no longer a founder directory. It is becoming the **operating layer** for crypto-native startups — connecting founders, builders, traders, agents, and launch infrastructure.

The architecture is ahead of the packaging. The next leap is not another feature — it is **clarity**: one sentence in five seconds, one obvious next action in mission control, and a story users can retell.

---

## Related Docs & Entry Points

| Area | Path |
|------|------|
| Founder OS UI | `/founder-den` |
| Builder settings | `/settings/builder` |
| Security center | `/settings/security` |
| Raise Room (public) | `/raise-room` |
| Project room + Founder Brain | `/project/[slug]` |
| API health | `/api/health` |

*This document contains no secrets, credentials, or environment values. Do not commit `.env` files or vault contents.*
