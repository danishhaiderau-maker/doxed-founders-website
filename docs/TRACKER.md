# GOAL (persistent — do not remove)

**Finish the full open list from `docs/CURSOR-STARTER-PROMPT.md` under hard rules R0–R23.**

Order:
1. Paper resumed and stable (`execution_paused=false`, advancing AI cadence)
2. `#885` / `#892` / `#900` merged and deployed (loss caps + deploy-pause always-release)
3. `#888` (route + apply tombstones → 339/339), `#886`, `#883`, `#884`/`#901`, `#882`, `#879`
4. Monitor-token admin-only (Railway emergency ops) — done via `#895`
5. FADE3 BZ rotation `#897`
6. CPU (`#889` + `#894`) deployed + 30‑min lag / `trade_lock` readback
7. R22 cleanup + Strategist R19 cycles
8. Arm gates + RK PASS, then **Danish-only** arm GS-01 live at `$0.25` with `$10/$10` limits

Everything else stays paper. FADE3 never live. Fail closed on unread safety data. Never Soft B / Force / blunt bot sync / wipe without R21 / tip without gates.

Last goal write: 2026-10-25 17:05 UTC (Boss / Cursor Project — remaster `#893` RK archive onto tip `0ce26fbd4` after `#896`/`#914`/`#912`).

---

# BTC open-items tracker

Last status refresh: 2026-10-25 17:05 UTC. Boss = Cursor Project.

## Standing operating rules (from Grok — keep)

1. Danish does not code. Never ask technical questions (R11/R18).
2. Four roles as separate threads; stricter-safety wins disagreements.
3. Git: stash/commit; never discard; never force-push.
4. Escalate to Danish only: arm live, real money, raise limits, delete prod data, credentials, payment.
5. Report only real results/blockers (2–3 sentences).
6. Grok unavailable except batched `HANDOFF-QUESTIONS.md`.
7. **Do not end a run while open items remain and none are blocked.** When a thread finishes, start the next item. Run HM / RK / Strategist cycles regularly.

## Definition of done (Danish / Grok 15:43 AEDT — keep)

1. CPU (`#889` + TradesTail `#894`) merged, deployed, lag &lt;1s, 0 trade_lock overruns 30 min.
2. `#885` caps on Fly $10/$10 + `$9.99`/`$10.00` test `#892`.
3. `#886`, `#888`, `#883`, `#884`, `#882`, monitor-token admin-only, FADE3 BZ rotation — full loop each.
4. Trade-count parity 339/339 via tombstone after `#888`.
5. GS-01 live only after ALL gates + Danish arm OK; else paper; FADE3 paper; fail-closed on unread safety data.
6. Live vs paper GS-01 ≥10 clean trades; fix R0 gaps.
7. Ongoing R22 + STRATEGIST R19.

## Live readback (16:10 UTC / remaster tip)

| Signal | Value |
|--------|--------|
| Fly `execution_paused` | **false** (post-`#915` tip) |
| `live_armed` / `bitfinex_live_enabled` | **false** / **false** |
| `force_paper_mode` | **true** |
| Fly paper tip (`git_rev` / `source_git_rev`) | **`9c12c3bb9a3a`** (`#915` tip with `#910`+`#913`) |
| `origin/master` tip | **`0ce26fbd4`** (`#896` hard-rules on `#914`/`#912`) |
| Soft B / Force / wipe / tip / arm from docs PRs | **NO** — docs remasters only |
| Quiet stack | Do **not** touch `#911` / `#884` / `#908` / `#914` |

## Open-list status

