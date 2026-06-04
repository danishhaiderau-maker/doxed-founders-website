# Sprint 7A — Discover visibility & stage-aligned bubbles

## Goal

Make Discover ranking transparent for founders and ensure **bubble ring colors match real project stage** (building = blue, validation = orange, live = green).

## Stage ring colors (fixed)

Previously, any project **created within 14 days** was forced to purple (`recently_listed`), even when still in the building phase.

**Now:**

| `stageBucket` / state | Ring color |
|----------------------|------------|
| `IDEA_STAGE`, `BUILDING` | Blue — Building |
| `LAUNCH_READY` | Orange — Validation |
| `LIVE_TOKEN` / live token | Green — Live |

**Recently Listed** is a **filter tab only** (projects listed ≤14 days). Ring color still follows stage. Optional violet **New** badge on bubbles when `recentlyListed: true`.

## API

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /founder-den/discover/universe` | Public | Bubble map data; `recentlyListed` per project |
| `GET /founder-den/discover/my-visibility` | JWT | Founder breakdown + tips for primary project |

## Utils (`@dcf/utils`)

- `resolveDiscoverUniverseStage()` — maps `stageBucket` → ring stage (no age override)
- `isDiscoverRecentlyListed()` — filter tab helper
- `computeDiscoverVisibilityBreakdown()` — per-factor points for founder panel

## Web

- `/discover` — ranking rules, visibility guide, corrected legend
- Mission Control **Settings** tab — `DiscoverMyVisibilityPanel`

## Smoke

`discover-universe` — `GET /api/founder-den/discover/universe?timeframe=24h`
