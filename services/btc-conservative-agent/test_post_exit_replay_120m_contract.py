import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
NORMALIZATION_SOURCE = (ROOT / "research" / "counterfactual_normalization.py").read_text(encoding="utf-8")


def _function_source(name):
    tree = ast.parse(SOURCE)
    node = next(
        row for row in tree.body
        if isinstance(row, (ast.FunctionDef, ast.AsyncFunctionDef)) and row.name == name
    )
    return ast.get_source_segment(SOURCE, node)


def test_post_exit_ring_survives_pre_exit_cap_and_covers_120m():
    assert 'POST_EXIT_REPLAY_TICK_MAX", "10000"' in SOURCE
    maintenance = SOURCE[SOURCE.index("for tid, buf in list(replay_buffers.items())"):]
    assert 'not buf.get("post_exit")' in maintenance
    assert "and len(buf.get(\"ticks\", [])) >= REPLAY_TICK_MAX" in maintenance


def test_executable_bbo_marks_and_restart_sidecar_are_persisted():
    service = _function_source("service_post_exit_replays")
    append = _function_source("append_replay_tick")
    restore = _function_source("_load_post_exit_replays")
    assert 'best_bid if direction == "LONG"' in service
    assert 'else best_ask' in service
    assert '"best_bid"' in append and '"best_ask"' in append
    assert '"observed_ts"' in append
    assert '"best_bid": row.get("best_bid")' in restore
    assert '"best_ask": row.get("best_ask")' in restore
    assert "rotated_paths" in restore and "replay_paths" in restore
    assert 'header.get("policy_version")' in restore


def test_missing_future_horizons_are_unknown_not_last_tick_substitutions():
    compute = _function_source("compute_horizon_outcomes_from_replay")
    assert '"1m": 60' in SOURCE
    assert '"120m": 7200' in SOURCE
    assert "horizons.setdefault" in compute
    assert "last_unreal" not in compute
    entry = _function_source("_counterfactual_entry_horizons")
    post = _function_source("_counterfactual_post_exit_horizons")
    assert "_pure_counterfactual_horizons" in entry
    assert "_pure_counterfactual_horizons" in post
    assert '"observed": observed is not None' in NORMALIZATION_SOURCE
    assert '"best_bid"' in NORMALIZATION_SOURCE and '"best_ask"' in NORMALIZATION_SOURCE
    assert 'executable_key = (' in NORMALIZATION_SOURCE


if __name__ == "__main__":
    test_post_exit_ring_survives_pre_exit_cap_and_covers_120m()
    test_executable_bbo_marks_and_restart_sidecar_are_persisted()
    test_missing_future_horizons_are_unknown_not_last_tick_substitutions()
    print("PASS: 120m replay collection and truthful horizon contract")
