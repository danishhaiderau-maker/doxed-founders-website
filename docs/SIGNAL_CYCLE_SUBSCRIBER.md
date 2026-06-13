# Signal Cycle API — Subscriber Guide

See also: [Signal API docs page](https://doxxedcrypto.digital/docs/signal-api)

Conservative BTC Agent publishes **exchange-neutral signal cycles**. Execute on **your venue** using **your local mark** — not Bitfinex absolute prices.

## Mandatory rules

1. **Subscriber mark at receipt** for limit price from `entry.offset_pct`.
2. **Exchange stop-loss at fill** — `stop_loss_placed: true` required before `FILLED` is accepted.
3. **Report lifecycle** via POST events.
4. **Success fee** — 10% of profit on close only; $0 on loss; waived if 10% < $0.20.

## API base

`https://doxed-founders-website-production.up.railway.app/api`

Header: `X-Signal-Api-Key: dcf_sig_...`

Full examples in repository `apps/web/src/app/docs/signal-api/page.tsx` and OpenAPI TBD.
