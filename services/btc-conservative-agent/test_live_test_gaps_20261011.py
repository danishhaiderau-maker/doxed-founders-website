"""GS-01 first live trade gaps (11 Oct 2026): signal->intent timing and deploy_flatten tags."""
from research.close_origin import is_deploy_flatten, trade_display
import monitor_tiles


def test_is_deploy_flatten_from_tag_and_origin():
    assert is_deploy_flatten({"exit_reason": "ADMIN_MANUAL_CLOSE", "close_origin": "DEPLOY_MAINTENANCE"})
    assert is_deploy_flatten({"deploy_flatten": True})
    assert not is_deploy_flatten({"exit_reason": "ADMIN_MANUAL_CLOSE", "close_origin": "OPERATOR"})
    assert not is_deploy_flatten({"exit_reason": "TP"})
    assert trade_display({"exit_reason": "ADMIN_MANUAL_CLOSE", "close_origin": "DEPLOY_MAINTENANCE"})["deploy_flatten"] is True


def test_bot_signal_at_helper_source():
    src = open("bot.py", encoding="utf-8").read()
    assert "def _live_copy_signal_at_for(" in src
    assert "signal_at=_live_copy_signal_at_for(trade_id, local)" in src
    assert src.count("_live_copy_note_signal_ts(") >= 3  # def + two order-creation sites
    assert '"deploy_flatten": _forced_close_origin(exit_reason) == "DEPLOY_MAINTENANCE"' in src
    assert "deploy_flatten_closes_excluded" in src
    assert '"deploy_flatten": bool(' in open(monitor_tiles.__file__, encoding="utf-8").read()
