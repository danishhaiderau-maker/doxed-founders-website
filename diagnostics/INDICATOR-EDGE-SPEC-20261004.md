# Live Indicator Edge Tracker - spec (from Boss, Danish's coordinator), 2026-10-04

Verbatim brief + Appendix A. Build exactly this. Observe-and-score only: paper, relay-ineligible, nothing touches execution.

## Brief

**Goal.** The AI's direction calls have no edge. Compute all 50 indicators from Danish's list **live, from our own collected data only**: the Bitfinex 1-second tape, the cross-venue feeds, Binance top-5 depth, funding, OI and liquidations. Score them **forward only**, so the system finds out which ones really point the right way, in which market conditions, and which combinations could replace the AI as the signal source. Do no backtesting on exchange history.

### 1. Fit it into the existing architecture
- **Fly (collection).** Add an `indicator_engine` module that runs off the main trading loop, so it doesn't undo the latency fixes in #367/#373.
  - On every closed 3-minute bar, built from our 1-second tape, it computes the feature vector and appends one row to `indicator_bars_v1.jsonl`.
  - Append-only, sealed segment like the other research streams, shipped to the laptop through the existing segment shipper and data-sync contract.
  - Every row carries `data_epoch`, `boot_id`, bar time, feature-set version and a feature-health flag (inputs fresh and complete or not).
- **Indicators.** All 50 (+2 cross-venue = 52, Appendix A), closed bars only, no repainting.
  - Most at two horizons: fast (~9 bars) and slow (~50 bars).
  - Each stored as raw value + normalised direction score (-1/0/+1 state or rolling-percentile z-score).
  - Long-history indicators (EMA 200/288, daily pivots, session VWAP) report `WARMING_UP` until enough bars exist on our own tape.
  - Inputs we don't collect (exchange inflow/outflow, top-20 book, DVOL) are `UNAVAILABLE`. Add top-20 depth snapshots to the collector if cheap.
- **Laptop (scoring).** Add an `indicator_forward_scorer` alongside the analyzer.
  - For each bar and indicator: predicted side (long/short/neutral) and outcome: Bitfinex executable return at +3, +15, +60, +120 min, and MFE/MAE over each window.
  - Reuse the shadow-exit recorder's price paths (no duplicated work).
  - Results: new "Indicator Edge" section on the analyzer dashboard, an API endpoint, and the daily analyzer cycle.

### 2. Pre-register before scoring
- Freeze feature list, parameters and scoring rules in the forward tracker (hash-chained) before the first scored bar. No retuning on scored data; a parameter change = new frozen candidate with a fresh clock.
- Every indicator x horizon x prediction window counts as a trial for honest FDR correction.

### 3. Daily scoring, per indicator
- Hit rate and rank correlation with forward return, overall and in top/bottom 20% of readings.
- Mean executable move in bp when it signals, after 2 bp round-trip spread, at 2 s and 9 s latency.
- Regime breakdown: volatility tercile, trending vs ranging (ADX), session (Asia/EU/US).
- Stability: does the sign hold day by day.
- Correlation clusters (>0.7): keep only the best of each, so 50 collapse to the real independent signals.

### 4. Labels and gates
- `HINT` after 3 days: sign held every day and move beats costs.
- `PROMISING` after 7 days: 1h-cluster-bootstrap CI clears zero after correction, sign held >=5 of 7 days, held in >=2 sessions.
- Everything else `NOISE`. Only `PROMISING` moves on to combinations.

### 5. Combinations (week 2 onward)
- Only `PROMISING` indicators, 2-3 at a time from different families: one regime/trend filter + one trigger + one order-flow confirmation. <=64 variants per combination.
- Freeze each in the forward tracker, score forward another 7 days on fresh data.
- Also score each as a filter on existing tiles (e.g. "fade the AI only when trend is down").

### 6. Tile packages
- A combination still `PROMISING` after its own forward week becomes a complete tile per Danish's rules:
  - Entry: volatility-aware or confirm-then-chase.
  - Exit: composite (late break-even, ATR/chandelier trail armed after +1.5 ATR, time backstop; first trigger wins).
  - Risk: conditional early cut (-10/-12 bp in first 3-5 min only if MFE never > +2 bp) and 40 bp hard stop.
  - $0.25 margin at 100x, REALISTIC_V1, zero fees.
- Registered paper-only, relay-ineligible, default OFF, pre-registered promote/kill rules. Danish turns it on.

### 7. Reports
- Daily one-paragraph summary for Danish: top 5 indicators with labels, which regime each works in, anything newly HINT/PROMISING/NOISE.
- Weekly `diagnostics/INDICATOR-EDGE-WEEK-<n>.md` with full ranking table and combination results.

### 8. Rules
- Ship in the follow-up deploy after the current stack is certified.
- No execution changes, no new load on the trading loop.
- Don't wipe research data the scorer needs.
- Explain everything to Danish in plain English.

## Appendix A: Indicator grid

