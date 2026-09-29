# Analyzer integration and visual QA — 5 September 2026

## Scope and current boundary

Root and independent agents implemented current-generation complete shadow-result
streaming and normal-publisher integration. This is source work, not a new deployed
Fly revision or an activated current analyzer generation. Production remains
cb7745e827e1 during bootstrap continuation33943616722.

## Implemented and tested so far

- Verified gzip full-result stream binds generation, report, candidate artifact,
  counts, complete EOF, compressed and uncompressed hashes. Large all-UNKNOWN
  matrices use explicit arithmetic ranges, never sample-derived profitability.
- Discovery verifies full input into disposable SQLite indexes before consuming.
  Actual SQLite page ceilings replace the disproven estimated-size-only bound.
  Four concurrent indexes now share an explicit total budget; resource exhaustion
  returns UNKNOWN rather than rank a truncated cohort.
- Normal publisher writes one replaceable working gzip through an exclusive
  temporary file; failed temporary outputs are removed. Published generations
  retain independent copies. No per-cycle accumulation of source gzip outputs.
- Binary copied size/hash is checked before atomic publication. A corrupt copied
  artifact leaves the previous generation unchanged.
- Optional BTC_ANALYZER_SHADOW_MODEL_FILE plus SHA256 selects explicit declared
  assumptions; missing, changed, ambiguous or wrong-generation inputs fail closed.
  Material model input hash participates in publication provenance/identity.
- Root integrated suite:176 passed before the later actual-page-budget hardening;
  focused post-hardening/publication/temp-cleanup suite:70 passed. These overlap
  and are not additive totals. Final combined regression remains required.
- Later root stream/publication integration:218 passed after evaluator gzip
  input also became disk-streamed and publication rejected unsafe paths before
  copying. Evaluator compressed/decompressed bytes, individual rows, indexes,
  groups and output have explicit ceilings; no arbitrary total row sampling.
- Declared economics provenance now survives adapted rows, scorecard cells and
  descriptive leaders. Incompatible assumptions produce MODEL_MISMATCH rather
  than pooled PnL or a spurious leader. Unknown basis remains UNSPECIFIED.
- Context integration initially passed170 root tests, but its whole-ledger16MiB
  limit would reject the known18,920,143-byte opportunity source. This was fixed
  with a temporary streaming row-membership index: entire source hash/size once,
  selected row hash/offset/line proof, source stat fences and bounded row/cache.
  Two18,920,143-byte ledgers retain supported context in regression tests.
- Final root combined full-stream/context/model/publication integration:
  **287 passed in12.12 seconds**. This is source integration acceptance, not a
  current mirror publication or profitability result. The actual producer of
  baseline sizing authorization/exact fill ATR remains missing; declared/derived
  research alternatives must be separately specified and truthfully labeled.

## Browser observation, approximately04:12–04:16Z

Used existing in-app analyzer and Fly tabs; no trading/destructive controls used.
Observed screenshots are in the task's tool results (not separately saved files).

| Journey | Actual result | Acceptance |
| --- | --- | --- |
| Analyzer Genome landing | Clear stale warning;889 opportunities and21,070 policies from September2 | Historical display only; not current analysis |
| Analyzer Overview | Three truth tiers visible; qualification unavailable; storage classifications unavailable | Missing evidence explained, current qualification not passed |
| Analyzer Lanes & AI | Navigation works; current lanes explicitly STALE / UNAVAILABLE | Functional navigation only |
| Analyzer Chase & Exits | Navigation works; executed and shadow sections present | Data currency not established |
| Fly Data navigation | Correct cb7745e revision; no pending/open paper orders; explicit empty-state explanations | Consistent with maintenance, not post-resume stability |
| Fly runtime incidents | Watchdog history shown; platform receipts explicitly unavailable | No invented platform cause |
| Narrow viewport smoke check |390px viewport observed; data tables retain horizontal scroll | Full mobile journey QA still pending; override reset |

Observed usability defects/follow-ups:

- Stale banner still says to run from `Final Bots`; canonical execution is elsewhere.
- Overview scope text says best-policy evidence is current/pinned even while the
  top-level stale warning is active. It needs freshness-aware wording.
- Some unavailable metrics render `n/a%` and `$n/a`; use consistent unavailable labels.
- A historical in-progress heartbeat is displayed as current synchronization;
  liveness must not be inferred from that file alone. Retain qualification block.
- Source/runtime labels such as `local Flask page` and obsolete stop-script
  instructions on the public Fly page deserve correction in a reviewed UI patch.

Not completed: every submenu/filter/download, full desktop/mobile screenshots,
current-generation data verification, new strategy verdict, or live readiness.
Repeat full QA after verified mirror promotion and atomic analyzer publication.
