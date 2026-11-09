"""Public keyless Bitfinex book and raw-trade collector.

This package is isolated from the paper engine. With no arguments the process
refuses to start. `capture --output` writes the public perpetual book and
trades into a new directory. It does not read API keys and it does not arm
live trading.
"""

SCHEMA_V = "pmc_raw_v1"
NORM_SCHEMA_V = "pmc_norm_v1"
VENUE = "bitfinex"

__all__ = ["NORM_SCHEMA_V", "SCHEMA_V", "VENUE"]
