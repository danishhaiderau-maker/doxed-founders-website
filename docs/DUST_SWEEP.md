# Residual-position reconciliation

**Revised:** 2026-07-24
**Scope:** Live Bitfinex copy relay.

## Rule

`MIN_QTY_BTC = 0.00004` is the minimum size for a new BTC-PERP entry. It is
not an accounting-zero tolerance. Bitfinex amounts are accounted for at eight
decimal places:

- Flat means exactly `0` satoshis.
- A `0.00003999 BTC` position is real exposure.
- A fill of one satoshi is a fill, never an unfilled order.

The former generic dust sweep is intentionally disconnected. Closing every
sub-minimum position was unsafe because Bitfinex merges same-symbol lots: a
small delta can sit on top of another legitimate relay lot or a manual
position.

## Immediate exit reconciliation

Every participant exit runs in the normal relay loop:

1. Sum all other OPEN relay lots in signed satoshis. This is the required
   post-exit exchange target.
2. Reject missing metadata, opposing directions, or exchange size greater than
   the total relay-owned ledger. The latter protects manual/foreign exposure.
3. Cancel only the exiting participant's managed protective stop and confirm
   it is gone.
4. Submit the exact attributable reduction:
   - partial merged-lot exit: eight-decimal `REDUCE_ONLY`;
   - final ledger target of zero: dedicated `CLOSE | REDUCE_ONLY`.
5. Re-read the raw Bitfinex position and record `EXIT` only when it exactly
   equals the post-exit ledger target.
6. On any ambiguity, cancellation failure, exchange rejection, or unconfirmed
   target, keep the lot open, set the relay to `PAUSED/EXIT_ONLY`, and surface
   a blocking error.

This handles a final `0.00003999 BTC` remainder immediately without risking a
reversal. A sub-minimum delta on top of another live lot is never rounded up;
it remains fail-closed until it can be safely reconciled.

## Periodic audit

A periodic scan may be used as a read-only alert and health backstop. It must
not blindly market-close the account every four hours because that could close
a legitimate showcase or manual position.

The flat-boundary gate requires a fresh reconciliation snapshot with:

- raw exchange quantity `0`;
- dust quantity `0`;
- ledger quantity and delta `0`;
- no OPEN/PENDING participants;
- no orphan orders or positions.

## Regression coverage

Focused tests cover zero, one satoshi, `0.00003999`, exact `0.00004`, full and
partial merged exits, opposing-direction rejection, manual/foreign exposure
rejection, raw-flat truth, and Bitfinex close flags.
