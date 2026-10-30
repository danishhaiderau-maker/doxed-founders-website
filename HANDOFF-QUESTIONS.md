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

_No open Grok-only questions._

## Closed (do not re-ask)

- **Countability / measured delay** (Danish 30 Oct 21:46): stays **live-ack-only**. No paper-measured delay. Rows count only after **10 real Bitfinex acks** from the GS-01 `$0.25` live test. Arming GS-01 needs every gate passing, `#929`/`#930` revert deployed, R16 + R9 checks, and Grok HM + RK both PASS the arm — and even then only when Boss relays that both passed.
