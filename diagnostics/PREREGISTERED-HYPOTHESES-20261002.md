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
