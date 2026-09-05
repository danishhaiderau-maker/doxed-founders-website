# Bounded pre-signal candle causality audit

Local samples only; no production probe, full archive scan, or historical rewrite.
Root: `services/btc-conservative-agent/canonical-research-data`.
These are sampled historical mirror records, not a current-generation parity receipt.
Read at most 1 MiB from each selected head/tail. Row hashes below exclude line endings.
All eight records contain 200 advertised pre-signal 1m candles, but zero candles
satisfy `open_timestamp_seconds + 60 <= signal_ts`. The seven legacy rows also
have empty `canonical_tape.path_1m`. This does not prove every archived row is unusable.

| Relative file | Record | Signal seconds | First candle open | Last candle open |
| --- | --- | ---: | ---: | ---: |
| opportunity_capture.jsonl | ftr-14f3d55e6cb7 | 1788413564.1452034 | 1788501660 | 1788513660 |
| opportunity_capture.jsonl | fhy-f10210b418c3 | 1788413588.5498438 | 1788501900 | 1788513900 |
| opportunity_capture.jsonl | fmg-bcc488500aba | 1788413640.7061527 | 1788502260 | 1788514260 |
| opportunity_capture.jsonl | cont-38cdbc5b7986 | 1788276263.3440876 | 1788416700 | 1788429000 |
| opportunity_capture.jsonl | scan-7e9fb390f621 | 1788276418.703584 | 1788417360 | 1788429540 |
| opportunity_capture.jsonl.1 | cont-11ad406753c0 | 1787874231.909581 | 1787895960 | 1787909640 |
| opportunity_capture.jsonl.1 | cont-371001f367f0 | 1787868492.4876409 | 1787895960 | 1787909640 |
| v3/ledgers/opportunity.jsonl | opportunity:episode-601b4bc04a960fdc4fa3 | 1788423104.3045778 | 1788519060 | 1788531000 |

SHA-256 in table order:

```text
3dc33f46819c7f80ca1ed678aafe7ec39aa6b52d62e2d7ec099e108e9f8ae48f
62072d1ec435eeffc3adc88b942846ae90690907382230365f4b0d50297867e9
f24ccf1d689d58c028dc9bf6ad264fa3e9b280c2c8e39c56e18cff082bbb8dcd
b7f54aff80915f55ad70b8052570f4eea79f615d8e6286e5a6e59c3dc1d7fb70
e31889b173f7af185a7460de1637d5e462932bd4544db26fabb542e591b763c4
86f3adf24b85d6243c2f89779dac53cdaec5f9e10d92c0007d9cd2295b6ca16f
78e244e13ced7c3611bc997fb303b088c751f453bc4423544a0c194d9390f8c9
d70c3a73efc68865b7dc93481f36738eee726875d24caca5384a1ec47b9dedbc
```

## Source findings and narrowly applied repairs

`collector_v22.build_pre_signal_context` previously clipped 1m rows only at
the lower lookback boundary. A later maturation call's current cache therefore
became an older signal's purported pre-signal context. Higher timeframes filtered
bar-open time but could still include the forming aggregate. Local repair requires
each source minute to be closed by signal, and each aggregate bar to be closed too.
It explicitly reports empty histories as UNKNOWN and never claims complete coverage
or independently verified availability time.

Separate defect: `bot._collector_cached_candles_1m` borrowed `latest_candles` when
the 1m cache was empty. `bot.fetch_ohlcv` fills that fallback from
`fetch_bitfinex_ohlcv("15m", limit=250)`. The helper now returns an empty missing-source
snapshot instead; no network call or trading behavior was added.

Tests: `test_pre_signal_candle_causality.py`, `test_collector_v22.py`, and
`test_collector_candle_snapshot.py`: 29 passed. No production deployment performed.

## Correct research route, not yet implemented

Raw OHLC is present in `pre_signal_context.series['1m'].candles` and sometimes
`canonical_tape.path_1m`; timestamps, cadence, venue and completeness must be
independently checked. The sampled histories above cannot supply their own causal
fill ATR. `cycle_3m_universe.jsonl` retains scalar ATR values, not underlying bars;
the producer currently computes ATR on all resampled bars, unlike its explicitly
closed RSI branch, so those scalars are not an exact closed-fill ATR receipt.

A derived research context can use hash-pinned, complete 1m bars, filter to closed
minutes/bars before the hypothetical fill, resample exact UTC 3m boundaries, and
compute ATR14 with a declared Wilder seed/warm-up. Label it
`DERIVED_CLOSED_3M_ATR14_AT_REPLAY_FILL`, never an observed historical fill value.
Exact source algorithm parity requires its warm-up/seed history, not merely fourteen
convenient bars. Current `wilder_atr` requires at least sixteen input bars.

Sizing research may explicitly declare fixed quantity or fixed margin plus leverage,
bound to the experiment hash. Historical `signal_snapshot.entry_sizing` and
`order_intent.requested_qty` are source references, not hypothetical-policy approval.
Re-evaluate fills and quantity constraints for changed quantities; do not reuse a
fill receipt for different sizing. No fake `baseline_sizing_authorization_v1` or
`baseline_fill_atr_observation_v1` should be inserted into historical ledgers.
The observed branch remains strict. The declared/derived branch must retain its
separate provenance and cannot make missing tape, venue limits, or costs observed.
