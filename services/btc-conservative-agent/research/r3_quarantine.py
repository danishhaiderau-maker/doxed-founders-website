"""R3 quarantine: research producers that price on the 1s tape / ticker ticks.

RK verdict (23 Oct 2026, AEDT): R3 forbids pricing or triggering on ``ticker``,
``book_tape_1s`` or ``market_microstructure_1s``.  Two research producers still
do exactly that until they are rebuilt on the shared ``paper_fill`` pricer and
``quote_trigger`` helper (R4):

* GENOME_GRID      -- research/genome_grid_study.py fast replay prices entries,
                      chases and exits on 1s tape bid/ask.
* EXIT_LADDER_SIM  -- analyzer exit_ladder_simulator_report replays
                      signal_replay ticker/BBO ticks through the fast-cut ladder.
* TICK_SIM         -- the analyzer tick-sim helpers (INVENTORY['TICK_SIM']).
* LIMIT_FILL_1S    -- the six evaluate_limit_fill callers (INVENTORY['LIMIT_FILL_1S'];
                      RK #847 cond 4, 24 Oct 2026 AEDT).  None has the book/trades
                      window book_limit_entry needs, so all six are retired.
* WALK_FORWARD_1S  -- research/walk_forward_scorer.py prices taker/maker entries and
                      exits on the 1s tape via fill_model.taker_fill_rows /
                      maker_fill_rows / realistic_exit_margin (HM #850 R2 C1).
* EXIT_STUDY_1S    -- diagnostics/hypothesis-tiles-20261004/exit_study.py books exits with
                      fill_model.realistic_exit_margin on the 1s tape (HM #850 R3, 5th caller).
                      main() refuses with SOURCE_RETIRED_R17 (exit 4); replay() raises.

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

    "TICK_SIM": {
        "producer": "analyzer_research_engine_v62 tick-sim producers (see INVENTORY['TICK_SIM'])",
        "price_source": "signal_replay ticker/BBO ticks (_simulate_ticks_fast_cut_ladder / _sim_replay_pnl_*)",
        "files": (
            "fast_cut_survivor_report.json",
            "fast_cut_sweep_report.json",
            "qualified_exit_policy_grid_report.json",
        ),
    },
    # RK #847 cond 4 (24 Oct 2026 AEDT): labels alone are not enough.  None of the six evaluate_limit_fill callers
    # can move onto book_limit_entry without a new per-episode bfx_book_10l + bfx_trades loader in its builder
    # (their inputs embed market_microstructure_1s rows only), so all six stop writing until rebuilt.  The
    # canonical CONSERVATIVE entry (research_v3_candidates -> book_limit_entry) is unaffected.
    "LIMIT_FILL_1S": {
        "producer": "conservative_limit_fill.evaluate_limit_fill callers (see INVENTORY['LIMIT_FILL_1S'])",
        "price_source": "market_microstructure_1s bid/ask (evaluate_limit_fill / evaluate_conditional_limit_fill)",
        "files": (
            "entry_baseline_replay_report.json",
            "entry_baseline_replay_episode_receipts.jsonl.gz",
            "conservative-results.jsonl.gz",
            "conservative_fill_descriptive_report.json",
        ),
    },
    # HM #850 R2 C1 (24 Oct 2026 AEDT): retired, not exempted.  Manual CLI with no importers/readers today.
    "WALK_FORWARD_1S": {
        "producer": "research/walk_forward_scorer.py",
        "price_source": "market_microstructure_1s bid/ask (fill_model.taker_fill_rows / maker_fill_rows / "
                        "realistic_exit_margin)",
        "files": (
            "walk_forward_scores.json",
            "walk_forward_scores.md",
            "walk_forward_trades.jsonl.gz",
        ),
    },
    # HM #850 R3 (24 Oct 2026 AEDT): 5th fill_model row-pricer caller.  Manual research CLI; its output path is the
    # caller's --out (no fixed basename), so there is no file list to refuse -- the CLI itself refuses to run.
    # boss_retest.py / danish_study.py import its replay() and are refused through it.
    "EXIT_STUDY_1S": {
        "producer": "diagnostics/hypothesis-tiles-20261004/exit_study.py",
        "price_source": "bitfinex_l1_tape_1s bid/ask via genome_grid_study paths + fill_model.realistic_exit_margin",
        "files": (),
    },
}

RETIRED_LABEL = "source retired (R17)"

# RK #847 cond 4 inventory (23 Oct 2026 AEDT).  Every producer that prices or triggers on the 1s tape / ticker
# ticks, by enclosing top-level function.  End state (tracker): each is moved onto paper_fill + quote_trigger
# or deleted.  test_r17_retired_sources.py re-derives this list from the AST so a new caller cannot slip in.
INVENTORY: dict[str, tuple[str, ...]] = {
    # analyzer_research_engine_v62 callers of _simulate_ticks_fast_cut_ladder / _sim_replay_pnl_usd /
    # _sim_replay_pnl_at_fill (the two _sim_replay_pnl_* helpers are themselves tick-sim helpers).
    "TICK_SIM": (
        "stop_thesis_wide_sweep_all_replays",
        "stop_ladder_2d_grid_sweep_all_replays",
        "stop_ladder_mfe_3d_sweep_all_replays",
        "qualified_exit_policy_grid_report",
        "_aggregate_cohort_replay_pnl",
        "entry_gate_replay_sweeps",
        "pullback_replay_fill_sweep",
        "entry_gate_mtf_chop_sweet_spot_sweep",
        "gate_margin_sweet_spot_analysis",
        "regime_segment_analysis",
        "margin_size_sweep_analysis",
        "thesis_fast_cut_optimization",
        "fast_cut_survivor_report",
        "fast_cut_sweep_report",
        "exit_ladder_simulator_report",  # EXIT_LADDER_SIM (#843)
    ),
    # research.conservative_limit_fill.evaluate_limit_fill / evaluate_conditional_limit_fill callers
    # (market_microstructure_1s).  Receipts are stamped R17_UNPROVEN_ENTRY + ENTRY_PRICED_ON_1S_TAPE (#848 C4).
    "LIMIT_FILL_1S": (
        "analyzer_research_engine_v62._joined_compressed_chase_tape_evidence",
        "research.entry_baseline_replay.replay_episode",
        "research.policy_evidence_evaluator.build_v3_conservative_results",
        "research.latency_schedule_replay._replay_delayed_entry",
        "research.conservative_fill_cohort.build_conservative_fill_cohort",
        "research.conservative_fill_cohort.build_v3_conservative_fill_cohort",
    ),
}

# HM #850 R2 C1: every caller of the fill_model 1s-tape row pricers, by enclosing top-level function, and the
# quarantine key that stops it.  test_r17_retired_sources.py re-derives this from the AST (repo top level +
# research/) so a new row-pricer caller cannot appear unpinned.
FILL_MODEL_ROW_PRICERS: tuple[str, ...] = ("taker_fill_rows", "maker_fill_rows", "realistic_exit_margin")
FILL_MODEL_ROW_CALLERS: dict[str, str] = {
    "research.genome_grid_study.fast_replay": "GENOME_GRID",
    "research.genome_grid_study.realistic_exit_parity": "GENOME_GRID",
    "research.walk_forward_scorer.simulate_entry": "WALK_FORWARD_1S",
    "research.walk_forward_scorer.simulate_exit": "WALK_FORWARD_1S",
}

# HM #850 R3: repo-wide pin.  Every reference to a retired 1s-tape / ticker pricer anywhere in the repo (excluding
# tests, the pricers' own definitions, fill_model.py and this registry) -- calls, attribute access, imports,
# module-level code, class methods and non-docstring string constants -- by "<repo path>::<scope>", mapped to the
# active quarantine key that stops it.  test_r17_retired_sources.py re-derives it from the AST.
SOURCE_RETIRED_R17 = "SOURCE_RETIRED_R17"
RETIRED_TAPE_PRICERS: dict[str, str] = {
    "taker_fill_rows": "fill_model (1s tape BBO)",
    "maker_fill_rows": "fill_model (1s tape trade-through)",
    "realistic_exit_margin": "fill_model (1s tape exit)",
    "_simulate_ticks_fast_cut_ladder": "analyzer tick-sim (ticker/BBO ticks)",
    "_sim_replay_pnl_usd": "analyzer tick-sim (ticker/BBO ticks)",
    "_sim_replay_pnl_at_fill": "analyzer tick-sim (ticker/BBO ticks)",
    "evaluate_limit_fill": "conservative_limit_fill (market_microstructure_1s)",
    "evaluate_conditional_limit_fill": "conservative_limit_fill (market_microstructure_1s)",
    "_evaluate_conservative_limit_fill": "analyzer alias of evaluate_limit_fill",
}
_A = "services/btc-conservative-agent/analyzer_research_engine_v62.py::"
_R = "services/btc-conservative-agent/research/"
REPO_PRICER_REFERENCES: dict[str, str] = {
    "diagnostics/hypothesis-tiles-20261004/exit_study.py::replay": "EXIT_STUDY_1S",
    _A + "<module>": "LIMIT_FILL_1S",  # import alias _evaluate_conservative_limit_fill
    _A + "_joined_compressed_chase_tape_evidence": "LIMIT_FILL_1S",
    **{_A + n: ("EXIT_LADDER_SIM" if n == "exit_ladder_simulator_report" else "TICK_SIM") for n in INVENTORY["TICK_SIM"]},
    _R + "conservative_fill_cohort.py::<module>": "LIMIT_FILL_1S",
    _R + "conservative_fill_cohort.py::build_conservative_fill_cohort": "LIMIT_FILL_1S",
    _R + "conservative_fill_cohort.py::build_v3_conservative_fill_cohort": "LIMIT_FILL_1S",
    _R + "entry_baseline_replay.py::<module>": "LIMIT_FILL_1S",
    _R + "entry_baseline_replay.py::replay_episode": "LIMIT_FILL_1S",
    _R + "latency_schedule_replay.py::<module>": "LIMIT_FILL_1S",
    _R + "latency_schedule_replay.py::_replay_delayed_entry": "LIMIT_FILL_1S",
    _R + "policy_evidence_evaluator.py::<module>": "LIMIT_FILL_1S",
    _R + "policy_evidence_evaluator.py::build_v3_conservative_results": "LIMIT_FILL_1S",
    _R + "genome_grid_study.py::fast_replay": "GENOME_GRID",
    _R + "genome_grid_study.py::realistic_exit_parity": "GENOME_GRID",
    _R + "genome_grid_study.py::canonical_parity_sample": "GENOME_GRID",  # string constant (parity rule label)
    _R + "walk_forward_scorer.py::simulate_entry": "WALK_FORWARD_1S",
    _R + "walk_forward_scorer.py::simulate_exit": "WALK_FORWARD_1S",
}
del _A, _R

# Tests may monkeypatch this set to exercise the legacy code path; production
# has no runtime switch.
ACTIVE: set[str] = set(QUARANTINE)

QUARANTINED_FILES: frozenset[str] = frozenset(
    name for spec in QUARANTINE.values() for name in spec["files"]
)


class R3QuarantinedSource(RuntimeError):
    """Raised when a quarantined producer tries to open its R3-forbidden price source."""


def guard(key: str, what: str) -> None:
    """Hard stop at the price-source boundary (defence in depth behind the producer's early return)."""
    if is_active(key):
        raise R3QuarantinedSource(f"{STATUS} {key}: refusing to open {what}. {REASON}")


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
        "label": RETIRED_LABEL,
        "countable": False,
    }
    out.update(extra)
    return out


