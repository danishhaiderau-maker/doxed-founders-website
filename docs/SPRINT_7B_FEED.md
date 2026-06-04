# Sprint 7B — Feed Hub & Unified Terminal

## Goal

Merge the **unified platform feed** (`GET /feed/unified`) and **Social Conviction Terminal** (`GET /feed/terminal`) into a single hub experience on `/feed`, with founder activity enriched from `FounderEvent`.

## API

| Endpoint | Purpose |
|----------|---------|
| `GET /feed/hub` | Merged stream + pulse + terminal sidebar data |
| `GET /feed/unified` | Legacy unified-only (landing widgets may still use hub) |
| `GET /feed/terminal` | Legacy terminal-only |

### Hub query params

- `category` — `all` \| `founder` \| `trading` \| `community` \| `market` (default `all`)
- `tab` — terminal sub-tab when category is `all` or `trading` (default `all`)
- `project` — optional project slug filter for terminal cards
- `limit` — merged stream cap (10–100, default 50)

### Response shape

- `stream` — chronologically merged `unified` and `terminal` entries (dedupes unified trade rows that already appear as terminal cards)
- `pulse`, `hotQuestions`, `scoutListings` — from unified feed
- `terminal` — full terminal payload (stats, sidebar) when category is `all` or `trading`; otherwise `null`
- `counts` — `{ unified, terminal, merged }`

## Shared logic (`@dcf/utils`)

- `mergeFeedHubEntries()` — merge + dedupe + sort
- `FEED_HUB_CATEGORIES` — category tab labels
- `founderEventToUnifiedItem()` — map `FounderEvent` rows to unified items

## Web

- `/feed` — **Platform Feed** with category tabs, pulse strip, optional terminal sub-tabs, merged `FeedHubStream`
- `fetchFeedHub()` in `apps/web/src/lib/api.ts`
- Components: `feed-hub-category-tabs`, `feed-hub-pulse-strip`, `feed-hub-stream`, `unified-feed-item-card`
- Landing hub preview uses hub stream for top items
- `useFeedNewCount` polls hub stream for badge count

## Smoke

`scripts/smoke-test.mjs` includes `feed-hub` → `GET /api/feed/hub?category=all&limit=10`.

## URL examples

- `/feed` — trading + market only (predictions, stakes, listings, paper trades)
- `/feed?category=founder` — deploys & pinned updates (no GitHub commits)
- `/feed?category=trading&tab=conviction` — conviction terminal slice
- `/feed?category=trading&project=my-token` — project-scoped trades
