# -*- coding: utf-8 -*-
"""Fail-closed compatibility marker for the retired duplicate analyzer.

The only executable analyzer is ``../analyzer_research_engine_v62.py``.  This
file deliberately contains no report logic, imports, dashboard, or fallback so
an accidental legacy invocation can never publish unqualified research.
"""
from __future__ import annotations


MESSAGE = (
    "Legacy research analyzer is disabled fail-closed. "
    "Run ../analyzer_research_engine_v62.py so every report uses the shared cohort gate."
)


def main() -> int:
    raise SystemExit(MESSAGE)


if __name__ == "__main__":
    main()
else:
    raise ImportError(MESSAGE)