| Item | Status | Link |
|------|--------|------|
| Paper resumed + stable | **IN PROGRESS** unpaused on tip `9c12c3bb9a3a`; memory-leak 3h clock running | Fly `9c12c3bb9a3a` |
| `#885` $10/$10 caps | **DONE** | [#885](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/885) |
| `#892` $9.99/$10.00 test | **DONE** | [#892](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/892) |
| `#900` bootstrap timeout + RED@45m | **DONE** | [#900](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/900) |
| `#904` sticky preserve-skip | **DONE** squash-merged `5c94ca96c` | [#904](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/904) |
| `#894` TradesTail CPU | **DONE** | [#894](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/894) |
| `#889` FADE3 feed CPU | **DONE** | [#889](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/889) |
| CPU 30‑min lag / trade_lock readback | **DONE** PASS (earlier on `#900` tip) | store `internal/btc-lag-trade-lock-readback.md` |
| `#888` tombstone route | **DONE** | [#888](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/888) |
| Tombstone apply → 339/339 | **QUEUED** after paper healthy + R21 | — |
| `#895` Railway admin-only | **DONE** | [#895](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/895) |
| `#901` / `#884` admin auth CI | **DONE** `#901`; quiet `#884` hold | [#901](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/901) |
| `#897` FADE3 BZ rotation | **DONE** merged `5252e7e99` | [#897](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/897) |
| `#886` relay cutoff + genesis | **DONE** merged `08c3d1b80` | [#886](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/886) |
| `#883` shared maker fill | **DONE** merged `9354d550b` | [#883](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/883) |
| `#882` analyzer floor/reconciler | **DONE** squash-merged `76bd6d2e5` | [#882](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/882) |
| `#879` R22 dead-code PR-A | **DONE** merged `4df62d494` | [#879](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/879) |
| `#903` exit-truth STOP fill | **DONE** merged `9fc3437e2` | [#903](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/903) |
| `#905` analyzer oversized JSONL | **DONE** squash-merged `f7c0cea6f` | [#905](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/905) |
| `#902` GOAL/TRACKER docs | **DONE** merged `e04485559` | [#902](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/902) |
| `#906` fly.paused RED+AMBER dedupe | **DONE** merged `aadd3d88f` | [#906](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/906) |
| `#907` pause total-time accounting | **DONE** merged `ec0aafda4` | [#907](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/907) |
| `#909` ledger rotation classify | **DONE** merged `e83030239` | [#909](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/909) |
| `#910` soft-fail bootstrap | **DONE** merged `6f828dbcd` | [#910](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/910) |
| `#913` pwsh fence timeout | **DONE** merged `fa4c4c6d5` | [#913](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/913) |
| `#915` tip deploy trigger | **DONE** merged `9c12c3bb9` | [#915](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/915) |
| `#912` handoff dashboard ask | **DONE** merged `09bd71e7e` | [#912](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/912) |
| `#914` disk_alert red-ack | **DONE** merged `c5a0d6dba` (quiet stack landed) | [#914](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/914) |
| `#893` RK verdicts tracker | **IN PROGRESS** remaster onto tip `0ce26fbd4` → squash when UT green | [#893](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/893) |
| `#896` hard-rules handoff | **DONE** squash-merged `0ce26fbd4` | [#896](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/896) |
| Quiet stack `#911`/`#884`/`#908`/`#914` | **HOLD** — do not touch | — |
| Simplified dashboard PR | **BLOCKED** need Boss PR#/branch | HANDOFF |
| Memory-leak 3h verdict | **QUEUED** clock on tip `9c12c3bb` | store `internal/btc-memory-leak-9c12c3bb.md` |
| GS-01 arm @ $0.25 | **BLOCKED** — all gates + Danish OK | — |

## Priority queue (working order while unblocked)

1. Keep paper unpaused on Fly tip `9c12c3bb9a3a`; finish memory-leak 3h (no Soft B / Force / wipe / tip / arm from docs)
2. Remaster docs `#893` / `#896` for Danish review — **do not merge** from this remaster
3. Hold quiet stack `#911`/`#884`/`#908`/`#914`
4. Post-stable: tombstone 339/339
5. Arm gates last (Danish OK)

## Rules Keeper / Health Monitor (recent)

- `#889` @ `3722a99e6`: RK PASS (see archive) → **merged**
- `#894` @ `2bcf8723`: RK PASS (see archive) → **merged**
- `#892` / `#895`: RK PASS (see archive) → **merged**
- `#900` / `#904` / `#882` / `#905` / `#901` / `#888`: **merged**
- `#903` / `#897` / `#906` / `#907` / `#909` / `#910` / `#913` / `#915` / `#912`: **merged**; master tip `09bd71e7e`
- Detailed early RK tables for `#889`/`#894`/`#891`/`#892`/`#895` preserved below (remaster `#893`)

## Recent ops notes (compressed; open-list above is source of truth)

- **Master tip:** `0ce26fbd4` (`#896` after `#914`/`#912`). Fly paper still `9c12c3bb9a3a` (`#915`) unless `#914` deploy completes tip separately. `#896` docs-only — no new Fly deploy from that merge.
- **Docs remaster:** `#893` keeps GOAL/open-list from `#902` and archives RK verdict tables. `#896` remaster keeps `#902` R0–R23 taxonomy (does not restore alternate numbering).
- **Do not tip / Soft B / Force / wipe / arm** from docs PRs. Quiet stack untouched.

---

## Rules Keeper verdict archive (from `#893`; historical)

_Preserved from the original Rules Keeper TRACKER. Several subjects below are now **merged**; tables stay as the RK audit trail. No merge/arm/deploy from this archive._

_2026-10-25 ~04:40 UTC — RK pass on open **draft** PRs for CPU (#889, #891) and R20 open-mark (#892). No merge, no arm, no deploy dispatch._

_2026-10-25 ~04:45 UTC — RK addendum: Builder draft [#894](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/894) (TradesTail); treat as canonical BTC-3 over #891 until #891 is closed._

_2026-10-25 ~04:42 UTC — RK re-check [#894](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/894) head `2bcf872389c0f6d632db38fc18960a382f4b6ee5` (R0 fix): **PASS**._

_2026-10-25 ~04:48 UTC — RK [#895](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/895) (Railway ops admin auth): **PASS**._

_2026-10-25 ~04:54 UTC — RK re-check [#889](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/889) head `3722a99e6e3c397dc2d99ddea3815356e89bcdd9` (R8 finite-BZ fix): **PASS**._

### [#889](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/889) — `perf/fade3-incremental-feed` (FADE3 feed CPU)

**RULES CHECK: PASS** _(head `3722a99e6e3c397dc2d99ddea3815356e89bcdd9`, 25 Oct 2026)_

| Rule | Assessment |
|------|------------|
| R0 (post-cutoff) | **PASS** — fixture rows `s >= 1792263671`; `test_fixture_is_post_cutoff_and_deriv_covers_six_hours` asserts book ≥ 5 h and deriv span ≥ 6 h continuous. |
| R4 (one shared gf3 feed path) | **PASS** — `compute_features` / `entry_signals_e8` unchanged; tick path only `LiveFeatureFeed`; `full_rebuild_inputs` moved to test-only `fade3_feed_test_helpers.py` (removed from `tile_fade3.py`). |
| R8 (bit-identical + mutation proof) | **PASS** — extended post-cutoff `bfx_deriv_status_1s` (~21600 s); `test_incremental_features_bit_identical_to_full_rebuild` and `test_full_replay_bit_identical_and_hits_finite_bz` (1200 s tail, `saw_bz` + `np.isfinite(BZ)`); prior edge-case replay + **five** added incremental mutants (eviction, cache gens, deriv `searchsorted`, duplicate trade seconds). |
| Research freeze / registry | **PASS** — no `combo_pathway_config` / tile roster / epoch wipe. |
| Deploy surface | **PASS** — production `tile_fade3.py` CPU path; `STEP_CPU_BUDGET_S` 0.1 runtime warn; `STEP_CPU_CI_CEILING_S` 0.5 pytest-only with optional `PYTEST_SKIP_CPU_BUDGET=1`. |
| `no-blunt-bot-sync` | **PASS** — no sync scripts or `bybit_bot.py`. |

**Fix condition:** None for rules. **HM (non-RK):** confirm `unit-tests` green on `3722a99e6`; keep **draft** until HM exact-head + deploy gate.

---

### [#894](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/894) — `cursor/tradestail-incremental-refresh-fc35` (TradesTail CPU, Builder)

**RULES CHECK: PASS** _(head `2bcf872389c0f6d632db38fc18960a382f4b6ee5`, 25 Oct 2026)_

| Rule | Assessment |
|------|------------|
| R4 | **PASS** — same `TradesTail` class/API; `_refresh` incremental only (`_ingest_line`, `_prune`); no second trigger or pricing path in `quote_trigger`. |
| R8 (equivalence + mutation proof) | **PASS** — in-test `_FullRebuildTradesTail` reference; append, two partial writes, truncation, rotation, no-reread when `(st_ino, st_size)` unchanged; mutation subclasses tied to asserts. |
| R0 (post-cutoff / genesis `1792263671`) | **PASS** — `EPOCH_FLOOR = 1792263671`, `T = EPOCH_FLOOR + 100`; all `int(T) + i` in tests stay `>= EPOCH_FLOOR` (max offset +50 in rotation case). |
| CI allowlist | **PASS** — `test_tradestail_incremental.py` on `ci/unit_test_allowlist.txt`. |
| Research freeze / `no-blunt-bot-sync` | **PASS** — no registry/epoch wipe; no `bybit_bot` sync. |
| Scope | **PASS** — `quote_trigger.py` only; no #889 FADE3 feed; deploy only on merge (expected). |

**Fix condition:** None for rules. **HM (non-RK):** confirm `unit-tests` green on `2bcf8723`; close duplicate [#891](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/891). **Nit (non-blocking):** recorded Fly fixture replay (as in #891) optional extra assurance; not required after R0 rebase.

---

### [#891](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/891) — `fix/tradestail-incremental` (TradesTail CPU) — **superseded by #894**

**RULES CHECK: PASS** _(historical; do not merge both)_

| Rule | Assessment |
|------|------------|
| R4 | **PASS** — same `TradesTail` class and public API; `_refresh` incremental only; `_ingest` extracted; no second trigger/pricing path. |
| R0 / R8 | **PASS** — `bfx_trades_recorded_20261025` fixture with `s >= 1792263671`; append/partial-write/truncate/inode-rotation replays match full rebuild after every step; unchanged-file no-reread test. |
| R23 | N/A — production `quote_trigger.py` change (allowed for CPU lane). |
| CI allowlist | **PASS** — `test_quote_trigger_tail_incremental.py` added to `ci/unit_test_allowlist.txt`. |
| Research freeze / sync | **PASS** — same as #889. |

**Fix condition:** None for rules. **HM:** keep **draft** until `unit-tests` CI is green on head (was pending at RK read).

---

### [#892](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/892) — `cursor/r20-open-mark-boundary-test-22af` (R20 open mark)

**RULES CHECK: PASS**

| Rule | Assessment |
|------|------------|
| R23 | **PASS** — test-only; `test_r20_open_mark_limit_boundary.py` + one allowlist line; no production modules changed. |
| R20 | **PASS** — pins $9.99 vs $10.00 at the **un-lowered** $10 limit through `daily_cap_entry_block`, `d3_cap_violations`, `live_guardian.evaluate` / `split_violations`; exit-side mark (bid/ask), realised+open sum, per-tile attribution, stale book fail-closed; mutation harness described in PR body. |
| Fly deploy filter | **PASS** — `test*.py` excluded from deploy path trigger. |
| Research freeze / relay / arm | **PASS** — no runtime, relay, or arm surface. |

**Fix condition:** None for rules. **HM:** keep **draft** until `unit-tests` CI is green on head (was pending at RK read).

---

### [#895](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/895) — `cursor/railway-ops-admin-only-80a5` (BTC-10 Railway ops auth)

**RULES CHECK: PASS** _(head `f44a65e5a77932b9e2514393660bf1b66261568d`, 25 Oct 2026)_

| Rule | Assessment |
|------|------------|
| Fail-closed / ops safety | **PASS** — `MONITOR_READ_TOKEN` alone could previously reach mutation ops via `getOpsRelayStatus`; both `POST :slug/ops/emergency-reconcile` and `POST :slug/ops/recover-already-flat` now call `assertBotAdminToken` **before** any status load or executor dispatch (matches `ops/position-flatten`, `ops/force-flat-disarm`). |
| R4 (one auth helper) | **PASS** — reuses shared `assertBotAdminToken` from `live-copy-ops.service`; GET `ops/relay-status` read path unchanged (admin or monitor). |
| Confirmation gates | **PASS** — unchanged typed confirmations (`FLATTEN_PAUSED_UNATTRIBUTED_RESIDUAL`, `RECOVER_ALREADY_FLAT_PENDING_WITHOUT_CLOSE`) and PAUSED/mismatch checks on emergency-reconcile. |
| Cross-layer tests | **PASS** — `btc10-ops-railway-admin.spec.ts` (route contracts, four reorder/drop mutation proofs, runtime reject monitor token with zero status/executor calls); session-summary specs updated for `BOT_ADMIN_TOKEN` env; CI + `apps/api` test script allowlist. |
| Relay arm / wipe / tile / freeze | **PASS** — auth-only tightening; no new arm surface, epoch wipe, registry change, or Fly bot execution path. |
| `no-blunt-bot-sync` | **PASS** — `apps/api` only. |

**Fix condition:** None for rules. **HM (non-RK):** confirm `unit-tests` / `bitfinex-policy` green on `f44a65e5`; keep **draft** until HM review of ops behavior (unchanged aside from who may invoke).

---

_RK archive remaster: no merge, no tip, no arm, no wipe, no Soft B/Force. Quiet stack untouched._
