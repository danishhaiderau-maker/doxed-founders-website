# Money Feed — product rules

**Founder OS = Build · Discover = Research · Feed = Money**

If an item does not help a trader find opportunity, momentum, conviction, smart money, or risk — it does not belong in Feed.

## Shown on Feed (default `Money Feed` tab)

- New listings
- Large / conviction buys, sells, adds, reduces
- Prediction markets (new, hot, resolved)
- Watchlist surges
- Hot buys & top-trader flow
- Founder verification & **major** ships (mainnet, public launch — not routine deploys)
- Raise / demand signals

## Not shown on Feed

- GitHub commits, PRs, sync tasks, agent runs, memory updates
- Routine deploys / build posts (unless headline matches major ship keywords)
- Founder videos, pinned X sync (use Discover / project page)

## Bubble strip (Feed page)

Bubble size uses **money-weighted** score (`bubbleMode=feed` on discover universe API):

- 40% trading volume
- 20% watchlists
- 15% prediction votes
- 10% founder milestones
- 10% community
- 5% social

GitHub commits do **not** contribute on Feed bubbles.

## API

- `GET /feed/hub?category=all` — merged stream + `sections` (top movers, tape, predictions, listings, smart money)
- `GET /founder-den/discover/universe?bubbleMode=feed` — bubble sizes for Feed strip
