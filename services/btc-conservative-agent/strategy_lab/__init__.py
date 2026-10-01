"""Strategy lab: the analyzer-owned strategy research engine.

Ported from the 2026-10-01/02 ad-hoc research scripts (TILE2 ``sim2.py`` /
``analyze3.py``, NEXT-TILE ``nt_cv*.py``, DATA-SUFFICIENCY model A) so agents
read pre-registered hypothesis results from the analyzer export instead of
re-running one-off grids. Analyzer-only (not shipped in the Fly image) and
read-only: nothing here can place, change or cancel an order.
"""

SCHEMA = "strategy_lab_report_v1"
REPORT_FILE = "strategy_lab_report.json"
