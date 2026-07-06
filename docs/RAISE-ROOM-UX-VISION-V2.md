# Raise Room UX Vision v2

**Status:** Vision + phased UI sprints (not a single rebuild)  
**Date:** 6 July 2026  
**Audience:** Product, design, engineering  
**Related:** [RAISE-ROOM-P0-INSPIRED-PLAN.md](./RAISE-ROOM-P0-INSPIRED-PLAN.md), [RAISE-ROOM-VALIDATION-VISION-SPEC.md](./RAISE-ROOM-VALIDATION-VISION-SPEC.md), [ARCHITECTURE-REVIEW-V2-RESPONSE.md](./ARCHITECTURE-REVIEW-V2-RESPONSE.md)

---

## 1. North star

Raise Room should feel like **YC Demo Day × Product Hunt × Apple Events** — not DexScreener, not a memecoin factory.

**Hero promise:** *Discover tomorrow's founders before everyone else.*

Scouts arrive to **witness conviction forming in public**: validators signing off, paper commits stacking, founders crossing compliance gates — with cinematic clarity, not terminal chrome.

---

## 2. Experience pillars (from product feedback)

| Pillar | What it means | Anti-pattern |
|--------|---------------|--------------|
| **Discovery theatre** | Live activity, momentum, social proof | Static tables, DexScreener charts |
| **Founder as protagonist** | Rich project cards = mini landing pages | Ticker + price only |
| **Compliance as story** | Animated compliance timeline (signature feature) | Hidden admin gates |
| **Conviction visible** | Visual conviction meter, tier badges | Opaque numbers |
| **Graduation as keynote** | Full-screen moment when founder graduates | Silent CSV export |
| **Curated exchange** | Founder Exchange — trust metadata, graduated-only | Generic swap UI |

---

## 3. Page wireframes (markdown)

### 3.1 `/raise-room` — Discovery hall

