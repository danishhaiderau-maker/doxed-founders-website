# Baseline multi-fill context repair

The context builder previously used top-level fill_price (the last accepted
schedule price) and trigger time while terminal replay used the accepted-event
VWAP and completion timestamp. Multi-price partial fills could consequently fail
position quantity reconstruction despite valid evidence.

`accepted_fill_position` now requires exact accepted-event quantity closure,
retains exact notional, and normalizes VWAP/completion identically to terminal
replay. The context builder uses that price for executed margin, requires the
first fill not precede signal, and binds ATR/coverage to completion. Normal replay
selects the completion-time ATR evidence rather than the top-level trigger.
Missing or inconsistent accepted events remain UNKNOWN; raw receipts are unchanged.

Root integration command in services/btc-conservative-agent:

```text
python -m pytest -q test_baseline_execution_context.py test_baseline_execution_context_integration.py test_conservative_shadow_terminal.py test_declared_shadow_model.py test_entry_baseline_replay.py --tb=short
131 passed in 3.64s
```

Tests include two fill prices/times passed through actual terminal replay,
nonterminating VWAP, venue-lot reconstruction, exact quantity mismatch, rejected
attempts, first-fill causality, ATR selection and no mutation of source receipts.
This source repair is not a current analyzer publication or production deployment.