All bars are 3-minute bars from our own Bitfinex 1-second tape (OHLC from mid/trade prints, volume and taker buy/sell from our tape). Every indicator stores: raw value, direction score (+1/0/-1) and, where sensible, a 0-100 rolling percentile (480 bars = 24h). Inputs: T = Bitfinex tape, X = cross-venue (Binance/Bybit/OKX), D = Binance top-5 depth, F = funding, OI = open interest, L = liquidations.

### A. Trend (filters)
| # | Feature id | Settings | Direction score rule | Input |
|---|---|---|---|---|
| 1 | EMA_200 | EMA 200 (10h) | +1 close > EMA and EMA slope up over 10 bars; -1 mirror; else 0 | T |
| 2 | EMA_50 | EMA 50 (2.5h) | same as #1 | T |
| 3 | EMA_CROSS_9_21 | EMA 9 vs 21 | +1 EMA9 > EMA21, -1 below | T |
| 4 | EMA_288 | EMA 288 (14.4h) | same as #1 (WARMING_UP until 288 bars) | T |
| 5 | ICHIMOKU_FAST | 7/22/44, disp 22 (compare price to already-printed cloud only) | +1 above cloud and Tenkan > Kijun; -1 mirror; else 0 | T |
| 6 | SUPERTREND | ATR 10, mult 2.0 | +1 uptrend state, -1 downtrend | T |
| 7 | PSAR | 0.02 / 0.2 | +1 SAR below price, -1 above | T |
| 8 | HMA_SLOPE | HMA 14 fast, 50 slow | sign of 3-bar slope | T |
| 9 | LINREG_SLOPE | 14 fast, 50 slow | sign of slope if abs(t) > 2, else 0 | T |
| 10 | TEMA | TEMA 9 vs 21 | +1 TEMA9 > TEMA21, -1 below | T |

### B. Momentum (triggers) - each scored as separate TREND and REVERSION features
| # | Feature id | Settings | TREND | REVERSION | Input |
|---|---|---|---|---|---|
| 11 | RSI | 9 fast, 21 slow | +1 > 55, -1 < 45 | +1 < 25, -1 > 75 | T |
| 12 | STOCH | 5,3,3 | +1 %K crosses up %D above 50 | +1 cross up below 20, -1 cross down above 80 | T |
| 13 | MACD_SCALP | 6,13,4 fast; 12,26,9 slow | sign of histogram and rising | histogram turning against an extreme (top/bottom 10% pct) | T |
| 14 | CCI | 14 | +1 > +100, -1 < -100 | +1 back above -100, -1 back below +100 | T |
| 15 | WILLR | 10 | +1 > -20, -1 < -80 | +1 up through -80, -1 down through -20 | T |
| 16 | AO | SMA 3/15 of median | sign and rising | zero-line twin-peak reversal | T |
| 17 | KST | ROC 3,6,9,12; SMA 3x4; signal 3 | +1 KST > signal | n/a | T |
| 18 | CMO | 9 | +1 > +30, -1 < -30 | +1 < -50, -1 > +50 | T |
| 19 | ROC | 6 (18m), 20 (60m) | sign if abs > rolling 60th pct | reversed if abs > 95th pct | T |
| 20 | DPO | 12 | +1 crosses above 0 | +1 bottom 10% pct, -1 top 10% | T |

### C. Volume and order flow (confirmation)
| # | Feature id | Settings | Rule | Input |
|---|---|---|---|---|
| 21 | VP_SESSION | profile from 00:00 UTC: POC/VAH/VAL (70%) | +1 accepted above VAH (2 closes), -1 below VAL; REVERSION: +1 VAL rejection, -1 VAH rejection | T |
| 22 | AVWAP_SESSION | VWAP anchored 00:00 UTC + 1/2 std bands | +1 close > VWAP, -1 below; REVERSION fades 2-std bands | T |
| 23 | OBV_EMA | OBV vs EMA 13 | +1 above, -1 below | T |
| 24 | AD_SLOPE | A/D slope 14 | sign; divergence variant +1 price down while A/D up | T |
| 25 | VWMA_20 | VWMA 20 vs SMA 20 | +1 close > VWMA and VWMA > SMA | T |
| 26 | MFI | 9 | REVERSION +1 < 20, -1 > 80; TREND +1 > 60, -1 < 40 | T |
| 27 | CMF | 12 | +1 > +0.05, -1 < -0.05 | T |
| 28 | KLINGER | 17/34/9 | +1 KVO > signal | T |
| 29 | EOM | 9 | sign | T |
| 30 | NET_TAKER_VOL | taker buy-sell per bar + 5-bar sum | sign if abs > rolling 70th pct | T |
| 44 | BOOK_IMBALANCE | (bid-ask)/(bid+ask), Bitfinex TOB + Binance top 5 (top 20 when collected) | +1 > +0.3, -1 < -0.3 | T, D |
| 45 | CVD | taker CVD per bar + 20-bar slope, Bitfinex and Binance; divergence price up/CVD down | trend: sign of slope; divergence variant fades price | T, X |
| 50 | XVENUE_NET_FLOW | Binance+Bybit taker net flow minus Bitfinex | sign if abs > 70th pct | X |

