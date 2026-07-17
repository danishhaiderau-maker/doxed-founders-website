# Shared AI, Tile 2, and relay cohort audit — 2026-07-18

## Matched recent-window baseline

The comparison uses each lane's most recent 87 unique `trade_id` closes from
the runtime `trades_3factor.csv`. Duplicate rows were collapsed by trade ID
using the last close record.

| Metric | Type B Hunter | Continuous |
|---|---:|---:|
| Unique closes | 87 | 87 |
| Win rate | 80.46% | 75.86% |
| Net P&L | $30.81 | $38.64 |
| EV per close | $0.3541 | $0.4441 |
| MFE >= 15% | 17.24% | 19.54% |
| Trades chased | 80 | 85 |
| Mean chase count | 1.805 | 3.414 |
| Median duration | 0.94 min | 3.74 min |

67 of 87 Type B closes had a same-direction Continuous trade within five
minutes (77.0% overlap; mean entry-price difference 0.0663%). This supports one
shared direction inference call, but it does not support merging the strategy
books: Type B had the higher win rate while Continuous had the higher EV.

## Frozen implementation contract

- One direction-only AI request is allowed per AI_SCAN cooldown (default 180s).
- The AI returns LONG, SHORT, or NO_TRADE plus comparative long/short evidence
  scores. It does not return a win probability.
- Continuous and Type B consume that same immutable result.
- Continuous and Type B retain separate feature gates, trade IDs, pending
  orders, chase state, fills, exits, policy versions, and P&L ledgers.
- Type B keeps its bounded pending-limit chase. It is not a static order lane.
- Tile 2 uses no AI. Its first normalized cohort uses canonical ADX and a rolling
  ATR percentile, fails closed on either missing value, and remains paper-only.
- Live copy permits only `cont-*` and `tbhv1-*`. It rejects Tile 2 and every
  unknown lane.
- The website Start action must stamp the current per-instance LIVE consent
  policy. An operations dry-run override always wins.
- The relay waits for the exact showcase pending limit, follows its replacements,
  and mirrors fill/exit lifecycle. Missing or stale showcase state blocks entry.

## Archive/reset rule

Historical runtime artifacts must be archived with the bot's verified research
archive mechanism before active cohort counters are reset. No raw research file
is deleted as a substitute for archiving.
