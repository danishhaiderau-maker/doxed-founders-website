"""R3 quarantine: research producers that price on the 1s tape / ticker ticks.

RK verdict (23 Oct 2026, AEDT): R3 forbids pricing or triggering on ``ticker``,
``book_tape_1s`` or ``market_microstructure_1s``.  Two research producers still
do exactly that until they are rebuilt on the shared ``paper_fill`` pricer and
``quote_trigger`` helper (R4):

* GENOME_GRID      -- research/genome_grid_study.py fast replay prices entries,
                      chases and exits on 1s tape bid/ask.
* EXIT_LADDER_SIM  -- analyzer exit_ladder_simulator_report replays
                      signal_replay ticker/BBO ticks through the fast-cut ladder.

Until fixed the producers STOP writing their outputs, and every analyzer /
dashboard / Strategist-facing reader refuses the named files (stale copies on
disk are left in place -- no data deletion -- but are never read or exported).
There is deliberately no environment override: lifting the quarantine is a code
change that must ship with the paper_fill/quote_trigger rebuild.
"""
from __future__ import annotations

import os
from typing import Any

STATUS = "R3_QUARANTINED"
RULE = "R3"
REASON = (
    "R3: prices/triggers on ticker, book_tape_1s or market_microstructure_1s; "
    "quarantined until rebuilt on paper_fill + quote_trigger (R4)."
)

QUARANTINE: dict[str, dict[str, Any]] = {
    "GENOME_GRID": {
        "producer": "research/genome_grid_study.py",
        "price_source": "bitfinex_l1_tape_1s + market_microstructure_1s bid/ask (fast replay)",
        "files": (
            "genome_grid_report.json",
            "genome_grid_rows.jsonl.gz",
            "research_api_cache.sqlite3",
            "research_api_summary.json",
        ),
    },
    "EXIT_LADDER_SIM": {
        "producer": "analyzer_research_engine_v62.exit_ladder_simulator_report",
        "price_source": "signal_replay ticker/BBO ticks (_simulate_ticks_fast_cut_ladder)",
        "files": ("exit_ladder_simulator_report.json",),
    },
}

# Tests may monkeypatch this set to exercise the legacy code path; production
# has no runtime switch.
ACTIVE: set[str] = set(QUARANTINE)

QUARANTINED_FILES: frozenset[str] = frozenset(
    name for spec in QUARANTINE.values() for name in spec["files"]
)


def is_active(key: str) -> bool:
    return key in ACTIVE


def quarantined_file(name: Any) -> bool:
    """True when ``name`` (path or basename) is an active quarantined output."""
    if not name:
        return False
    base = os.path.basename(str(name).replace("\\", "/"))
    return any(base in QUARANTINE[k]["files"] for k in ACTIVE if k in QUARANTINE)


def disabled_payload(key: str, **extra: Any) -> dict[str, Any]:
    spec = QUARANTINE[key]
    out = {
        "status": STATUS,
        "data_status": STATUS,
        "quarantine": key,
        "rule": RULE,
        "reason": REASON,
        "producer": spec["producer"],
        "price_source": spec["price_source"],
        "files_not_written": list(spec["files"]),
        "countable": False,
    }
    out.update(extra)
    return out
