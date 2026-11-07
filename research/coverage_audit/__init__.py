"""Synthetic timestamp and schema coverage auditor.

Stdlib startup effects: the interpreter may read environment variables and
locale data before this package loads. Application code does not read
environment variables, does not enumerate their names, and does not use them
as configuration. See guards.STDLIB_STARTUP_EFFECTS and README.md.
"""

from research.coverage_audit.auditor import run_audit
from research.coverage_audit.guards import STDLIB_STARTUP_EFFECTS, TINY_SYNTHETIC_PROFILE

__all__ = ["STDLIB_STARTUP_EFFECTS", "TINY_SYNTHETIC_PROFILE", "run_audit"]
