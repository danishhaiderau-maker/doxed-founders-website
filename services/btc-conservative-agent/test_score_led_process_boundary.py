"""Execute the actual process_signal boundary AST without starting the bot."""
import ast
import copy
from pathlib import Path

import pytest

from score_led_paper import project_score_led_paper


SPEC = {"admission_treatment": "SCORE_LED_PAPER_V1", "paper_only": True,
        "platform_relay_eligible": False, "live_copy_eligible": False}


def boundary():
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    process = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "process_signal")
    blocks = [n for n in ast.walk(process) if isinstance(n, ast.If)
              and 'ai.get(\'admission_treatment\') == \'SCORE_LED_PAPER_V1\'' == ast.unparse(n.test)]
    assert len(blocks) == 1
    wrapper = ast.parse("def run(ai):\n    pass\n").body[0]
    wrapper.body = [copy.deepcopy(blocks[0]), ast.Return(value=ast.Name(id="ai", ctx=ast.Load()))]
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    namespace = {"COMBO_LANE_SPECS": {"TEST": SPEC}, "research_lane": "TEST",
                 "_force_paper_mode_active": lambda: True,
                 "state": {"live_armed": False}, "invert_signal_active": lambda: False}
    exec(compile(module, str(Path(__file__).with_name("bot.py")), "exec"), namespace)
    return namespace["run"], namespace


def child():
    ai, _ = project_score_led_paper(
        {"decision": "REJECT", "raw_decision": "REJECT", "explicit_abstain": True,
         "long_score": 20, "short_score": 35}, spec=SPEC,
        force_paper=True, live_armed=False)
    ai["trade_id"] = "original-child-id"
    return ai


def test_actual_boundary_preserves_valid_short_and_metadata():
    run, _ = boundary()
    ai = child()
    original = copy.deepcopy(ai)
    assert run(ai) is ai
    assert ai == original
    assert ai["direction"] == "SHORT"


@pytest.mark.parametrize("field,value", [("direction", "LONG"),
    ("candidate_direction", "LONG"), ("direction", None), ("candidate_direction", None)])
def test_actual_boundary_rejects_changed_or_missing_direction(field, value):
    run, _ = boundary()
    ai = child()
    ai[field] = value
    assert run(ai) == {"entry_resolution": "NO_ORDER",
                       "exact_reason": "SCORE_LED_DIRECTION_PROJECTION_MISMATCH"}


def test_actual_boundary_still_rejects_live_arming():
    run, namespace = boundary()
    namespace["state"]["live_armed"] = True
    assert run(child()) == {"entry_resolution": "NO_ORDER",
                           "exact_reason": "SCORE_LED_PAPER_BOUNDARY_REQUIRED"}
