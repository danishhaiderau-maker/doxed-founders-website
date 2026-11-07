"""Public keyless Bitfinex book and raw-trade collector.

This package is isolated from the paper and AI engine. Real start is refused
until a future board freeze and a separate ops gate. Synthetic sessions never
open a socket.
"""

SCHEMA_V = "pmc_raw_v1"
NORM_SCHEMA_V = "pmc_norm_v1"
VENUE = "bitfinex"

__all__ = ["NORM_SCHEMA_V", "SCHEMA_V", "VENUE"]
