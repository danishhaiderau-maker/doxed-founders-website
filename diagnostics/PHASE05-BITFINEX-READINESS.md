# Phase 5 — Bitfinex readiness + full-lifecycle observability

Status: **built, tested (55 passing), wired fail-closed. Relay/arm gate remains OFF.**
Worktree: `C:\DoxxedCrypto\wt-phase05` off `origin/master fddb555e10bd` (13 tiles).

## What this phase delivered

Per-lane "Bitfinex Live Orders" switch (default OFF, fail-closed), a read-only
query API, a signed/hash-chained order-action audit log, a paper-vs-Bitfinex
twin matcher, book-priced fill provenance, full lifecycle telemetry, health
mismatch alerts, and a self-aware surface so the whole picture is queryable in
one place.

Nothing here arms, toggles a tile, places/cancels an order, or copies paper
state to live. Arming stays on the existing `/api/live_arm` and
`/api/bitfinex_live` operator paths. No secret value is printed or returned;
only the env var name `BITFINEX_AUDIT_HMAC_SECRET` is referenced.

## Modules (all pure, importable without `bot.py`)

| File | Purpose |
|------|---------|
| `bitfinex_live_switch.py` | Per-lane live-orders toggle + `evaluate()`/`why_not_armed()`/`compute_size_checks()` |
| `order_action_audit.py` | Append-only JSONL, monotonic seq, SHA256 hash chain + HMAC signature |
| `paper_bitfinex_match.py` | Twin matcher (intent_id → client_order_id → policy_signature) + diff |
| `lifecycle_telemetry.py` | SIGNAL→ORDER→FILL→OPEN→PARTIAL→CLOSE stage recorder + latency/feed/resource |
| `health_mismatch_alerts.py` | `evaluate_alerts()` — divergence / inconsistent switch / stall / stale feed |
| `fill_pricing_source.py` | `REALISTIC_V1` book-pricing provenance + compatibility guard |
| `bitfinex_readiness_api.py` | Flask blueprint, `wire(context_provider, audit, ...)` injection |

## API endpoints (all GET, read-only, fail-closed)

On the bot (`:7002`, after wiring in `bot.py`):

| Endpoint | Returns |
|----------|---------|
| `/api/bitfinex/status` | relay/arm state + per-tile switch/eligibility/denials + armed lanes |
| `/api/bitfinex/tiles/<lane>/arming` | per-tile "why isn't this armed" explanation object |
| `/api/bitfinex/audit` | signed/hash-chained order-action audit (queryable by action/trade/intent/lane/seq) |
| `/api/bitfinex/matches` | paper-vs-Bitfinex twin match + diff list |
| `/api/bitfinex/fill-pricing` | book-priced fill provenance (`REALISTIC_V1`) |
| `/api/bitfinex/telemetry` | lifecycle stages, latencies, feed + resource telemetry |
| `/api/bitfinex/alerts` | health-monitor mismatch alerts (CRITICAL/WARNING) |
| `/api/bitfinex/overview` | **one-place aggregate** of all of the above |

On the self-aware daemon (`127.0.0.1:9021`):

| Endpoint | Returns |
|----------|---------|
| `/api/selfaware/bitfinex` | reduced readiness verdict + alerts, fetched from the bot overview |
| `/api/selfaware/health` | now includes a `bitfinex.readiness` finding (GREEN/AMBER/RED/SKIP) |

## Test results

```
services/btc-conservative-agent (pytest -p no:anyio):
  test_bitfinex_live_switch.py              15 passed
  test_order_action_audit.py                 8 passed
  test_paper_bitfinex_match.py               8 passed
  test_lifecycle_telemetry_alerts.py        10 passed
  test_fill_pricing_and_api.py               8 passed
  => 49 passed

scripts/test_self_aware_bitfinex_readiness.py:
  6 passed
```

Total: **55 passed**.

## Per-item status

1. Per-tile live switch — DONE (`bitfinex_live_switch.py` + status/arming endpoints).
2. Read API + "why isn't this armed" — DONE (`/api/bitfinex/status`, `/api/bitfinex/tiles/<lane>/arming`).
3. Signed audit log — DONE (`order_action_audit.py` + `/api/bitfinex/audit`).
4. Twin match view + diff — DONE (`paper_bitfinex_match.py` + `/api/bitfinex/matches`).
5. Book-priced fills — DONE (`fill_pricing_source.py` + `/api/bitfinex/fill-pricing`).
6. Lifecycle telemetry — DONE (`lifecycle_telemetry.py` + `/api/bitfinex/telemetry`).
7. Mismatch alerts — DONE (`health_mismatch_alerts.py` + `/api/bitfinex/alerts`).
8. Self-aware wiring — DONE (`/api/selfaware/bitfinex` + diagnose check).

## Draft-PR grouping

- **Fly draft PR** (do not merge/deploy; guarded by `fly-bot-deploy.yml`): everything under
  `services/btc-conservative-agent/` — the 7 modules, the 5 test files, and the
  `bot.py` wiring block (registers the blueprint + a read-only, cached context provider).
- **Laptop-only** (mergeable `[skip ci]`): `scripts/self_aware/bitfinex_readiness.py`,
  `scripts/self_aware/diagnose.py`, `scripts/self_aware/engine.py`,
  `scripts/self_aware/server.py`, and `scripts/test_self_aware_bitfinex_readiness.py`.
  These do not touch any Fly runtime path.

## Fail-closed / safety notes

- Relay/arm gate is **OFF** and **nothing copies to Bitfinex** until the operator arms
  via the existing authenticated paths.
- The bot wiring reads only cached state (`_exchange_exposure_audit_snapshot`,
  `_runtime_readiness_components`, `_relay_delivery_guard.arming_block_reason`); it never
  triggers a private Bitfinex call and never returns a credential.
- Live-test sizing: `$0.20–$0.25` margin @ 100x; size/leverage/protection denials are
  recorded, never rounded up.
- No secrets-risk or arming-risk was touched by this phase.
