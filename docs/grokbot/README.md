# Grokbot handoff

Grokbot is an external, **read-only** monitor of the BTC V3.1 paper bot. This folder is where the
operators answer Grokbot and tell it what it can read. It contains no secrets and no account details.

- **Start here:** [`ANSWERS.md`](ANSWERS.md) — current answers to Grokbot's open questions, the endpoint
  list (what is live now vs after the next deploy), and known false alarms.
- **Handoffs:** pull requests and issues meant for Grokbot carry the label
  [`grokbot-handoff`](https://github.com/danishhaiderau-maker/doxed-founders-website/labels/grokbot-handoff).
  Post-freeze Fly changes additionally carry `POST-FREEZE` and stay draft until the deploy window.
- **Answers are versioned:** each update replaces the relevant section of `ANSWERS.md` and bumps its
  "as of" timestamp; history is in git.

## Rules Grokbot can rely on

- Every endpoint listed in `ANSWERS.md` is GET-only, sanitized, size-capped and carries `boot_id`.
  None of them can change bot state, place orders or arm the Bitfinex relay.
- `boot_id` changes on every process restart; `counters_since_boot` reset with it. Compare
  counters only within one `boot_id`.
- The only credentialed monitor route is `GET /api/monitor/digest`
  (`Authorization: Bearer <MONITOR_READ_TOKEN>`). That token is delivered through the secure secret
  box, never in chat, issues or this repository. It is not an admin credential and is rejected on
  every other route. While the secret is unset the route returns 404.
- Bitfinex stays disarmed and the relay paper-only unless the operator arms it; Grokbot should treat
  `safety.live_armed=true` or `safety.bitfinex_live_enabled=true` as a page-worthy change.
