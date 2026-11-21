"""Single-source literals for the B-R63D decision layer (card r2.2 24430bc38561 s4.1; HM C4).

The freeze pack later hashes this file, and once frozen its hash is checked at start-up.
This module imports nothing and is never imported by the reference oracle's callers in production.
pins.py (batch-1 constants) is NOT used by this layer (GS MINOR-4).
"""
# R63-F.3 (A9 r10 L195): the governing exit clock; the sole allowed value is X1.
EXIT_CLOCK = "X1"
# R63-F.6 / eda4 A2 (iii) (plan L719 (iii)): the stale literal, seconds.
STALE_S = 600
# R63-F.2 / A9 r10 L111 E1: fill window T+1 .. T+5 seconds.
ENTRY_WINDOW = (1, 5)
# A9 r10 L115 E4: the 12 horizons, seconds.
HORIZONS_12 = (1, 5, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 14400)
# Production decision grid (A9 r10 L110: T = 60m) and the UTC day (EM6).
DECISION_GRID_S = 60
DAY_S = 86400
# RK 7a06 C.5: full sha256 of research_fulln480m/fulln_closure.py at base 2392b0f77a98 (git show).
CLOSURE_SHA256 = "f5b6464023a20b69dd3725b47d3ec87f09368c4956f293ac46a7bf6c3d0c1eb4"
EXIT_STOP = 78

# Start-up self-check (HM C4): any other literal is STOP 78.
if type(STALE_S) is not int or STALE_S != 600 or EXIT_CLOCK != "X1":
    raise SystemExit(EXIT_STOP)
