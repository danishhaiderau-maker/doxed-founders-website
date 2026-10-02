# Pre-registered hypotheses, 2026-10-02

Registered: **2026-10-02T00:00:00Z**. Source of truth is
`services/btc-conservative-agent/research/preregistered_hypotheses.py`. The
harness is `research/event_study.py`, which writes `event_study_report.json`
and is shown on the analyzer page `/data-health`.
`test_event_study_and_data_health.py` pins each specification's hash.

All five are research hypotheses only. None creates a tile, an order, a relay
eligibility or a risk change.

## Rules that apply to every hypothesis

1. **Frozen specification.** The event rule, sign, metric, horizons, primary
   horizon, minimum sample, kill rule and lockbox are fixed. Any change means a
   new id and a new lockbox starting at the change. Editing in place fails the
   spec-hash pin test.
2. **Discovery and lockbox.** Events before the registration time are
   discovery. The report shows them labelled `EXPLORATORY_NOT_CONFIRMATORY`;
   they never count toward a verdict. Events from registration until the
   lockbox ends are the lockbox. While a lockbox is open the harness reports
   only event counts, events per day and days to the minimum sample, never
   their outcomes.
3. **One look.** When a lockbox closes it is scored once with the primary
   horizon and kill rule. The verdict is one of `CONFIRMED` (or, for the
   two-sided H2, `CONFIRMED_CONTINUATION` / `CONFIRMED_REVERSAL`), `KILLED`, or
   `INSUFFICIENT_LOCKBOX_EVENTS` if fewer than the minimum sample were seen. A
   lockbox is never extended to reach the sample; that would be a new id.
4. **Controls.** Each event gets 5 deterministic (seeded) control seconds that
   share the event's UTC hour and causal volatility tercile. The tercile is
   Bitfinex 1-minute rv15 ranked against the trailing 7 days only. Controls
   exclude any second within the longest horizon of an event. The abnormal
   outcome is the event minus the control mean, except H1, whose after-spread
   taker markout is already an absolute, tradeable quantity.
5. **Inference.** Standard errors are clustered by UTC hour. Benjamini-Hochberg
   q-values are applied across all scored lockboxes together.
6. **Data quality.** Seconds with a stale or missing feed are null, never
   forward-filled. An event whose inputs are null is not detected, and an
   outcome whose prices are null is excluded.

## Hypotheses

### H1_XVL_LEAD_10S_8BP_60S (spec hash `cb265d7a161a2da9`)

- **Mechanism:** Bitfinex tBTCF0 is a satellite venue. Binance and Bybit perps
  lead it by seconds, so a large cross-venue gap closes toward the leaders.
- **Rule:** the mean 10 s mid return of Binance and Bybit minus the Bitfinex 10 s
  return is at least 8 bp in absolute value, the Bitfinex spread is at most
  3 bp, and events are debounced by 60 s. Side is the sign of the lead.
  This matches the XVL tile hypothesis `H5_XVENUE_LEAD_60S_20261002`
  (`cross_venue_lead.LeadRule` defaults, owned by worker 4a9ff2bd).
- **Metric:** after-spread taker markout in bp: enter 1 s after the event at the
  ask (or bid), exit at the bid (or ask). Horizons are 5, 10, 30, 60, 120 and
  300 s; the primary is **60 s**.
- **Minimum sample:** 200 lockbox events.
- **Kill:** mean ≤ 0 bp or t < 2.
- **Lockbox:** 14 days, to 2026-10-16T00:00Z.
- **Discovery (exploratory, not evidence):** 139 events on the mirror before
  registration, primary +2.22 bp, t = 6.15.

### H2_LIQ_BURST_60S_1M_CONTINUATION (spec hash `cd156cfe18917807`)

- **Mechanism:** forced liquidations are price-insensitive flow. They either
  cascade (continuation) or overshoot and revert. The test is two-sided by
  construction.
- **Rule:** at least $1M of same-side liquidation notional across Binance,
  Bybit and OKX within a trailing 60 s, debounced by 900 s. Side is
  continuation: SHORT after a long-liquidation burst, LONG after a
  short-liquidation burst.
- **Metric:** Bitfinex mid cumulative abnormal return in bp, entering 1 s after
  the event. Horizons are 10 s to 30 min; the primary is **300 s**.
- **Minimum sample:** 40 lockbox events.
- **Kill:** |t| < 1.5. If t ≥ 1.5 the verdict is continuation; if t ≤ −1.5 it
  is reversal.
- **Lockbox:** 30 days, to 2026-11-01T00:00Z.
- **Known bias:** Binance totals are a lower bound (at most one push per symbol
  per second). The threshold therefore errs toward fewer events.

### H3_FUNDING_WINDOW_DRIFT_30M (spec hash `0cf35229b3437116`)

- **Mechanism:** the side paying funding cuts exposure into the 00/08/16 UTC
  settlement, so price drifts against the paying side.
