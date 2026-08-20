"""Exact live-policy provenance and two-phase boundary tests."""
from pathlib import Path
from path_replay_v1 import (
    CONTROL_CELL,
    LIVE_HARD_STOP_CLOSES_PAPER,
    LIVE_HARD_STOP_DOES_NOT_CLOSE_PAPER,
    LIVE_HARD_STOP_PCT,
    LIVE_HARD_STOP_START_SEC,
    LIVE_SCENARIO_C_LADDER,
    LIVE_THESIS_CUT,
    LIVE_THESIS_WINDOW_SEC,
    path_recovery_stats,
    replay_group_report,
    simulate_live_policy_on_path,
)


EXPECTED_LADDER = [
    (8, 5), (12, 10), (19, 17), (40, 28),
    (60, 45), (80, 60), (100, 75), (150, 120),
]

BOT_SOURCE = (Path(__file__).parent / "bot.py").read_text(encoding="utf-8")


def _sim(*points):
    ticks = [
        {"seq": i + 1, "t": t, "price": 100.0, "unreal_pct": unreal}
        for i, (t, unreal) in enumerate(points)
    ]
    return simulate_live_policy_on_path(
        ticks, direction="LONG", entry_price=100.0, leverage=100, margin_usdt=20, fill_t=0,
    )


def test_control_provenance_is_exact_and_invert_defaults_off():
    assert list(LIVE_SCENARIO_C_LADDER) == EXPECTED_LADDER
    assert CONTROL_CELL == {
        "tag": "CONTROL",
        "LIVE_CELL": True,
        "orig_offset_pct": 0.10,
        "thesis_cut": -12.0,
        "thesis_window_sec": 300.0,
        "hard_stop_pct": 30.0,
        "hard_stop_start_sec": 300.0,
        "hard_stop_closes_paper": True,
        "ladder": [list(rung) for rung in EXPECTED_LADDER],
        "invert_on": False,
    }
    assert LIVE_THESIS_CUT == -12.0
    assert LIVE_THESIS_WINDOW_SEC == 300.0
    assert LIVE_HARD_STOP_PCT == 30.0
    assert LIVE_HARD_STOP_START_SEC == 300.0
    assert LIVE_HARD_STOP_CLOSES_PAPER is True
    assert LIVE_HARD_STOP_DOES_NOT_CLOSE_PAPER is False
    assert '"hard_stop_closes_paper": False' not in BOT_SOURCE
    assert '"hard_stop_closes_paper": bool(CONTROL_CELL.get("hard_stop_closes_paper"))' in BOT_SOURCE


def test_two_phase_boundaries_are_exact():
    assert _sim((299.999, -12.0))["exit_reason"] == "THESIS_FAST_CUT"
    assert _sim((300.0, -12.0))["exit_reason"] == "PATH_END"
    assert _sim((300.0, -29.999))["exit_reason"] == "PATH_END"
    assert _sim((300.0, -30.0))["exit_reason"] == "HARD_STOP"


def test_ladder_and_phase_priority_are_deterministic():
    ladder = _sim((10.0, 8.0), (20.0, 5.0))
    assert ladder["exit_reason"] == "PROFIT_LOCK_LADDER"
    assert ladder["exit_unreal_pct"] == 5.0
    # During the thesis window, the loss cap is evaluated before a ladder floor.
    priority = _sim((1.0, 8.0), (2.0, -12.0))
    assert priority["exit_reason"] == "THESIS_FAST_CUT"


def test_group_report_live_result_and_provenance_match_exact_simulator():
    ticks = [
        {"seq": 1, "t": 300.0, "price": 100.0, "unreal_pct": -12.0},
        {"seq": 2, "t": 301.0, "price": 100.0, "unreal_pct": -30.0},
    ]
    exact = simulate_live_policy_on_path(ticks, direction="LONG", entry_price=100.0)
    report = replay_group_report(ticks, direction="LONG", entry_price=100.0, include_all=False)
    assert report["live"]["exit_reason"] == exact["exit_reason"] == "HARD_STOP"
    assert report["live_policy_untouched"] == {
        "thesis_cut": -12.0,
        "thesis_window_sec": 300.0,
        "hard_stop_pct": 30.0,
        "hard_stop_start_sec": 300.0,
        "hard_stop_closes_paper": True,
        "hard_stop_does_not_close_paper": False,
        "ladder": [list(rung) for rung in EXPECTED_LADDER],
        "note": "4pp / 5pp numbers are analysis grouping, not a live knob grid.",
    }


def test_recovery_stop_fields_are_versioned_and_unambiguous():
    stats = path_recovery_stats(
        [{"seq": 1, "t": 1.0, "price": 100.0, "unreal_pct": -20.0}],
        direction="LONG", entry_price=100.0,
    )
    assert stats["schema"] == "path_recovery_v2"
    assert stats["would_stop_13_hit"] is True
    assert stats["would_stop_30_hit"] is False
    assert stats["would_live_hard_stop_hit"] is False
