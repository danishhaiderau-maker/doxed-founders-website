# Cross-venue leader tape (shadow only)

Additive price-data collection for the hypothesis that Binance / Bybit / OKX BTC
perps lead Bitfinex `tBTCF0:USTF0` by seconds. It creates **no orders, no tiles,
no relay eligibility and no fee constants**. Bitfinex arming is unaffected.

## Collector

- `cross_venue_collector.py` is a separate process started by `fly-entrypoint.sh`
  (`nice -n 10`, restart loop, log capped at 5 MB). It imports neither `bot` nor
  Flask. Disable with `CROSS_VENUE_COLLECTOR_ENABLED=0`; restrict venues with
  `CROSS_VENUE_VENUES=binance,bybit,okx`.
- Public websockets only, no API keys:
  - Binance USDT-M `BTCUSDT`: `depth5@100ms` (`/public`), `aggTrade` + `markPrice@1s` (`/market`).
  - Bybit linear `BTCUSDT`: `tickers` (BBO, funding, OI) + `publicTrade`.
  - OKX `BTC-USDT-SWAP`: tickers, trades, funding-rate, open-interest.
- Each connection reconnects with exponential backoff (1 s doubling to 60 s,
  ±20% jitter, reset after a 60 s stable session) and recycles sockets silent
  for 30 s.

## Files (runtime dir)

| File | Purpose | Shipped |
|--|--|--|
| `cross_venue_tape_1m.jsonl` (`cross_venue_tape_1m_v1`) | One compact row per minute: per-venue per-second mid, last, signed buy/sell volume, the Bitfinex mid from `market_microstructure_1s.jsonl`, mean basis, derivatives (funding/mark/index/OI), receive latency, collector CPU/RSS/reconnects. Rotates at 20 MB. | yes, via the segment pipeline |
| `cross_venue_live.json` (`cross_venue_live_v1`) | Atomic per-second state: last 150 s of mids, venue health, derivatives. | no (excluded) |
| `xvl_shadow_signals.jsonl` (`xvl_shadow_trigger_v1` / `xvl_shadow_outcome_v1`) | XVL tile: one trigger row per qualifying second (venue deltas, side, gate, feed ages, Bitfinex bid/ask, cap-1 flag) and one outcome row per trigger with the hypothetical 60 s after-spread markout. Written whether or not the tile toggle is ON. Rotates at 20 MB. | yes, via the segment pipeline |

Retention: the tape is append-only and durable; at 20 MB it rotates to `.N`
generations (no rolling cap, no pruning) and each generation is sealed and
shipped by the segment pipeline to the laptop mirror.

Bucket `s` holds the quote as of `s+1`, matching the Bitfinex 1 s tape. A venue
mid is null when its quote is older than 3.5 s. Measured footprint is about
4 KB/minute (~6 MB/day) and a few percent of one core.

## Leader shadow challenger

`leader_10s` in `ai_shadow_challengers.CHALLENGERS`: at each shared AI call the
side is the sign of the priority venue's (Binance, then Bybit, then OKX) 10 s
mid return ending at bucket `floor(decision_ts) - 1`, if |return| ≥ 2 bp;
otherwise `NONE`. It matures like every other challenger (+10 s..+60 m markouts
and registry tile geometry). Call rows also carry `leader_features` and a
`derivatives` block (Bitfinex funding/mark/index/OI plus leader-venue
derivatives).

## Cross-venue lead tile (XVL)

`FAMILY_XVENUE_LEAD_60S` (paper only, relay-ineligible) reads
`cross_venue_live.json` and the Bitfinex 1 s tape on its own `xvl-evaluator`
thread, once per second at `floor(t)+1.6 s` with anchor bucket `floor(t)-1`
(no catch-up; missed seconds are counted). Lead = mean(Binance, Bybit 10 s mid
return) − Bitfinex 10 s mid return. A trigger needs |lead| >= 8 bp, every feed
<= 2 s old and spread <= 3 bp. Shadow rows are always written; when the tile is
ON a one-slot worker submits a taker paper order (5 bp cap, 3 s TTL) through the
standard lane spawn path, with >=5 s between submissions and <=60 per hour, and
refuses stale triggers (>1.5 s) or an open position. Exit: 60 s time exit
(`PATH_END_1M`) or 40 bp catastrophic stop. An evaluator crash is recorded and
never pauses execution. Health: `/ready.xvl_evaluator_health`.

## Analyzer

`research/lead_lag_report.py` → `lead_lag_report.json` (analyzer report index
"Cross-Venue Lead-Lag"): cross-correlation of 1 s returns at lags −10..30 s,
Bitfinex response 1–30 s after leader moves, and the after-spread markout of a
leader-follow rule (enter at the Bitfinex ask/bid 1 s after the trigger, exit at
the opposite side, capacity 1) with hour-cluster robust CIs, cluster bootstrap
and Benjamini–Hochberg FDR. The pre-registered rule is 10 s / 2 bp. Prices
only; fees are deliberately not modelled here. Its `xvl` section replays the
registered XVL rule on the tapes (capacity one), summarises the shadow stream
(gates, stale-feed share, cap-1 and every-qualifying-second outcomes with 1 h
cluster CIs) and reports anchor-matched shadow-vs-replay parity; the :9001
Exit Combinations page renders it. The XVL kill/promotion verdict is in
`tile_paired_comparison_report.json`.

## Monitoring

`/ready.cross_venue_health` (not part of `ready_ok`) → laptop status snapshot →
`CROSS_VENUE_TAPE_STALE` warning in `laptop-chain-monitor.ps1` whenever the
status is not `OK` or `DISABLED`. The AI shadow dashboard panel shows the
leader-feed status. `/ready.xvl_evaluator_health` follows the same path and
raises `XVL_EVALUATOR_STALE` when the evaluator is `STALE` or `DEGRADED`.
