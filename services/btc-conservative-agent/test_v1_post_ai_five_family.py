"""Pathway Lab v1_post_ai must accept the five-family paper roster.

The previous contract required OFFSET_029_ATR_TP_25 to be the only executable
combo lane. That lane is retired, so startup raised SystemExit and Fly
restarted with rc=1. Score-led admission changes policy identity, not roster.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pathway_lab_validation as validation
from combo_pathway_config import (
    COMBO_EXECUTION_LANES,
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
)

BOT = Path(__file__).with_name("bot.py")


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_v1_post_ai_passes_for_five_family_roster():
    spawn_src = _function_source(BOT, "spawn_combo_lanes_from_ai_scan")
    process_src = _function_source(BOT, "process_signal")
    checks = validation.build_v1_post_ai_checks(spawn_src, process_src)
    failed = [row for row in checks if not row["passed"]]
    assert not failed, failed
    names = [row["check"] for row in checks]
    assert names[:3] == [
        "five-family tiles are the only executable combo lanes",
        "retired offset and Type B are outside the executable roster",
        "each family tile consumes shared AI direction without its own prompt",
    ]
    assert names[3:] == [
        "generic fan-out routes only the executable allowlist",
        "process_signal contains one shared AI evaluator call",
        "retired Type B has no process_signal fan-out",
        "legacy dispatchers are absent from active shared fan-out",
    ]
    lanes = validation.five_family_execution_lanes()
    assert tuple(lanes) == tuple(COMBO_EXECUTION_LANES)
    assert RESEARCH_LANE_OFFSET_029_ATR_TP_25 not in lanes
    assert RESEARCH_LANE_TYPE_B_HUNTER_V1 not in lanes
    runner = _function_source(Path(__file__).with_name("pathway_lab_validation.py"), "run_independent_v1_post_ai_spawn_validation")
    assert "build_v1_post_ai_checks(" in runner
    assert "five_family_shared_direction_post_ai_spawn_validation_v3" in runner
    assert "v15-typeb-opportunity-v2" not in runner
    assert validation.EXECUTION_FIX_VERSION in (
        "v31-five-family-analyzer-hypothesis-paper",
        "v31-five-family-score-led-paper-v1",
    )


def test_retired_offset_only_contract_does_not_match_current_roster():
    assert tuple(COMBO_EXECUTION_LANES) != (RESEARCH_LANE_OFFSET_029_ATR_TP_25,)
    assert tuple(COMBO_EXECUTION_LANES) == validation.five_family_execution_lanes()


def test_score_led_flag_keeps_the_same_family_roster():
    import subprocess
    import sys

    script = """
import os
os.environ["SCORE_LED_PAPER_RESEARCH_ENABLED"] = "1"
from combo_pathway_config import COMBO_EXECUTION_LANES, EXECUTION_FIX_VERSION
from pathway_lab_validation import five_family_execution_lanes
assert tuple(COMBO_EXECUTION_LANES) == five_family_execution_lanes()
assert EXECUTION_FIX_VERSION == "v31-five-family-score-led-paper-v1"
print("score-led-roster-ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "score-led-roster-ok" in result.stdout
