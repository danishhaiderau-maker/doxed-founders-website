# Section-contract audit - 2026-10-02

Owner: SECTION-CONTRACTS. Scope: every section, page, table and card of Analyzer :9001 (all /details tabs and decision pages, `analyzer-exports\latest`, `analysis-archive`), the Fly dashboard (public API plus the laptop-chain runtime/relay snapshots), Self-aware :9021 and the health watcher :9011. Laptop-only; no Fly deploys (freeze until 2026-10-04T11:10Z); public Fly API called at most 6 times per heavy pass, disk-cached for 1 h; the admin token was never used.

**Why.** The :9001 "Top 100 policy combos" collapsed to ADX x score gap x DIRECT x lane while every check stayed GREEN. The checks proved liveness and freshness, not content. ANALYZER-GENOME (ac5e2cd7) owns that section's root cause and fix. This audit looks for every other gap of the same class and adds a generic content contract that also covers Top-100.

**Result.** 86 section contracts now run inside self-aware: light freshness every 5 min, the full set every 2 h. Final measurement at 2026-10-02T14:38Z, after the first successful analyzer cycle since 13:20Z: **55 GREEN, 26 AMBER, 5 RED**. Within a day of history the drift detector already caught a live collapse (gap 2). Coverage: **40/40** /details sections and decision pages have a contract. Archive drift: no report vanished or shrank across the 8 archived generations (but see gap 14: the archive keeps only 12 reports per generation).

## Ranked gaps (content wrong or missing while liveness checks stay GREEN)

