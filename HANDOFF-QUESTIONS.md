# Handoff questions for Grok Bot (paste batch only)

**For Danish:** When Cursor asks, copy this whole file to Grok Bot and paste Grok's reply back into the Cursor Project chat.

**Quota:** Ask Grok only for artifacts that exist on the Grok machine (not in git).

**Fly ops (Boss 2026-10-25):** Agents use GitHub Actions (`fly-bot-deploy.yml` modes like `resume-paper`, and `fly-*-once.yml` if present). `FLY_API_TOKEN` / `BOT_ADMIN_TOKEN` stay in Actions secrets — never paste into chat.

---

## Danish / operator blockers

1. **Simplified dashboard PR** (Boss/Strategist 00:10 audit item 5; overdue since ~16 Oct) — re-searched open PRs/branches (`dashboard` / `simplify` / `fly ui` / etc.): still **no match**. Please send the **PR number or branch name** (no tokens).

~~Cancelled: hung run `40169802400` — completed failure; paper unpaused on `#900` tip `f99681380830`.~~
~~`#904` sticky merge `5c94ca96c` — done on master (not tipped).~~

Keep GS-01 **disarmed** until Boss/gates say otherwise. Do not paste tokens into chat.

<!-- Cursor: append Grok-only numbered questions below. Delete answered blocks after paste-back. -->

## BTC-478 — Countability delay source (RK prep; Grokbot chat paused — batch only)

**Context:** While GS-01/live stays disarmed, `live_ack_pool` has `pool_n=0`, so every paper close stamps `delay_source=default_until_n10` and fails R17 (`DELAY_NOT_MEASURED`). Own bot-side `_paper_own_s2s_*` is already measured but does not satisfy current `MEASURED_DELAY_PATTERN` (RK ruling A: pooled live Fly-intent→ack only, N≥10). Soft B / Force / wipe / arm stay NO. Packet: store `internal/btc-countability-delay-rk.md`.

**Question for Grok (rules/strategist judgment — do not code):**

While live remains off, may we admit a **parallel paper-measurable** delay label based only on the already-measured bot-side own signal→send (`PAPER_OWN_S2S…`), for **paper relative scoring only** (`live_parity=false`), with live-parity / promotion / any GS-01 arm path still requiring `POOLED_LIVE_ACK_CROSS_LANE` with N≥10?

Pick one:

1. **HOLD_A** — No. Keep fail-closed; only real live-ack seeds countability (prior D2).
2. **PASS_B** — Yes. Parallel `PAPER_OWN_S2S` cohort only; never replaces live-ack for live-parity or arm.
3. **REJECT_WIDEN** — Also refuse counting `default_until_n10`, lowering `MIN_POOL_N`, or pooling paper signal→fill.

Builder lean: **PASS_B**. Optional GS-01 $0.25 live-ack sample stays inside Danish/Grok 15:43 DoD item 6 only after HM full safety green + RK PASS + Danish plain yes (not this worker).
