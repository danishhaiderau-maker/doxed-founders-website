# Frozen Experimental Tile Architecture V1

This contract applies to every current and future experimental tile.

1. Each tile has an independent toggle, policy signature, trade-ID namespace,
   lock, capacity, pending orders, positions, receipts, and PnL ledger.
2. Tile OFF creates no paper or exchange order. Its scheduled opportunity,
   decision, rejection, shadow path, and counterfactual outcome remain recorded.
3. Tile ON may create its own paper lifecycle after all common health, market,
   capacity, and policy gates pass.
4. Tile ON never submits directly to Bitfinex.
5. Relay OFF means paper only, without exception.
6. Relay ON may copy only new, signed, allowlisted paper lifecycle events after
   lane/policy identity and all relay safety gates pass. Existing paper orders
   and positions are never copied retroactively.
7. Only the operator may arm the relay. A deploy, restart, tile toggle, or
   configuration restore must not arm it.
8. Paper and copied execution truth remain separate. Fill, partial-fill,
   reprice, partial-close, stop update, terminal close, funding, slippage, and
   reconciliation receipts must preserve the causal paper identity.
9. A tile whose required relay lifecycle operation is unsupported remains
   paper-capable but relay-copy fail-closed until that operation is tested.
10. Analyzer ranking uses qualified conservative evidence only. Tile presence,
    paper profit, or relay eligibility is not strategy qualification.

Current roster:

- Tile 1: baseline 0.29% Patient Chase, ATR 2.5x/path-end control.
- Tile 2: Continuous benchmark.

Rejected experiments are removed from executable source and current analyzer
cohorts. Their immutable evidence is quarantine/archive data only. A new tile
requires qualified evidence and explicit operator approval.