### D. Volatility (regime labels; breakout direction where noted)
| # | Feature id | Settings | Use / score | Input |
|---|---|---|---|---|
| 31 | BB | 20, 2.0; bandwidth pct | squeeze = bandwidth < 20th pct; breakout direction = close outside band after squeeze | T |
| 32 | ATR | 14 | regime tercile; ATR-scaled offsets/trails/stops | T |
| 33 | KELTNER | EMA 20, 1.5 ATR | +1 close above upper (trend) / REVERSION fade at band | T |
| 34 | DONCHIAN | 20 (60m) | +1 break of 20-bar high, -1 low | T |
| 35 | CHAIKIN_VOL | 10,10 | expansion flag (> 80th pct) | T |
| 36 | HV | 10-bar realised vol pct | REVERSION trigger > 95th pct (fade last 3-bar move) | T |
| 37 | STDDEV_Z | (close-SMA20)/StdDev20 | REVERSION +1 < -2, -1 > +2 | T |

### E. Structure (previous-day / confirmed-swing values only)
| # | Feature id | Settings | Rule | Input |
|---|---|---|---|---|
| 38 | FIB_4H | rolling 4h high/low, 0.5/0.618 | +1 bounce at 0.618 in a 4h up-leg, -1 mirror | T |
| 39 | FIB_EXT_4H | 1.272/1.618 | take-profit levels only (not scored) | T |
| 40 | CPR_PIVOTS | prev UTC day pivot, BC/TC, S1/R1 | +1 above TC, -1 below BC; REVERSION fades S1/R1 | T |
| 41 | CAMARILLA | prev day H3/L3/H4/L4 | REVERSION fade H3/L3; breakout follow beyond H4/L4 | T |
| 42 | ZIGZAG_MSB | 0.3%, confirmed swings | +1 break of last confirmed swing high, -1 swing low | T |
| 43 | PITCHFORK_12H | confirmed 12h swings | +1 inside rising fork above median; mostly context label | T |

### F. Derivatives
| # | Feature id | Settings | Rule | Input |
|---|---|---|---|---|
| 46 | LIQ_BURST | liq $ per bar long vs short, pct | REVERSION fade burst > 95th pct (long liqs -> +1); TREND variant follows | L |
| 47 | VOL_EXPECTED | DVOL if collected else realised-vol pct proxy | regime label only | (proxy T) |
| 48 | FUNDING | current + predicted, pct | REVERSION -1 if > 90th pct, +1 < 10th; time-to-funding label | F |
| 49 | OI_DELTA | OI change per bar and 20 bars with price direction | +1 OI up & price up, -1 OI up & price down; OI down = 0 | OI |
| 51 | BASIS_PREMIUM | Bitfinex vs Binance/Bybit premium deviation from 60m mean (existing cross-venue premium trigger) | follow sign | X |
| 52 | XVENUE_LEAD | Binance/Bybit 10s lead (existing cross-venue lead trigger), per bar | follow sign | X |

### G. Regime labels on every row (splits only, never scored)
vol_tercile (ATR pct), trend_state (ADX 14: <20 range, 20-30 weak, >=30 trend), session (ASIA/EU/US = UTC 0-8/8-16/16-24), spread_bp bucket, time_to_funding bucket, ai_class and ai_side of the latest AI call.

### H. Combination grid (week 2, PROMISING only)
Shape: [1 regime/trend filter from A, D or G] + [1 trigger from B, C-momentum, E or F] + [optional 1 order-flow confirmation from C]. Max 3 features, <=2 settings each (<=8 variants per combination, 64 cap per family). Starters (if parts survive):
- Trend breakout: EMA_50 & EMA_200 agree + DONCHIAN or BB squeeze breakout + CVD slope agrees + OI_DELTA rising.
- Range reversion: ADX < 20 + RSI/STOCH REVERSION extreme at Keltner band or Camarilla H3/L3 + BOOK_IMBALANCE absorbing.
- Liquidation squeeze: LIQ_BURST > 95th pct + price back at VP POC/VAL + MACD histogram turns.
- VWAP pullback: above AVWAP_SESSION & EMA_50 rising + pullback to AVWAP + NET_TAKER_VOL flips to buyers.
- Funding fade: FUNDING extreme + CVD divergence + HV not top 5%.
- Indicator as filter on existing tiles: committed fade and NO_TRADE follow only when the best trend feature agrees.

### I. Scoring outputs per feature and per combination
signals/day, hit rate at +3/+15/+60/+120 min, mean executable bp after 2 bp spread at 2 s and 9 s latency, rank IC, MFE/MAE, by-regime split, daily sign stability, cluster-bootstrap CI, FDR-adjusted p, label HINT / PROMISING / NOISE.
