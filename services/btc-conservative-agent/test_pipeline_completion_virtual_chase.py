import ast
from pathlib import Path


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
TREE = ast.parse(BOT_SOURCE)


def _compiled_validator(warnings):
    node = next(
        item for item in TREE.body
        if isinstance(item, ast.FunctionDef) and item.name == "validate_pipeline_completion"
    )
    namespace = {
        "logger": type(
            "Logger",
            (),
            {
                "error": lambda *_args, **_kwargs: None,
                "warning": lambda *_args, **_kwargs: warnings.append("warning"),
                "info": lambda *_args, **_kwargs: None,
            },
        )(),
        "enforce_log": lambda *_args, **_kwargs: warnings.append("enforce"),
        "full_pipeline_trace": lambda *_args, **_kwargs: warnings.append("trace"),
        "SIGNAL_STATUS_AWAITING_MIN_AGE": "AWAITING_MIN_AGE",
        "SIGNAL_STATUS_AWAITING_MICRO": "AWAITING_MICRO",
        "SIGNAL_STATUS_AWAITING_5M": "AWAITING_5M",
        "SIGNAL_STATUS_AWAITING_CHASE_3PLUS": "AWAITING_CHASE_3PLUS",
        "SIGNAL_STATUS_AWAITING_DASHBOARD_CHASE": "AWAITING_DASHBOARD_CHASE",
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<pipeline-validator>", "exec"), namespace)
    return namespace["validate_pipeline_completion"]


def test_dashboard_virtual_chase_is_a_valid_nonterminal_completion_state():
    warnings = []
    validate = _compiled_validator(warnings)

    assert validate({"trade_id": "fhy-waiting", "status": "AWAITING_DASHBOARD_CHASE"}) is True
    assert warnings == []


def test_unknown_completion_state_still_emits_pipeline_warning():
    warnings = []
    validate = _compiled_validator(warnings)

    assert validate({"trade_id": "fhy-bad", "status": "UNKNOWN_STATE"}) is False
    assert warnings == ["warning", "enforce", "trace"]
