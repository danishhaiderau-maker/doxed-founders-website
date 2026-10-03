# Market-context collector (watch only)

Additive data collection for research: Coinbase spot and premium, liquidations,
funding/OI/basis, and session/macro flags. It creates **no orders, no tiles, no
relay eligibility and no fee constants**, uses no API keys, and cannot affect
Bitfinex arming, readiness or any gate.

## Process

- `market_context_collector.py` runs as its own process from `fly-entrypoint.sh`
  (`nice -n 10`, restart loop, log `market-context-collector.log` capped at 5 MB).
  It imports neither `bot` nor Flask. Disable with `MARKET_CONTEXT_COLLECTOR_ENABLED=0`.
- WebSockets (public, reconnect/backoff via `cross_venue_collector.ConnectionWorker`):
  - Coinbase `ws-feed`: `ticker` + `heartbeat` for `BTC-USD` and `USDT-USD`.
  - Binance spot `BTCUSDT@depth5@100ms`.
  - Liquidations: Binance USDT-M `btcusdt@forceOrder` (`/market`), Bybit
    `allLiquidation.BTCUSDT`, OKX `liquidation-orders` (SWAP, filtered to
    `BTC-USDT-SWAP` and `BTC-USD-SWAP`).
- REST, once a minute at :20 (one request per endpoint, 5 s timeout, 256 KB cap):
  Binance `premiumIndex` + `openInterest`, Bybit `tickers`, OKX funding-rate /
  open-interest / mark-price / index-tickers, Bitfinex `status/deriv`.
- Measured on a laptop: about 1-1.5 % of one core, rows about 6.4 KB/minute
  (about 9 MB/day plus liquidations).

## Files (runtime dir)

| File | Schema | Shipped |
|--|--|--|
| `market_context_1m.jsonl` | `market_context_1m_v1` | yes (segment pipeline), rotates at 20 MB |
| `liquidations.jsonl` | `liquidation_event_v1` | yes, rotates at 20 MB |
| `market_context_live.json` | `market_context_live_v1` | no (excluded in `research_segment_selection`) |

One minute row holds:

- `spot.{coinbase,binance_spot}`: per-second mids (`m0` + `dm` offsets in
  0.005 USD), an `up` mask and `up_sec`. A second is up only when the socket is
  connected **and** the quote is at most 3.5 s old; otherwise the mid is null.
  Nothing is forward-filled.
- `premium`: per-second premiums in 0.01 bp for Coinbase vs Bitfinex, vs Binance
  spot, vs Binance perp, and Binance spot vs Bitfinex. Bitfinex mids come from
  `market_microstructure_1s.jsonl`, Binance perp mids from `cross_venue_live.json`.
  `premium_bp_mean` and `ref_up_sec` summarize the minute.
- `usdt_usd`: Coinbase USDT-USD mean/last/up_sec (stablecoin basis).
- `derivatives.{binance,bybit,okx,bitfinex}`: funding, predicted funding, next
  funding time, mark, index, `basis_bp`, OI in BTC, `status`
  (`OK`/`PARTIAL`/`ERROR`/`MISSING`), and minute deltas. Deltas are null when
  the previous snapshot is missing, failed or older than 180 s. A minute
  without a fetch says `MISSING` rather than repeating the previous value.
  Bitfinex `predicted_funding_rate` is the accrued next-funding value.
  `binance_perp_vs_spot.basis_bp` is the cross-feed basis.
- `liquidations.{binance,bybit,okx}`: per-minute long/short counts, BTC and USD,
  the largest event, and `status` from feed uptime (`OK`, `PARTIAL`, `FEED_DOWN`).
  Binance pushes at most one liquidation per symbol per second, so its totals
  are a lower bound (`completeness` on each event says so).
- `flags`: `market_session_calendar.flags(minute_ts)`: session, weekend, Asia/EU/US
  cash open and 30-minute open windows, CME, funding-settlement window, and the
  macro window (FOMC, CPI, NFP; -30/+60 min). Outside the static table the
  status is `CALENDAR_MISSING_YEAR` and no window is claimed. **Add the 2027
  table before 2027-01-01.**
- `regime`: Bitfinex 1-minute-close rv15 ranked against the trailing 7 days only
  (`trailing_regime`, `WARMUP` until 1,440 minutes of history). On boot the
  collector rehydrates that history from the Bitfinex 1 s tape (active file plus
  rotations, about 55 h) with the live rv15 formula, and fills older parts of
  the 7-day window from rv15 values already stamped in `market_context_1m`
  rotations. A deploy or restart therefore keeps labelling; only a volume with
  less than one day of tape starts in `WARMUP`. The seed receipt
  (`tape_minutes`, `tape_rv_n`, `context_rv_n`, `labels_ready`, `elapsed_ms`)
  is in `/api/status.collection.market_context_tape.regime.seed`.
- `meta`: collector version, calendar version, CPU %, RSS, message counts,
  reconnects, REST latency and errors.

## Monitoring

`/ready` returns `market_context_health`, and the status payload has
`market_context_tape`. Both come from `market_context_tape.health_from_live`:

- `COLLECTOR_DOWN` when the live file is missing or older than 60 s.
- `DEGRADED` with `stale_feeds` when a socket is silent for more than 60 s, or a
  REST venue is not `OK` within 180 s.

Health never feeds `ready_ok` or any order path (`affects_orders: false`).

The analyzer's `data_health_report.json` (`research/data_health_report.py`, page
`/data-health`, API `/api/streams/data-health`) shows coverage, staleness and lag
against the mirror head for every stream. It also tracks the quality of the
repaired streams.
