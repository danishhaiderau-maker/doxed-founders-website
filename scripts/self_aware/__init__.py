"""Self-aware, self-diagnosing observability layer for the BTC V3.1 paper bot (laptop only).

Fly only trades, logs and ships data. Everything here runs on the laptop over
the custody mirror, analyzer exports and laptop-chain state, writes computed
results with provenance into one DuckDB store, and serves them read-only on
127.0.0.1. Nothing in this package touches trading, relay or exchange state.
See docs/SELF_AWARE_RUNBOOK.md.
"""
