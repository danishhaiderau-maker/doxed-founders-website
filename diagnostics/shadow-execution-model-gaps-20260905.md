# Alternate-policy execution-model evidence audit

Read-only audit of current source and bounded latest-50 ledger samples. These
counts are samples, not whole-dataset totals. No source data was modified.

## Observed inputs

- Pre-entry feature receipts retain immutable pre-decision feature hashes and
  segment references. Signal-time ATR14_pct_3m: 50/50 sampled feature rows and
  42/50 sampled opportunity rows. Signal ATR is not automatically fill ATR.
- Sampled order intents: leverage/margin 19/50, requested quantity 31/50,
  signal ATR 11/50, signed quantity constraints 0/50. Legacy imported default
  leverage/margin values are not observed sizing evidence.
- Sampled executions: fill ATR 21/50, fill quantity 48/50. Actual closed-trade
  economics apply to that actual policy/holding period, not every alternate exit.
- Market segments include BBO/depth/trade paths and some 1-minute OHLCV.
  Complete causal history at every hypothetical fill has not been established.

## Open collection requirements

1. Persist an immutable execution-model context at admission: venue/symbol,
   observed timestamp, effective fee schedule/source/version, maker/taker rates,
   explicit leverage/margin/original quantity, signed lot/notional constraints,
   and causal identity. Keep missing fields unavailable; do not fill from legacy
   defaults or treat a fee-profile name as a verified venue fee schedule.
2. Persist hash-bound funding observations throughout candidate holding horizons.
   Distinguish venue observations from synthetic fallback; actual-close funding
   cannot price a different holding period.
3. Persist submit/reprice/cancel/exit request-to-ack timing and contemporaneous
   BBO evidence. AI response latency and time waiting for price to touch a limit
   are not exchange acknowledgement latency.
4. Retain causal OHLC history sufficient for each candidate fill's ATR, or use
   an explicitly signed policy contract that uses signal-time ATR. Never relabel
   signal ATR as observed fill-time ATR.

The normal atomic analyzer currently has no validated research-model producer
supplying these contexts. Its shadow terminal evaluator therefore retains
UNKNOWN where inputs are missing. Source support for an evaluator and test
fixtures is not evidence that historical data supplies its required inputs.

## Next implementation boundary

Wire the collection context through collector, immutable ledgers/segments,
transfer manifest, analyzer loader/evaluator and coverage UI in one reviewed
change. Test both observed and unavailable branches, revision/config identity,
causal timing, partial quantities and alternate-exit costs. Do not change a
production tile or claim static/dynamic profitability from invented costs.
