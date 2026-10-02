# ANALYZER-GENOME audit - 2026-10-02

Owner: ANALYZER-GENOME (laptop-only; no Fly deploy, no manual v2c move, Bitfinex DISARMED).
Branch `fix/analyzer-genome-20261002`.

## 1. Root cause: why the "Top 100 policy combos" panel collapsed

The `/details` combos section has two different tables, and both had shrunk to almost nothing.

1. **The visible "Top 100" table was never the policy genome.** It is the legacy
   executed-lane cohort (`top_combinations_report.json`). That cohort only has four
   dimensions: ADX bucket, score-gap bucket, entry mode and lane. It counts real paper
   fills of the *current* tiles only. After the roster changed, the cohort narrowed
   from 65 rows (09-30) to 16 (10-01) to 5 (10-02). `all_data` still had 135 rows, but
   the page reads `reports/`.
2. **The rich genome block** (`policy_grid`, from Safe Policy Genome V3.1) was empty, for
   four reasons inside the engine:
   - **Replay window.** `ANALYZER_PROTECTION_REPLAY_MAX_EVENTS=150` meant only 100 of
     2,533 eligible events were replayed (coverage 0.059, alert RED). The engine
     enumerated 21,280 specs over just 28 episodes (train 19, OOS 9).
   - **Conservative world can never fill.** All 21,280 exhaustive rows are
     `UNSUPPORTED` (`NO_CONSERVATIVE_FILLS` / `UNSUPPORTED_OR_MISSING_EXECUTION_EVIDENCE`).
     V3 intents carry no `signed_quantity_constraints`, which
     `research.conservative_limit_fill.evaluate_limit_fill` requires. So
     `profitable_conservative_top_100` is empty by construction.
   - **Display filter.** The page shows only positive rows. Only 56 of 21,280 rows were
     positive, and only in IDEAL_TOUCH. Ideal-touch diagnostic rows fell from 10 to 0
     in the 12:30Z generation, leaving `policy_rows = 0` and status
     `NO_PROFITABLE_CONSERVATIVE_POLICIES`.
   - **Coarse price path.** `_ordered_prices` expects `price`/`mark`/`close`.
     Microstructure rows carry only bid/ask/last, so replay silently falls back to
     1-minute adverse-first OHLC.
3. **Secondary defects:**
   - `regime_breakdown` keys are raw dicts (`{'observed_ts':..,'value':'BULL'}`).
   - Report status is `V3_ORDER_RESOLUTION_INTEGRITY_FAILED` (orphan expected order,
     policy identity contamination, incomplete pre-entry features).
   - Since the 13:14Z generation (ANALYZER-FIDELITY work), live `/api/combos` advertises
     entry/chase/fill/exit/protection/regime dimensions, but with 1 row.

In short, the panel only ever ranked what the current tiles had executed recently. It
never ranked the policy genome over everything we collected.

## 2. Data check per dimension

The genome grid evaluated 1,142 episodes (09-29 14:36Z to 10-02 10:30Z) on 1-second
Bitfinex tape:

- 1,029 v3 AI opportunities. Side comes from the raw AI direction, or else the
  score-led side derived from long/short scores; ties are skipped.
- 113 signal-replay starts that are not duplicates of a v3 opportunity (629
  duplicates were dropped).
- 91 censored: their exit window extends past the tape.
- 197 cross-venue-clock episodes excluded. Those are a different 60-second clock and
  are not AI episodes.

The tape covers 313,366 of 345,075 seconds (09-28 13:39Z to 10-02 13:31Z).

| Dimension | Collected? | Analyzed now? | Sufficient? |
|---|---|---|---|
| Entry offset (taker; 0.05-0.50 %, incl. 0.27 / 0.30) | Yes (1 s tape) | Yes, 11 values | Screening yes (hundreds of fills per policy); qualification no (2.5 days, one regime flip) |
| Chase windows / 50 % remaining-gap moves / 180 s reprice / "wait patiently" (TTL 15/30/60 min) | Yes (tape) | Yes: 7 chase identities x 3 TTLs | Same as above; no queue position or depth modelled |
| Order type (taker vs resting limit) | Yes | Yes; BBO_MARKETABLE vs IDEAL_TOUCH worlds | Conservative depth/queue world **blocked** (no signed quantity constraints) |
| % hard stop | Yes | Partly: 30 % (engine screen) and 40 % margin (active registry) only | Thin: only 2 values |
| Initial vs trailing SL (ATR SL k sweep, ATR trail SL1.5/arm0.75/trail1, Chandelier 1.5) | Yes | Yes: 9 ATR-stop values, 4 trail, 5 chandelier | Yes for screening |
| Thesis-cut-fast / time cut | Yes | Yes: thesis 2 values; time stop 30/60/90/120 min and registry 60 min | Yes |
| Scenario-C profit ladder | Yes | Yes, ladder x ATR-stop sweep | Yes |
| ATR target 2.5, hybrid runner partials, MFE giveback 20 % | Yes | Yes | Yes |
| R-multiples | Derived | Yes: avg R per policy (1R = tightest initial loss bound) | Yes |
| Follow vs fade of the signalled side | Yes | Yes (FOLLOW / FADE) | Yes |
| ATR at signal | Partly: `atr14_pct_3m` missing or dead in recent snapshots | 3-minute ATR derived from tape for 329 episodes | OK with fallback |
| Regime / ADX per episode | **Dead fields** in recent feature snapshots | Not used for slicing | **Blocked** (post-freeze) |
| Fees / funding | Not modelled (same as the canonical replay) | Labelled in the report | Caveat |
| Live paper outcomes | Yes (lifecycle terminal rows) | Yes, per lane as LIVE_PAPER (never pooled) | Small N per lane |

