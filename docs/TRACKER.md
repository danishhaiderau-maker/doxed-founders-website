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

Last goal write: 2026-10-25 19:38 AEDT (Boss / Cursor Project — remaster `#902` onto tip after `#904`/`#882`/`#905`).

---

# BTC open-items tracker

Last status refresh: 2026-10-25 19:38 AEDT. Boss = Cursor Project.

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

## Live readback (19:38 AEDT / 08:38 UTC)

| Signal | Value |
|--------|--------|
| Fly `execution_paused` | **false** |
| `pause_owner` | none |
| `manual_admin_pause` | **false** |
| `live_armed` / `bitfinex_live_enabled` | **false** / **false** |
| `force_paper_mode` | **true** |
| Fly paper tip (`git_rev` / `source_git_rev`) | **`f99681380830`** (`#900` merge) |
| `origin/master` tip | **`f7c0cea6f`** (`#905`; includes `#904` `5c94ca96c` + `#882` `76bd6d2e5`) |
| Paper tip vs master | Fly still on `#900`; `#904`/`#882`/`#905`/`#883`/`#886`/`#879` on master **not tipped** — no Soft B / Force / wipe / tip / arm |
| Sticky lease | `#904` **merged** `5c94ca96c` (preserve-skip). Pre-merge post-`#900` 10m continuous **FAIL** (1× `DEPLOY_MAINTENANCE` re-assert @08:14:28Z). Post-`#904` continuous prove: watch / not claimed here |
| CPU 30‑min lag / `trade_lock` | **PASS** on Fly tip `f99681380830` (scheduler lag max 386 ms; 0 wait/hold over-budget) |

## Open-list status

| Item | Status | Link |
|------|--------|------|
| Paper resumed + stable | **IN PROGRESS** unpaused on `#900` tip `f99681380830`; pre-`#904` 10m continuous FAIL (sticky); `#904` now on master — re-prove continuous when tipped | Fly `f99681380830` |
| `#885` $10/$10 caps | **DONE** | [#885](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/885) |
| `#892` $9.99/$10.00 test | **DONE** | [#892](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/892) |
| `#900` bootstrap timeout + RED@45m | **DONE** on Fly tip; Guaranteed resume works | [#900](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/900) |
| `#904` sticky preserve-skip | **DONE** squash-merged `5c94ca96c` (not on Fly tip yet) | [#904](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/904) |
| `#894` TradesTail CPU | **DONE** | [#894](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/894) |
| `#889` FADE3 feed CPU | **DONE** | [#889](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/889) |
| CPU 30‑min lag / trade_lock readback | **DONE** PASS on Fly `#900` tip | store `internal/btc-lag-trade-lock-readback.md` |
| `#888` tombstone route | **DONE** | [#888](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/888) |
| Tombstone apply → 339/339 | **QUEUED** after paper healthy + R21 | — |
| `#895` Railway admin-only | **DONE** | [#895](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/895) |
| `#901` / `#884` admin auth CI | **DONE** | [#901](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/901) |
| `#897` FADE3 BZ rotation | **IN PROGRESS** remastered; CI / merge queue | [#897](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/897) |
| `#886` relay cutoff + genesis | **DONE** merged `08c3d1b80` | [#886](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/886) |
| `#883` shared maker fill | **DONE** merged `9354d550b` | [#883](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/883) |
| `#882` analyzer floor/reconciler | **DONE** squash-merged `76bd6d2e5` | [#882](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/882) |
| `#879` R22 dead-code PR-A | **DONE** merged `4df62d494` | [#879](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/879) |
| `#903` exit-truth STOP fill | **IN PROGRESS** RK PASS `7fb43bd7`; merge after green | [#903](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/903) |
| `#905` analyzer oversized JSONL | **DONE** squash-merged `f7c0cea6f` | [#905](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/905) |
| `#902` GOAL/TRACKER docs | **IN PROGRESS** remaster onto tip `f7c0cea6f` | [#902](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/902) |
| `#906` fly.paused RED+AMBER dedupe | **IN PROGRESS** RK PASS; rebase vs `#907` | [#906](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/906) |
| `#907` pause total-time accounting | **IN PROGRESS** RK PASS; stack after `#906` | [#907](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/907) |
| Simplified dashboard PR | **BLOCKED** need Boss PR#/branch | HANDOFF |
| Memory-leak 3h verdict | **QUEUED** clock ~18:53 AEDT | — |
| GS-01 arm @ $0.25 | **BLOCKED** — all gates + Danish OK | — |

## Priority queue (working order while unblocked)

1. Keep paper unpaused on Fly tip; post-`#904` continuous prove when `#904` is tipped (no Soft B / Force / wipe / tip / arm from docs)
2. Merge `#903` then `#902` (this PR) as CI green; `#897` when remaster tip green
3. `#906` → `#907` stack (later; not ahead of open-list merges)
4. Post-stable: tombstone 339/339, memory-leak 3h
5. Arm gates last (Danish OK)

## Rules Keeper / Health Monitor (recent)

- `#889` @ `d41b63b`: RK PASS, HM CONDITIONAL → **merged**
- `#900` @ `28efcf74a`: RK PASS → **merged** `f9968138`
- `#904` sticky: **merged** `5c94ca96c`
- `#882` @ remaster tip: RK PASS, HM CONDITIONAL → **merged** `76bd6d2e5`
- `#905`: **merged** `f7c0cea6f`
- `#903` @ `7fb43bd7`: RK PASS; CI / merge pending
- `#897` remaster: RK PASS, HM CONDITIONAL; CI / merge pending
- `#906` / `#907`: RK PASS; later stack
- `#901` @ `76d11e34`: RK PASS, HM PASS → **merged**
- `#888`: RK PASS, HM PASS → **merged**

## Recent ops notes (compressed; open-list above is source of truth)

- **`#900` tip live:** deploy `40172488019` failed loud at receipt bootstrap; Guaranteed resume SUCCESS → paper clear on `f99681380830`.
- **Sticky race:** post-`#900` 10m continuous FAIL (1× `DEPLOY_MAINTENANCE` re-assert @08:14:28Z). Fix landed as `#904` `5c94ca96c`.
- **Master tip after merges:** `#904` → `#882` → `#905` (`f7c0cea6f`). Do not tip Fly from this docs PR.