- **Rule:** the event is 30 minutes before each settlement, provided the mean
  Binance/Bybit predicted funding at that minute has an absolute value of at
  least 0.00005 (0.5 bp per 8 h). Side is minus the sign of that funding.
- **Metric:** Bitfinex mid cumulative abnormal return in bp. Horizons are 5 to
  60 minutes; the primary is **1800 s** (that is, through settlement).
- **Minimum sample:** 60 lockbox events.
- **Kill:** t < 1.
- **Lockbox:** 30 days, to 2026-11-01T00:00Z.

### H4_US_CASH_OPEN_VOL_EXPANSION (spec hash `95cd8c325ac11d0a`)

- **Mechanism:** the US cash open concentrates ETF and macro repricing, so
  absolute BTC moves in its first 30 minutes exceed those of matched controls.
- **Rule:** every weekday at 09:30 New York time (13:30 UTC in summer, 14:30 UTC
  in winter, with DST computed by rule).
- **Metric:** absolute Bitfinex mid move in bp. Horizons are 5, 15, 30 and 60
  minutes; the primary is **1800 s**. Controls are matched on weekday versus
  weekend class and the volatility tercile.
- **Minimum sample:** 20 lockbox events.
- **Kill:** t < 2.
- **Lockbox:** 30 days, to 2026-11-01T00:00Z.
- **Known bias:** US market holidays are not excluded.

### H5_COINBASE_PREMIUM_LEAD_300S (spec hash `5caed452cf6364b5`)

- **Mechanism:** Coinbase BTC-USD reflects US spot demand, so a sudden change in
  its premium over Bitfinex is information Bitfinex has not priced yet.
- **Rule:** the 10 s average Coinbase-vs-Bitfinex premium changes by at least
  3 bp compared with 60 s earlier. Both windows need at least 8 valid seconds.
  Events are debounced by 300 s. Side is the sign of the change.
- **Metric:** Bitfinex mid cumulative abnormal return in bp. Horizons are 10 s to
  15 minutes; the primary is **300 s**.
- **Minimum sample:** 100 lockbox events.
- **Kill:** mean ≤ 0 bp or t < 2.
- **Lockbox:** 21 days, to 2026-10-23T00:00Z.

## Data availability

Two hypotheses can use data from before the collector existed:

- H1 uses the cross-venue tape.
- H4 needs only the Bitfinex 1 s tape.

H2, H3 and H5 need `market_context_1m.jsonl` and `liquidations.jsonl`, which
start accruing when the market-context collector is deployed. Their lockbox
clock still starts at registration, so these three can end as
`INSUFFICIENT_LOCKBOX_EVENTS`. That outcome is accepted rather than extending
the lockbox.


## Addendum: tile and model gates (registered 2026-10-02T07:00:00Z)

These four use the ids H6 to H9. They are separate from the event studies
H1 to H5 above (the older `H5_XVENUE_LEAD_60S_20261002` tile id predates this
addendum and is unrelated to the event study H5). The tile gates' source of truth
is each tile's `pre_registration` block in
`services/btc-conservative-agent/combo_pathway_config.py`, scored by
`tile_paired_comparison.py`. H8 and H9 are fixed in
`ai_regime_shadow.py` and `research/decision_model_report.py`. Any change to a
threshold means a new id and a new registration time.

None of the four can arm Bitfinex or become relay-eligible. "Promotion" in each
case means only that the result is ready for Danish to review.

### H6_TREND_FADE_60_COMMITTED_20261002 (Tile 2, Trend Fade 60 — committed calls only)

- **Rule:** fade the score-led side only when the shared AI call commits: the
  raw direction is LONG or SHORT, it matches the score-led side, and the score
  gap is at least 30. It never fades `NO_TRADE`. Execution is a taker entry
  capped at 5 bp and a 3600 s time exit with a 40 bp hard stop. The spread must
  be ≤ 1.68 bp. Policy signature `c2c561c3…5ebe`, cohort
  `v31-committed-fade-premium-v6`.
- **Control:** Tile 1 (Trend Fade 60) on the same shared calls.
- **Evidence:** dev +12.6 bp per call [+3.3, +25.2]; holdout +11.6 bp
  [−4.5, +27.9]. This is a HINT only, because the audit that proposed the rule
  also chose it.
- **Promotion (all required):**
  - at least 150 trades across at least 40 distinct hours;
  - at least 3 regime days covering up, down and range. A UTC day with
    |r24h| ≥ 1.5% is a trend day.
  - per-trade EV with a 2 h-cluster bootstrap lower 95% CI above 0;
  - beats Tile 1 on the same calls by at least 10 bp per trade;
  - both halves positive.
- **Kill (any one):**
  - K1: hit rate below 52% after 150 trades.
  - K2: any up-trend or down-trend day averaging below −15 bp per trade.
  - K3: any trade worse than −60 bp.
  - K4: drawdown above $1.00.
  - K5: day 21 without promotion.