def retired(key: str, *, writes_report: bool = False, returns: Any = None, print_once: bool = False):
    """Decorator for a quarantined producer: when ``key`` is active it runs nothing, writes nothing, prints the
    section as ``source retired (R17)`` and returns ``disabled_payload`` (report writers), ``returns(*args,
    **kwargs)`` when ``returns`` is callable (per-row producers whose callers need a shaped "no evidence" value),
    else None (stdout sweeps, whose callers already treat None as "no result").  ``print_once`` limits the
    label to the first call per process (per-row producers)."""
    import functools

    def wrap(fn):
        printed: list[bool] = []

        @functools.wraps(fn)
        def inner(*args, **kwargs):
            if is_active(key):
                if not (print_once and printed):
                    printed.append(True)
                    print(f"\n=== {fn.__name__}: {RETIRED_LABEL} — {STATUS} {key}, not run, nothing written ===")
                if writes_report:
                    return disabled_payload(key, section=fn.__name__)
                return returns(*args, **kwargs) if callable(returns) else None
            return fn(*args, **kwargs)
        inner.r3_retired_key = key
        return inner
    return wrap


def is_retired_payload(obj: Any) -> bool:
    """True for a ``disabled_payload`` (a retired producer's return value)."""
    return isinstance(obj, dict) and obj.get("status") == STATUS and obj.get("label") == RETIRED_LABEL