| # | Severity | Section(s) | Gap | Owner / fix path |
|---|---|---|---|---|
| 1 | RED | `/details#combos` Top-100, `/safe-policy-genome-v3.1`, `/static-policies`, `/details#spread-perf` | **Genome collapse** (known). `policy_grid.search_counts.unique_policies_evaluated=21280` but `materialized_policy_rows=0`; page falls back to the 5-row legacy table over adx/directional_spread/entry_mode/spread_bucket/lane with `entry_mode` constant DIRECT and `spread_bucket` constant "0-1". Static policies `rows_shown=0` of 21280 evaluated; genome candidate-screen leaders empty; spread-perf has one bucket and no explanation. | ANALYZER-GENOME (root cause and fix). Contract `analyzer.combos` (genes, min_distinct, invariant, `combos_genome` reconciler) now catches it generically. |
| 2 | RED | `/details#genome` Safe Policy Genome, `/evidence-maturity`, `/static-policies` | **New, caught live by drift: the policy search shrank 76x.** In the 14:37Z generation `unique_policies_evaluated` fell to **280** from a baseline median of **21,280** (7 prior passes); genome `descriptive_top_100` dropped to 8 rows. New blockers `MIXED_OR_PRE_CUTOFF_V3_EVIDENCE_EXCLUDED` and `CAUSAL_IDENTITY_ALIAS_EXCLUDED` suggest a deliberate evidence-exclusion change, but no page says the search space shrank. | ANALYZER-GENOME / ANALYZER-FIDELITY: confirm intent; if deliberate, show the before/after search size on the page and re-baseline. |
| 3 | RED | `/details#regime` (Historical Regime & ADX) | **New: section is dead.** The tab fetches `/api/report/regime_leaderboard.json`; the file is not in the published report manifest and exists nowhere under exports, v2c data or report-history, so the endpoint 404s and the tab renders empty with no message. | Analyzer :9001 owner: produce the report or remove the nav entry (laptop-side). Requested on WALL. |
| 4 | RED | `/details#ai` | AI section is silently hollow: `feature_attribution.regime` and `.context` empty with no stated reason, `calibration_buckets` empty, `fingerprints.avg_ai_confidence` 0 on all 20 rows, `ai_verdict_coverage.status=UNKNOWN`. Ties to self-aware `data.dead_fields` (`adaptive_entry_decisions.ai_feature.win_prob=0`, dead returns in the AI prompt). | Producer fields on Fly: POST-FREEZE. Analyzer side should declare why the tables are empty (laptop). |
| 5 | AMBER | Fly dashboard analyzer panels (`/api/analyzer/summary`, `/api/analyzer/genome`) | Both answer 200 with `ok=false, mirror_available=false`: the public Fly dashboard shows no analyzer data at all, while its health tiles stay green. | POST-FREEZE (Fly-side mirror/upload path). |
| 6 | AMBER | `/api/accumulator` (research accumulator) vs canonical cohort | PnL with opposite sign and no unit: TF60 +2.22 vs cohort -$0.460; XVP +3.41 vs -$0.591 (14:38Z). While the analyzer cohort stalled (13:20-14:37Z) counts diverged too (XVL 22 vs 12, XVP 101 vs 71); they reconcile again after the 14:37Z generation. | Analyzer owner: label the unit (R? bp?) and watermark the accumulator to the cohort generation. |
| 7 | AMBER | `/details#evidence-coverage`, `/evidence-points`, policy evidence library, `/api/integrity` | Evidence binding is near zero: `episode_coverage.exact` 0/1402; exactly-bound decisions 148/7320 (2%); conservative evaluator ingested 3674/7320 rows (50%); integrity `ok=false` (HTTP 503), so every evidence page inherits BLOCKED. | ANALYZER-FIDELITY (TTL signature split; post-freeze runtime part). |
| 8 | AMBER | `/details#lanes` Current Lanes | Executed and counterfactual ledgers `UNAVAILABLE_CURRENT_GENERATION`; `all_time_ev/fills/pnl` columns dead. Earlier today the tab showed 22/1/9/54 closes vs cohort 23/2/12/71 and the decision page 86 vs 108 trades with `consistent=true`. Both self-healed in the 13:1x generation; contracts `lanes_vs_cohort` and `decision_vs_cohort` now guard against regression. | ANALYZER-FIDELITY (generation binding). |
| 9 | AMBER | `/api/research-design` | 125 MB JSON (`entry_baseline_replay.episode_receipts` 2336 receipts inline). The page risks browser/dashboard memory crashes; the contract caps reads at 20 MB and flags OVERSIZE. | Analyzer owner: paginate receipts or move them behind a drill-down endpoint. |
| 10 | AMBER | `/details#exit-combos`, `#exit-reason-leak`, `#horizon`, `#ladder-sim`, cross-world, missed-opportunity, conservative-fill | Exit evidence mostly empty or dead: conservative BBO-depth and shadow-lab classes empty (declared); shadow `avg_left_usd/avg_mfe_margin_pct/avg_leakage_margin_pct/avg_stop_slippage` null; cross-world `NOT_COMPUTABLE`, pairwise comparisons all 0; post-exit horizon coverage max 48.5%, so recovery rates are hidden; ladder replays 14/731 (1.9%); 293 missed-opportunity proofs all `INSUFFICIENT_EVIDENCE`; conservative-fill `accumulated_qty` 0 on 1114 receipts. | Mostly needs Fly-side post-exit capture: POST-FREEZE. `accumulated_qty` is analyzer-side. |
| 11 | AMBER | `/details#archives` "Past analysis" | Reads legacy `DATA_ROOT/past_analysis`, so it is always empty, while `analysis-archive` holds 8 generations today (only `/history` shows them). Silent: no message. | Analyzer owner (laptop): point at analysis-archive or drop the card. |
| 12 | AMBER | `/details#runtime-incidents`, `#pathway-audit` | `current_process.bot_instance_id/started_at/source_revision` null; `lane_memory_violation.verdict` null, so the audit renders without a verdict. | Analyzer owner; the runtime half needs the Fly snapshot to carry the fields (POST-FREEZE). |
| 13 | AMBER | `/api/best-policy` | `deployed_policy_collection.policies` does not list the four active tiles (roster mismatch vs Fly `active_tile_lanes`). | Analyzer owner. |
| 14 | AMBER | `analysis-archive` | Each generation archives only 12 reports (no top_combinations, lanes, genome); the full ~104-report `report-history` started today. Historical drift baselines for most sections are missing; contract history now accumulates them (30-day retention). | ANALYZER-FIDELITY / archive owner: archive the full set per generation (#315 started this). |
| 15 | AMBER | analyzer cadence (`chase_*`, exit, evidence-points, hypotheses, streams_health) | Cycles at 13:20Z, 13:34Z and 14:15Z ended with exit codes 3, 6 and 3; reports reached 80 min old, hypotheses/streams `STALE`, insights `PARTIAL`. The contracts surfaced this as content staleness on 8 sections; it cleared with the successful 14:37Z cycle. | ANALYZER-FIDELITY. |
| 16 | INFO | genome, best-policy, evidence-maturity collections | 14 lanes (10 retired) in `lane_decision_outcomes`; labelled historical, so not flagged. Contract `no_retired` is on for every current-cohort table. | Registry-driven; no action. |
| 17 | INFO | chase sections | Chase reports present for taker tiles that never chase (`chase_events` 0 of 1087 orders). Honest but noisy. | Presentation; analyzer owner. |
| 18 | INFO | fees / costs columns | `trading_fees_usd` 0 everywhere, `latency_cost` null, slippage 0: by design (`cost_model=BITFINEX_ZERO`, declared). Contracts `allow_constant` these; the UI should state the cost model on the card. | Analyzer owner (label). |
| 19 | INFO | self-aware data catalog; :9001 `/api/streams/self-aware-data` | 27 of 100 streams curated (73 auto-discovered); the :9001 stream link 404s until the analyzer code refresh picks up #311. | SELF-AWARE / analyzer refresh. |

**Relation to ANALYZER-GENOME #324.** #324 (merged during this work) added `/api/genome-grid`, `/api/sections{,/health}` on :9001 and a self-aware `sections` job (`analyzer.sections/dimensions/consistency`). The two are complementary and both stay: #324 checks :9001 sections from inside the analyzer; section contracts cover all five surfaces, reconcile across them and keep drift history. Contracts `analyzer.genome_grid` and `analyzer.sections_health` watch the #324 endpoints (GREEN at 14:38Z).

**HTML-only sections.** None found: every /details tab and decision page is backed by a JSON endpoint (`/api/<section>` or `/api/report/<file>.json`). No new :9001 API was needed. `/api/selfaware/contracts/<id>?rows=N` gives the raw-row drill-down for every section.

## What was implemented (PR below)

- `scripts/self_aware/section_contracts.json`: declarative registry, one spec per section (86). Each spec declares source (`http`, `fly`, `file`, `export_table` or `self`), freshness, required/live fields, tables (min rows, columns, live columns, min distinct values, policy-genome `genes`, roster vs Fly `active_tile_lanes`, no retired lanes), counters, invariants, ratios, expected values, allowed HTTP codes, payload cap, a named reconciler and the /details sections it `covers`.
- `scripts/self_aware/contracts.py`: evaluator, fetcher (Fly public-only, capped, cached, kill switch `SELF_AWARE_CONTRACTS_FLY=0`), drift vs history (`DRIFT_COLLAPSE`, `DRIFT_DIMS_DROPPED`), archive drift over `generations` and `report-history`, AST coverage of `REPORT_NAV_GROUPS`/`DECISION_NAV_LINKS`, and reconcilers: `lanes_vs_cohort`, `decision_vs_cohort`, `accumulator_vs_cohort`, `export_tiles_vs_cohort`, `export_ledger`, `combos_genome`, `fly_roster`, `fly_analyzer_mirror`, `data_health_vs_selfaware`.
- Engine jobs: `contracts_light` (5 min) and `contracts` (2 h, deferred while an analyzer cycle is in flight, up to 45 min). History goes to `res_contract_history` (30 days). Findings: `contract.analyzer|fly|exports|selfaware|watcher`, `contract.collapse`, `contract.archive_drift` and `contract.coverage`, which reach :9001 Alerts via `selfaware.*` alarms. The health document carries a `contracts` summary block.
- API and UI on :9021: `/contracts`, `/api/selfaware/contracts?surface=&status=`, `/api/selfaware/contracts/registry` and `/api/selfaware/contracts/<id>?rows=N&history=N`; linked from the overview.
- Runbook: `docs/SELF_AWARE_RUNBOOK.md#section-contracts` plus one anchor per finding.
- Tests: `scripts/test_self_aware_contracts.py` (16 tests), registered in `laptop-tests.yml`. All 64 self-aware tests pass (including #324's).

## POST-FREEZE (needs Fly; after 2026-10-04T11:10Z)

1. Fly analyzer panels: restore the mirror/upload so `/api/analyzer/summary` and `/api/analyzer/genome` carry data (gap 5).
2. AI producer fields: `ai_feature.win_prob`, prompt returns, confidence, so attribution and calibration can populate (gap 4).
3. Post-exit path capture for horizon, ladder and shadow exit evidence (gap 10).
4. Runtime snapshot: `bot_instance_id`, `started_at` and `source_revision` for runtime incidents (gap 12).
5. Integrity TTL signature split (ANALYZER-FIDELITY, gap 7).
6. `collection.xvl_evaluator` absent (XVL-FEED, already tracked).
7. Fly chase panels (CHASE-BUCKETS request): export `chase_analytics` into the runtime snapshot (#317). Contract `fly.chase_buckets` (follow-up PR) reports INFO while the field is absent and goes RED unless the status is `VERIFIED_RECENT_SNAPSHOT` or `NOT_APPLICABLE`.

## Requests to other owners

- **BLINDSPOT-CLOSE (system_health.py, :9011).** The watcher already monitors the new jobs through `engine.jobs`/`cadence_sec`. To surface the contract summary on :9011, add one check next to `selfaware.engine` (`sa` is the health doc already fetched):

```python
c = sa.get("contracts") if isinstance(sa.get("contracts"), Mapping) else None
if c:
    counts = c.get("counts") or {}
    st = RED if counts.get("RED") else AMBER if counts.get("AMBER") else GREEN
    add(check("selfaware.contracts", "selfaware", st,
              f"section contracts {counts} (heavy {c.get('heavy_at')}); collapse: "
              + (", ".join(x["id"] for x in c.get("collapse") or []) or "none"),
              "every dashboard section honours its content contract (:9021/contracts)",
              "" if st == GREEN else "open http://127.0.0.1:9021/contracts and drill into the offender",
              fields={"counts": counts, "surfaces": c.get("surfaces"), "offenders": c.get("offenders")}))
```

- **ANALYZER-GENOME.** When the canonical grid ships, keep gene column names containing `entry_offset`, `chase`, `stop`, `trail`, `thesis`, `ladder`, `atr`, `mfe` and `r_multiple` (the `analyzer.combos` genes); `combos_genome` also reads `analyzer-exports/genome-grid/genome_grid_report.json` if present.
- **Analyzer :9001 owner.** Gaps 3, 6, 9, 11 and 13 are laptop-side `research_dashboard.py`/report fixes. I did not edit `research_dashboard.py` because it is claimed.
- **ANALYZER-FIDELITY.** Gaps 2, 7, 8, 14 and 15, especially the failed analyzer cycles (exit 3 at 13:31Z, exit 6 at 14:10Z, exit 3 at 14:15Z; 14:37Z succeeded).

## Full inventory (generated from the 14:38Z heavy pass)

Columns: Populated = no dead/empty/constant violations; Dims = expected tables, then actual metrics; Reconciled = named reconciler and its verdict; Drift = rolling-history drift (history starts with this release).

| Section | API / source | Depends on | Populated? | Dims / rows expected ΓåÆ actual | Reconciled? | Drift? | Verdict |
|---|---|---|---|---|---|---|---|
| `analyzer.accumulator` Research accumulator | :9001/api/accumulator | research accumulator sqlite | yes | fields only | MISMATCH (accumulator_vs_cohort) | none | **AMBER** UNIT_OR_SIGN_MISMATCH; UNIT_OR_SIGN_MISMATCH |
| `analyzer.ai` AI comparison & calibration | :9001/api/ai | ai_calibration_report.json, ai_funnel_report.json, ai decisi | no: DEAD_COLUMN, EMPTY_SILENT | calibration_buckets: ΓëÑ1 rows; feature_attribution.regime: ΓëÑ1 rows ΓåÆ calibration_buckets=0, feature_attribution.regime=0, feature_attribution.context=0, fingerprints=20 | n/a | none | **RED** EMPTY_SILENT; EMPTY_SILENT; DEAD_COLUMN; COVERAGE_UNKNOWN |
| `analyzer.ai_challengers` AI vs challengers | :9001/api/ai-challengers | ai_challenger_report.json, challenger shadow calls | yes | report.challengers: ΓëÑ3 rows; report.primary_comparisons: ΓëÑ3 rows ΓåÆ report.challengers=9, report.primary_comparisons=16, report.random_placebo=10 | n/a | none | **GREEN** |
| `analyzer.alerts` Alerts page | :9001/api/system-health/alerts | laptop-chain/health/alarms.jsonl | yes | alerts: ΓëÑ1 rows; active: ΓëÑ0 rows ΓåÆ alerts=145, active=15 | n/a | none | **GREEN** |
| `analyzer.archive_history` History & retention (analysis archive) | :9001/api/archive | C:/DoxxedCrypto/analysis-archive generations + rollups, long | yes | fields only | n/a | none | **GREEN** |
| `analyzer.archives` Archives (details tab: sessions + past analysis) | :9001/api/archives | archive sessions dir | yes | sessions: ΓëÑ1 rows ΓåÆ sessions=3 | n/a | none | **GREEN** |
| `analyzer.best_policy` Best policy research | :9001/api/best-policy-research | best_policy_research_report.json, safe policy genome | yes | deployed_policy_collection.policies: ΓëÑ1 rows ΓåÆ deployed_policy_collection.policies=4 **(ROSTER_MISMATCH)** | n/a | none | **AMBER** ROSTER_MISMATCH |
| `analyzer.chase_attribution` Execution: chase attribution | :9001/api/chase | chase_attribution_report.json, order lifecycle | yes | executed_buckets: ΓëÑ1 rows; shadow_buckets: ΓëÑ2 rows ΓåÆ executed_buckets=0, shadow_buckets=1 | n/a | none | **GREEN** |
| `analyzer.chase_delay` Historical chase delay | :9001/api/chase-delay | chase_delay_report.json | yes | lanes: ΓëÑ1 rows ΓåÆ lanes=0 | n/a | none | **GREEN** |
| `analyzer.chase_policy_lab` Execution: chase policy lab | :9001/api/chase-policy-lab | chase_policy_lab_report.json | yes | ranked_schedules: ΓëÑ2 rows ΓåÆ ranked_schedules=1 | n/a | none | **GREEN** |
| `analyzer.chase_threshold` Execution: chase threshold | :9001/api/chase-threshold | chase_threshold_report.json | no: DEAD_COLUMN | thresholds: ΓëÑ2 rows; shadow_thresholds: ΓëÑ1 rows ΓåÆ thresholds=1, shadow_thresholds=1 | n/a | none | **AMBER** DEAD_COLUMN |
| `analyzer.chronological_oos` Chronological OOS | :9001/api/chronological-oos | v3.1 evidence | yes | rows: ΓëÑ1 rows ΓåÆ rows=0 | n/a | none | **GREEN** |
| `analyzer.combos` Top 100 policy combos | :9001/api/combos | safe_policy_genome_v3_report.json policy_grid, top_combinati | no: EMPTY_DECLARED | top100: ΓëÑ10 rows 9 genes; legacy: ΓëÑ1 rows distinct entry_modeΓëÑ2,spread_bucketΓëÑ2 ΓåÆ top100=0, legacy=6 **(DIMENSION_COLLAPSE)** | yes (combos_genome) | DRIFT_COLLAPSE | **RED** EMPTY_DECLARED; DIMENSION_COLLAPSE; DIMENSION_COLLAPSE; DIMENSION_COLLAPSE |
| `analyzer.conservative_fill` Conservative fill research | :9001/api/conservative-fill-research | conservative_fill_descriptive_report.json, BBO/depth tape | no: DEAD_COLUMN | receipts: ΓëÑ10 rows ΓåÆ receipts=1265 | n/a | none | **AMBER** DEAD_COLUMN |
| `analyzer.cross_world` Cross-world evidence | :9001/api/cross-world-evidence | cross_world_evidence_report.json, Bitfinex copy fidelity, co | no: DEAD_COLUMN, EMPTY_DECLARED | joined_rows: ΓëÑ1 rows; pairwise: ΓëÑ1 rows ΓåÆ joined_rows=0, pairwise=10 | n/a | none | **AMBER** EMPTY_DECLARED; DEAD_COLUMN; DEAD_COLUMN |
| `analyzer.data_health` Data health (analyzer) | :9001/api/streams/data-health | data_health_report.json, event_study_report.json | yes | event_study.hypotheses: ΓëÑ1 rows ΓåÆ event_study.hypotheses=5 | yes (data_health_vs_selfaware) | none | **GREEN** |
| `analyzer.decision` Decision page | :9001/api/decision | ai_funnel_report.json, selector, forward trial, rankings, re | yes | tiles: ΓëÑ1 rows; freshness: ΓëÑ3 rows ΓåÆ tiles=4, freshness=8 | yes (decision_vs_cohort) | none | **GREEN** |
| `analyzer.downloads` Downloads (GPT audit bundle) | :9001/api/gpt-audit | gpt audit zip, genome report summary | yes | file_index: ΓëÑ100 rows ΓåÆ file_index=296 | n/a | none | **GREEN** |
| `analyzer.dynamic_policies` Dynamic policies | :9001/api/dynamic-policy-research | v3/dynamic_policy_analysis_report.json, verified input recei | yes | regimes: ΓëÑ1 rows ΓåÆ regimes=0 | n/a | none | **AMBER** INPUT_UNVERIFIED |
| `analyzer.evidence_coverage` Evidence coverage | :9001/api/evidence-coverage | evidence_coverage_triage_report.json, decision/lifecycle/exe | yes | fields only | n/a | none | **AMBER** COVERAGE_UNKNOWN |
| `analyzer.evidence_maturity` Evidence maturity | :9001/api/evidence-maturity | v3.1 evidence | yes | fields only | n/a | DRIFT_COLLAPSE | **RED** DRIFT_COLLAPSE |
| `analyzer.evidence_points` Evidence points | :9001/api/evidence-points | tile_evidence_points_report.json | yes | fields only | n/a | none | **GREEN** |
| `analyzer.exit_combos` Execution: exit combos | :9001/api/exit-combos | exit_combinations_report.json, conservative BBO/depth replay | no: DEAD_COLUMN, EMPTY_DECLARED | executed_top: ΓëÑ1 rows; conservative_top: ΓëÑ1 rows ΓåÆ executed_top=2, conservative_top=0, shadow_top=0, shadow_stop_matrix=4 | n/a | none | **AMBER** EMPTY_DECLARED; EMPTY_DECLARED; DEAD_COLUMN |
| `analyzer.exit_leakage` Historical exit leakage | :9001/api/leakage | top_leakage_report.json, post-exit paths | yes | trades: ΓëÑ5 rows ΓåÆ trades=50 | n/a | none | **GREEN** |
| `analyzer.exit_reason_leak` Execution: exit reason leak | :9001/api/exit-reason-leak | exit_leakage_by_reason_report.json | no: DEAD_COLUMN | executed_reasons: ΓëÑ1 rows; shadow_reasons: ΓëÑ1 rows ΓåÆ executed_reasons=4, shadow_reasons=4 | n/a | none | **AMBER** DEAD_COLUMN; DEAD_COLUMN; DEAD_COLUMN |
| `analyzer.features` Historical edge & features | :9001/api/features | feature_importance_report.json | yes | features: ΓëÑ5 rows ΓåÆ features=7 | n/a | none | **GREEN** |
| `analyzer.findings` Historical findings | :9001/api/findings | research_findings, coverage summary | no: DEAD_FIELD | findings: ΓëÑ3 rows ΓåÆ findings=12 | n/a | none | **AMBER** DEAD_FIELD; DEAD_FIELD |
| `analyzer.genome` Safe Policy Genome V3.1 (details tab) | :9001/api/genome | safe_policy_genome_v3_report.json, protection replay window, | no: EMPTY_DECLARED | candidate_screen.descriptive_top_100: ΓëÑ10 rows ΓåÆ candidate_screen.descriptive_top_100=8 | n/a | DRIFT_COLLAPSE | **RED** EMPTY_DECLARED; DRIFT_COLLAPSE |
| `analyzer.genome_grid` Full-history policy genome grid (canonical Top-100) | :9001/api/genome-grid | genome_grid_study.py, analyzer-exports/genome-grid/genome_gr | yes | fields only | n/a | none | **GREEN** |
| `analyzer.health` :9001 liveness + generation parity | :9001/api/health | dashboard process, report manifest, mirror sync receipt | yes | fields only | n/a | none | **GREEN** |
| `analyzer.horizon` Historical recovery (horizon) | :9001/api/horizon | horizon_profitability_report.json, post-exit path capture | yes | horizons: ΓëÑ4 rows ΓåÆ horizons=6 | n/a | none | **AMBER** LOW_COVERAGE |
| `analyzer.hypotheses` Hypotheses (strategy lab) | :9001/api/hypotheses | analyzer export hypotheses/family_tests, tape | yes | hypotheses: ΓëÑ3 rows; family_tests: ΓëÑ10 rows ΓåÆ hypotheses=6, family_tests=44 | n/a | none | **GREEN** |
| `analyzer.insights` Agent insights | :9001/api/insights | export, archive, watcher, Fly snapshot, transfer | no: CONSTANT_COLUMN | components: ΓëÑ5 rows ΓåÆ components=7 | n/a | none | **AMBER** CONSTANT_COLUMN |
| `analyzer.integrity` Analyzer integrity checks | :9001/api/integrity | analyzer_integrity_report.json, policy lifecycle reconciliat | yes | checks: ΓëÑ3 rows ΓåÆ checks=5 | n/a | none | **AMBER** HTTP_STATUS; INTEGRITY_INVALID |
| `analyzer.ladder_sim` Historical ladder simulator | :9001/api/ladder-sim | exit_ladder_simulator_report.json, matched executed replays | yes | profiles: ΓëÑ3 rows ΓåÆ profiles=7 | n/a | none | **AMBER** LOW_COVERAGE |
| `analyzer.lanes` Current lanes | :9001/api/lanes | benchmark_vs_lanes_report.json, lane_lab_pnl_ledger.json, mi | yes | lanes: ΓëÑ1 rows ΓåÆ lanes=4 | yes (lanes_vs_cohort) | none | **AMBER** LEDGER_NOT_CURRENT; LEDGER_NOT_CURRENT |
| `analyzer.missed_opportunity` Missed-opportunity proof | :9001/api/missed-opportunity-proof | missed opportunity arm receipts, checkpoint counterfactuals | no: CONSTANT_COLUMN | proofs: ΓëÑ1 rows ΓåÆ proofs=322 | n/a | none | **AMBER** CONSTANT_COLUMN |
| `analyzer.overview` Overview (summary) | :9001/api/summary | research_compact_summary.json, mirror execution ledger | yes | fields only | n/a | none | **GREEN** |
| `analyzer.partial_reduction` Partial reduction | :9001/api/partial-reduction | partial reduction receipts | yes | fields only | n/a | none | **GREEN** |
| `analyzer.past_analysis` Past analysis (details Archives tab) | :9001/api/past-analysis | DATA_ROOT/past_analysis (legacy) - not analysis-archive | no: EMPTY_SILENT | analyses: ΓëÑ1 rows ΓåÆ analyses=0 | n/a | none | **AMBER** EMPTY_SILENT |
| `analyzer.pathway_audit` Pathway audit (tile independence) | :9001/api/pathway-audit | tile_independence_report.json, lane memory, ai scan role | yes | fields only | n/a | none | **AMBER** NULL_VERDICT |
| `analyzer.policy_evidence_library` Policy evidence library | :9001/api/policy-evidence-library | derived/policy-evidence cache, decision bindings | yes | fields only | n/a | none | **AMBER** LOW_COVERAGE; LOW_COVERAGE |
| `analyzer.regime` Historical regime & ADX | :9001/api/report/regime_leaderboard.json | regime_leaderboard.json | yes | fields only | n/a | none | **RED** NOT_JSON |
| `analyzer.report_explorer` Report explorer + manifest | :9001/api/manifest | research_report_manifest.json, tile registry | yes | active_tiles: ΓëÑ1 rows; reports: ΓëÑ60 rows ΓåÆ active_tiles=4, reports=96 | n/a | none | **GREEN** |
| `analyzer.research_design` Entry & regime evidence (research design) | :9001/api/research-design | policy_evidence_library manifest, entry_baseline_replay_repo | yes | shadow_tiers.rows: ΓëÑ1 rows | n/a | none | **AMBER** OVERSIZE |
| `analyzer.risk_drawdown` Risk and drawdown | :9001/api/risk-drawdown | v3.1 evidence | yes | rows: ΓëÑ1 rows ΓåÆ rows=0 | n/a | none | **GREEN** |
| `analyzer.runtime_incidents` Runtime incidents | :9001/api/runtime-incidents | mirror runtime incident receipts, Fly platform history | no: DEAD_FIELD | application_incidents: ΓëÑ1 rows ΓåÆ application_incidents=10 | n/a | none | **AMBER** DEAD_FIELD; DEAD_FIELD; DEAD_FIELD |
| `analyzer.safe_policy_genome_page` Safe Policy Genome V3.1 page | :9001/api/safe-policy-genome-v3.1 | safe_policy_genome_v3_report.json | no: EMPTY_DECLARED | candidate_screen.descriptive_top_100: ΓëÑ10 rows; candidate_screen.drawdown_control_leaders: ΓëÑ1 rows ΓåÆ candidate_screen.descriptive_top_100=8, candidate_screen.drawdown_control_leaders=0, candidate_screen.scenario_c_atr_st | n/a | none | **AMBER** EMPTY_DECLARED; EMPTY_DECLARED; EMPTY_DECLARED |
| `analyzer.sections_health` :9001 section index health (ANALYZER-GENOME) | :9001/api/sections/health | dashboard_sections.py | yes | sections: ΓëÑ30 rows ΓåÆ sections=38 | n/a | none | **GREEN** |
| `analyzer.self_aware_link` Data health -> self-aware data awareness link | :9001/api/streams/self-aware-data | :9001 code refresh after #311, :9021 /api/selfaware/data | yes | fields only | n/a | none | **GREEN** |
| `analyzer.shadow_research` Shadow research | :9001/api/shadow-policy-research | shadow counterfactual terminals | yes | fields only | n/a | none | **GREEN** |
| `analyzer.spread_perf` Legacy gap performance | :9001/api/spread-performance | top_combinations_report.json (legacy executed) | no: EMPTY_SILENT | buckets: ΓëÑ2 rows distinct spread_bucketΓëÑ2 ΓåÆ buckets=1 | n/a | none | **AMBER** EMPTY_SILENT |
| `analyzer.static_policies` Static policies | :9001/api/static-policy-research | safe_policy_genome_v3_report.json | no: GENOME_EMPTY | profitable_policies: ΓëÑ1 rows ΓåÆ profitable_policies=0 | n/a | none | **AMBER** GENOME_EMPTY |
| `analyzer.status` :9001 analyzer status + generation receipt | :9001/api/status | analyzer generation receipt, manifest, tile registry signatu | yes | generation_receipt.failed_required_studies: ΓëÑ0 rows ΓåÆ generation_receipt.failed_required_studies=0 | n/a | none | **GREEN** |
| `analyzer.streams_health` Streams health | :9001/api/streams/health | analyzer export stream_health | yes | streams: ΓëÑ15 rows; unhealthy: ΓëÑ0 rows ΓåÆ streams=24, unhealthy=4 | n/a | none | **GREEN** |
| `analyzer.system_health_banner` :9001 system-health banner | :9001/api/system-health | laptop-chain/health/system-health-latest.json, Fly /api/stat | yes | failing: ΓëÑ0 rows ΓåÆ failing=9 | n/a | none | **GREEN** |
| `fly.analyzer_genome` Fly dashboard analyzer panel (genome) | /api/analyzer/genome | laptop analyzer-report upload to Fly mirror | no: DEAD_SECTION | fields only | yes (fly_analyzer_mirror) | none | **AMBER** DEAD_SECTION |
| `fly.analyzer_summary` Fly dashboard analyzer panel (summary) | /api/analyzer/summary | laptop analyzer-report upload to Fly mirror | no: DEAD_SECTION | fields only | yes (fly_analyzer_mirror) | none | **AMBER** DEAD_SECTION |
| `fly.relay_snapshot` Fly relay snapshot | {chain}/relay_status_snapshot_v1.json | laptop-chain monitor polling relay state | yes | fields only | n/a | none | **GREEN** |
| `fly.runtime_snapshot` Fly runtime snapshot (laptop-chain) | {chain}/fly_runtime_snapshot_v1.json | laptop-chain monitor polling Fly /api/status | yes | fields only | yes (fly_roster) | none | **GREEN** |
| `fly.segment_head` Fly segment head | {chain}/fly_segment_head_snapshot_v1.json | segment shipper | yes | fields only | n/a | none | **GREEN** |
| `fly.state` Fly /api/state (public dashboard: trades, positions, truth) | /api/state | Fly runtime, paper ledger | yes | trades: ΓëÑ1 rows; positions: ΓëÑ0 rows ΓåÆ trades=5, positions=2 | n/a | none | **GREEN** |
| `fly.status` Fly /api/status (tiles, collection, uptime) | /api/status | Fly runtime | yes | active_tiles: ΓëÑ1 rows ΓåÆ active_tiles=4 | yes (fly_roster) | none | **GREEN** |
| `fly.system_health` Fly system-health relay | /api/system-health | laptop watcher POST /api/system-health/report | yes | fly_self_checks: ΓëÑ1 rows ΓåÆ fly_self_checks=4 | n/a | none | **GREEN** |
| `export.data_health_streams` Export data_health_streams | data_health_streams | data_health_report.json | yes | rows: ΓëÑ10 rows ΓåÆ rows=17 | n/a | none | **GREEN** |
| `export.exit_regret` Export exit_regret_trades | exit_regret_trades | post-exit tape | yes | rows: ΓëÑ20 rows ΓåÆ rows=180 | n/a | none | **GREEN** |
| `export.family_tests` Export family_tests | family_tests | strategy lab | yes | rows: ΓëÑ10 rows distinct family_idΓëÑ2 ΓåÆ rows=44 | n/a | none | **GREEN** |
| `export.fill_markouts` Export fill_markouts | fill_markouts | execution markouts | yes | rows: ΓëÑ8 rows distinct liquidityΓëÑ2,horizonΓëÑ2 ΓåÆ rows=32 | n/a | none | **GREEN** |
| `export.hypotheses` Export hypotheses + trades | hypothesis_trades | strategy lab, tape 1s | yes | rows: ΓëÑ50 rows distinct hypothesis_idΓëÑ2 ΓåÆ rows=1043 | n/a | none | **GREEN** |
| `export.main_rankings` Export main_rankings | main_rankings | main_rankings_report.json | yes | rows: ΓëÑ20 rows ΓåÆ rows=87 | n/a | none | **GREEN** |
| `export.quarantine` Export quarantine | quarantine | ledger reconciliation | yes | rows: ΓëÑ0 rows ΓåÆ rows=970 | n/a | none | **GREEN** |
| `export.research_events` Export research_events | research_events | research_events_v22 | yes | rows: ΓëÑ10 rows ΓåÆ rows=55 | n/a | none | **GREEN** |
| `export.sim_parity` Export sim_parity | sim_parity | live fills, tape replay | yes | rows: ΓëÑ20 rows ΓåÆ rows=176 | n/a | none | **GREEN** |
| `export.stream_health` Export stream_health | stream_health | mirror streams, Tier A | yes | rows: ΓëÑ15 rows ΓåÆ rows=24 | n/a | none | **GREEN** |
| `export.summary` Analyzer export summary.json | {exports}/summary.json | analyzer export step | yes | generation.active_tiles: ΓëÑ1 rows ΓåÆ generation.active_tiles=4 | yes (export_ledger) | none | **GREEN** |
| `export.taker_counterfactual` Export taker_counterfactual | taker_counterfactual | tape, decisions | yes | rows: ΓëÑ10 rows distinct latency_secΓëÑ2 ΓåÆ rows=36 | n/a | none | **GREEN** |
| `export.tile_stats` Export tile_stats | tile_stats | analyzer cohort | yes | rows: ΓëÑ1 rows ΓåÆ rows=4 | yes (export_tiles_vs_cohort) | none | **GREEN** |
| `export.walk_forward` Export walk_forward | walk_forward | strategy lab | yes | rows: ΓëÑ4 rows ΓåÆ rows=8 | n/a | none | **GREEN** |
| `selfaware.ai_scorecard` AI scorecard | :9021/api/selfaware/ai/scorecard | mirror ai_tranche, tape 1s | yes | rows: ΓëÑ5 rows ΓåÆ rows=42 | n/a | none | **GREEN** |
| `selfaware.data` Self-aware data awareness | data | mirror tree, Tier A, retention receipts | yes | streams: ΓëÑ20 rows; sufficiency: ΓëÑ3 rows ΓåÆ streams=100, sufficiency=4 | n/a | none | **GREEN** |
| `selfaware.edges` Edge tracker | :9021/api/selfaware/edges | tape 1s, edges_registry.json | yes | edges: ΓëÑ10 rows ΓåÆ edges=47 | n/a | none | **GREEN** |
| `selfaware.health` Self-aware diagnosis | health | laptop-chain snapshots, mirror raw views | yes | findings: ΓëÑ15 rows ΓåÆ findings=33 | n/a | none | **GREEN** |
| `selfaware.receipts` Deploy / v2c / proof receipts | receipts | deploy runs snapshot, v2c auto-ff receipts, proof receipt | yes | deploys: ΓëÑ1 rows; auto_ff: ΓëÑ1 rows ΓåÆ deploys=8, auto_ff=8 | n/a | none | **GREEN** |
| `selfaware.tiles` Self-aware tile stats | :9021/api/selfaware/tiles | mirror V3 execution ledger | yes | tiles: ΓëÑ1 rows ΓåÆ tiles=13 | n/a | none | **GREEN** |
| `selfaware.uptime` Uptime | uptime | watcher verdict history | yes | fields only | n/a | none | **GREEN** |
| `watcher.health` :9011 system health | :9011/api/system-health | system_health.py tick, laptop-chain state | yes | checks: ΓëÑ30 rows ΓåÆ checks=48 | n/a | none | **GREEN** |