### H7_XVENUE_PREMIUM_60S_20261002 (Tile 4, cross-venue premium extreme)

- **Rule:** the leaders' premium over Bitfinex is compared with its 20-minute
  mean.
  - Trigger: the deviation is ≤ −1.75 bp (LONG) or ≥ +1.88 bp (SHORT), with
    both feeds fresh and the spread ≤ 3 bp.
  - Execution: Bitfinex taker entry in the leaders' direction, capped at 5 bp;
    hold 60 s with a 40 bp hard stop.
  - Policy signature `39ad7a3f…bfe1`.
  - One tile only. The 300 s hold variant was not better on the same
    holdout, so it is not registered.
- **Evidence:** 8 h holdout, 1-minute hit rate 78%, +2.0 bp per trade
  [+0.7, +3.2], 115 independent events. The dashboard badge reads
  "HINT — 8h holdout evidence".
- **Promotion (all required):**
  - at least 500 trades over at least 7 UTC days, including at least 3 sessions
    each of ASIA (00–08 UTC), EU (08–13) and US (13–21);
  - per-trade EV with a 1 h-cluster lower 95% CI above 0;
  - 5 s-delayed shadow entry mean above 0;
  - no single day above 30% of profit;
  - both sides ≥ 0;
  - replay parity ≤ 1 bp;
  - median signal-to-fill ≤ 2 s.
- **Kill (any one):**
  - K1: mean ≤ 0 bp after 300 trades.
  - K2: 5 s-delay shadow mean below −0.5 bp after 300 trades.
  - K3: any trade worse than −45 bp, or more than 1% of trades on a stale feed.
  - K4: drawdown above $0.50.
  - K5: day 14 without promotion, scored as INCONCLUSIVE.
  - K6: a lifecycle, identity, analyzer, feed or mirror defect pauses and
    quarantines the tile; this is not a strategy verdict.
- **Warm-up:** the 20-minute mean needs 1200 samples. The tile cannot trigger
  for about 20 minutes after each boot.

### H8_AI_REGIME_60M_SHADOW_20261002 (shadow regime prompt, prompt `shadow_regime_60m_v1_20261002`)

- **Question:** does the next 60 minutes trend? Is the last hour exhausted?
  How likely is the last hour's direction to persist?
- **Output:** JSON with `trending` / `exhausted` (booleans), `persistence`
  (0–1) and `abstain`. No side and no 0–100 scores.
- **Inputs:** 12 normalised facts: z15, z60, rv15, trend score, ADX 15m,
  Donchian location, flow 5m, funding, OI 1h, premium deviation, lead 60 s and
  lead 300 s.
- **Cadence and cost:** logged with the live call. It is spaced at least 15
  minutes apart, capped at 96 calls per UTC day and uses at most 80 output
  tokens. It is skipped whenever the 30 s evidence-hook budget would be at risk.
- **Labels:** these mature from the decision feature snapshot 60-minute label.
  - `trending`: |fwd 60m| ≥ 1 σ, with σ = rv15 × √360.
  - `exhausted`: the next 60 m retraces at least half of the prior 60 m move.
  - `persistence`: the next 60 m return has the same sign as the prior 60 m
    return.
- **Baselines with the same inputs:**
  - expanding climatology (the no-AI look-alike);
  - a fixed `stat_rule` in `ai_regime_shadow.py`.
- **Kill criterion:** after 14 UTC days and at least 300 scored non-abstain
  calls, the prompt and its log are removed unless its Brier score beats both
  baselines on persistence AND on trending. "Beats" means the 2 h-cluster
  bootstrap upper 95% bound of Brier(AI) − Brier(baseline) is below 0.
  `decision_model_report.json` shows `KEEP` or `KILL_REMOVE_PROMPT`.

### H9_LOGISTIC_CHALLENGER_30M_20261002 (laptop-side shadow logistic model)

- **Target:** sign of the 30-minute forward Bitfinex mid return at each shared
  AI call.
- **Features:** 21 causal fields from the decision feature snapshot (tape
  returns, flow, L1 imbalance, spread, rv; trend, ADX, location, funding, OI,
  basis; premium deviation, leads, leader return). They are standardised per
  fold with training-mean imputation.
- **Fit:** L2 logistic (λ = 1), refit once per UTC day. Each refit uses only
  snapshots whose 30 m label matured before that day. Scoring is out of sample
  from the 4th UTC day onward.
- **Gate:** checked after at least 10 OOS days and at least 1500 OOS
  predictions. PASS_FOR_REVIEW needs both:
  - the 2 h-cluster upper 95% bound of Brier(model) − Brier(climatology) is
    below 0;
  - at least 300 sign trades (|p − 0.5| ≥ 0.03) whose cluster-bootstrap lower
    95% bound of the mean 30 m return, net of one full quoted spread, is above 0.
- **Kill:** if it has not passed by 14 OOS days.
- **Scope:** it never trades or changes a tile, and runs only in the laptop
  analyzer.