## 3. What the fix does

Everything below is laptop-side research only; nothing can place an order.

- **`research/genome_grid_study.py`** (new). Simulates every collected episode across
  6,676 policies per world:
  - entry offset x chase x TTL x the engine's 70 `protection_screen()` exits, plus the
    active-registry exits (read from `combo_pathway_config`; no second tile list);
  - FOLLOW/FADE, in two fill worlds.
  - **Parity:** `fast_replay` is a vectorised twin of
    `research_v3_policy_replay.replay_protected_policy`. Each run replays a 406-check
    sample through the canonical evaluator; the status is MATCH.
  - **Holdout:** chronological 70/30. Policies are **ranked by train EV only**; the
    holdout confirms or rejects (CONFIRMED / FAILED_HOLDOUT / NEGATIVE_TRAIN /
    INSUFFICIENT).
  - **Statistics:** Wilson CI. The report notes that overlapping paths make the CIs
    optimistic.
  - **Provenance:** every row is labelled `SIMULATED_COUNTERFACTUAL`. Live paper is
    listed separately as `LIVE_PAPER`, with registry status ACTIVE or
    RETIRED_QUARANTINED. Retired tiles appear only as parameter sets.
  - **Caching:** an incremental per-episode cache keyed by a grid signature. A full
    pass takes about 4-10 minutes; refreshes compute only new episodes.
  - **Scheduling:** it skips while the segment analyzer is in its ANALYZER phase.
    Cycles run back to back, so it starts in the I/O-bound phases at below-normal
    priority.
- **`/details` combos section.** A new "Policy Genome Grid - simulated over all
  collected data" block sits above the legacy tables. It shows:
  - KPIs (episodes, span, policies, ranked, holdout confirmed/failed, parity);
  - a per-axis table (best train-selected value with its holdout result);
  - Top 100 per fill world, with a "holdout-confirmed only" filter;
  - live paper per lane.
  The legacy cohort stays, labelled as legacy.
- **Section APIs on :9001.**
  - `/api/genome-grid`;
  - `/api/sections`, an index of every `/details` section and decision page. Each
    entry lists the JSON endpoints derived from the page's own loaders;
  - `/api/sections/<id>`;
  - `/api/sections/health`:
    - per section: populated, fresh, and API OK;
    - combos: genome present / fresh / axes complete / ranked / engine parity /
      coverage vs collected;
    - Safe Genome: replay window / shortlist / integrity.
- **Self-aware check (`sections` job, every 2 h).** Code is
  `scripts/self_aware/analyzer_sections.py`; the route is `/api/selfaware/sections`.
  It produces three findings:
  - `analyzer.sections`: the core sections are readable, populated, and at most 3 h old;
  - `analyzer.dimensions`: the Top-100 panel is backed by the full genome, and the Safe
    Genome shortlist and replay coverage are OK;
  - `analyzer.consistency`: the grid is at most 3 h old (AMBER) / 12 h (RED), covers at
    least 50 % of collected opportunities, has parity MATCH, and no core section shrank
    by more than 80 % between checks.
  A collapse is AMBER or RED, never GREEN.
- **Runner and task.** `scripts/run-genome-grid.ps1` and
  `scripts/register-genome-grid-task.ps1` (DoxxedGenomeGrid, every 2 h, runs the v2c
  code).

## 4. Result (13:49Z run, revision bc532125e plus this branch)

Holdout cut: 2026-10-01 16:41Z. Of 6,592 rankable policies:

- 5,330 are NEGATIVE_TRAIN;
- 1,254 are FAILED_HOLDOUT;
- **8 are CONFIRMED, all in IDEAL_TOUCH** (17-37 OOS fills, EV $0.0006-0.016 per
  fill);
- **0 are confirmed in BBO_MARKETABLE.**

The train-period leaders are FADE entries with the registry's 1-hour time exit plus 40 %
hard stop (the Trend-Fade shape). They flip to clearly negative after the cut. Live paper
agrees: every active lane is negative so far (Trend-Fade 60: 24 closes, -$0.37).

**There is no robust edge in the collected data yet.** The grid now says so explicitly,
instead of showing an empty or four-dimension table.

## 5. Blocked: needs data or engine work

- **Engine (requested from ANALYZER-FIDELITY, no edit by me):**
  - widen or segment the protection replay window;
  - make the conservative fill path accept 1 s BBO evidence, or have intents carry
    `signed_quantity_constraints`;
  - make `_ordered_prices` use side-correct bid/ask 1 s marks;
  - unwrap regime dict keys.
- **POST-FREEZE (Fly collector):**
  - feature snapshot dead fields (`atr14_pct_3m`, regime, ADX null);
  - `signal_price` null on 204 opportunity rows (the grid uses the tape mid instead);
  - counterfactual `epoch_id` / `opportunity_id` null;
  - `pre_entry_features.captured_at_ts` null;
  - `opportunity_capture.jsonl` stale for about 18 h;
  - signed quantity constraints on v3 intents;
  - L2 depth for queue-position fills.
- **Self-aware go-live:** the `self-aware-live` checkout (owned by SELF-AWARE) must be
  re-pinned to the merge commit to run the `sections` job. Requested on the WALL.