```
┌─────────────────────────────────────────────────────────────────┐
│  [Logo]   Discover tomorrow's founders before everyone else      │
│           Live · 12 projects registering interest today          │
├──────────────────────────────┬──────────────────────────────────┤
│  LIVE ACTIVITY FEED          │  DEMAND HEATMAP (existing)        │
│  ─────────────────────       │  Paper USD + DDollar commits      │
│  Sarah validated Acme…       │  [violet gradient tiles]          │
│  Mike committed $25k paper…  │                                   │
│  Nova cleared Trust Gate…    │                                   │
├──────────────────────────────┴──────────────────────────────────┤
│  FEATURED PROJECT CARDS (horizontal scroll / grid)               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │ Hero image   │ │ Hero image   │ │ Hero image   │             │
│  │ Name · stage │ │ Name · stage │ │ Name · stage │             │
│  │ Conviction ▓ │ │ Conviction ▓ │ │ Conviction ▓ │             │
│  │ Validators 8 │ │ Paper $420k  │ │ Stage 4/6    │             │
│  │ [Enter room] │ │ [Enter room] │ │ [Enter room] │             │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

**Components:** new `raise-room-hero.tsx`, `raise-room-live-feed.tsx`, extend `raise-room-panel.tsx` card chrome.

---

### 3.2 Project room — Raise tab (Apple product page rhythm)

```
┌─────────────────────────────────────────────────────────────────┐
│  [Cinematic hero — founder video / build screenshot]             │
│  ProjectName · Proof Raise · Stage 4 — Raise Room               │
│  One-line thesis (founder voice)                                 │
├─────────────────────────────────────────────────────────────────┤
│  CONVICTION METER (full width)                                   │
│  Paper $680k / $1M  ████████░░  68%   ·  DDollar heat secondary  │
├──────────────────────────────┬──────────────────────────────────┤
│  COMPLIANCE TIMELINE         │  PROOF RAISE PANEL (existing)     │
│  (animated, scroll-linked)   │  `raise-room-panel.tsx`           │
│  ○ Listed                    │  allocate · export · lock slots   │
│  ● Trust validated           │                                   │
│  ◐ Raise window open         │                                   │
│  ○ Graduation                │                                   │
└──────────────────────────────┴──────────────────────────────────┘
```

**Components:** `project-room.tsx` (layout), new `compliance-timeline.tsx`, new `conviction-meter.tsx`, existing `raise-room-panel.tsx`.

---

### 3.3 Founder Graduation — keynote moment

```
┌─────────────────────────────────────────────────────────────────┐
│                    [full viewport, dark stage]                   │
│                                                                  │
│              ✦  Founder Graduation  ✦                            │
│              Acme Protocol · $ACME                               │
│                                                                  │
│     Compliance timeline animates to final checkmark              │
│     Confetti / subtle particle (brand violet, not casino)        │
│                                                                  │
│     [Share moment]  [Open Founder Exchange listing]              │
└─────────────────────────────────────────────────────────────────┘
```

**Trigger:** `FounderGraduationEvent` (Phase 2 backend). UI: `graduation-keynote-modal.tsx`.

---

### 3.4 Founder Exchange (post-graduation)

```
Curated pairs only — graduated projects
Trust badge · integrity score · regulatory class chip
NOT: DexScreener clone, anon pair spam, live bonding curve factory
```

**Components:** future `apps/web/src/app/exchange/` — out of Raise Room sprint scope until RR-UX-009.

---

## 4. Map to existing codebase

| Vision element | Existing asset | Gap |
|----------------|----------------|-----|
| Raise allocation UI | `apps/web/src/components/raise-room-panel.tsx` | Restyle + conviction meter hook |
| Project shell | `apps/web/src/components/project-room.tsx` | Hero + timeline layout |
| Paper raise API | `founder-den.service.ts`, `SimulatedRaise` | Live feed events API |
| Export / snapshot | `exportRaiseParticipants`, `@dcf/utils/raise-room` | Graduation keynote trigger |
| Compliance stages | `ProjectLifecycleStage`, progressive unlock spec | Animated `compliance-timeline.tsx` |
| Trust / validation | Trust Center, `computeTrustWeight` | Feed items for live activity |
| Founder Copilot copy | `founder-copilot.service.ts` | Launch checklist assistant (not mint bot) |
| Heatmap | `/raise-room` page | Side-by-side DDollar + paper |

---

## 5. Phased UI sprints

| Sprint | ID | Deliverable | Depends on |
|--------|-----|-------------|------------|
| 1 | **RR-UX-001** | Hero + tagline on `/raise-room` | None |
| 2 | **RR-UX-002** | Live activity feed (static mock → API) | Events/notifications |
| 3 | **RR-UX-003** | Rich project cards (mini landing) | Project media fields |
| 4 | **RR-UX-004** | Conviction meter component | Raise metrics API |
| 5 | **RR-UX-005** | Compliance timeline (animated) | `compliance-timeline.service` Phase 1.5 |
| 6 | **RR-UX-006** | Project page hero rhythm (Apple-style sections) | RR-UX-003 assets |
| 7 | **RR-UX-007** | Paper + DDollar dual heatmap | TokenLaunch schema Phase 1 |
| 8 | **RR-UX-008** | Graduation keynote modal | `FounderGraduationEvent` |
| 9 | **RR-UX-009** | Founder Exchange curated shell | Phase 4 backend |
| 10 | **RR-UX-010** | Polish pass — motion, a11y, mobile | All above |

**Rule:** Each sprint is shippable behind feature flags. No big-bang rewrite.

---

## 6. Copy & legal guardrails

- Paper conviction and DDollar commits are **platform signals**, not investment contracts.
- Live activity feed paraphrases public actions — no private investor amounts unless founder opts in.
- Capital Raise regulatory class → **Consult AU counsel** CTA only (see Architecture Review v2).
- Founder Exchange shows **graduated-only** badge; non-graduated projects: Discover card with “Not yet graduated” — not hidden, not swap-ready.

---

## 7. What we are NOT building in Raise Room UX

- DexScreener-style chart wall as the hero
- One-click AI token deploy (p0 factory pattern)
- Anonymous bonding-curve launch UI
- Platform custody of mint keys

See [RAISE-ROOM-P0-INSPIRED-PLAN.md](./RAISE-ROOM-P0-INSPIRED-PLAN.md) §B for explicit reject list.

---

*Product engineering context only. Not legal or investment advice.*
