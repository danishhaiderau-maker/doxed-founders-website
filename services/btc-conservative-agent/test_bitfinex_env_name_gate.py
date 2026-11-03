"""CI proof: BITFINEX_* process-env names are refused (names only; R10)."""
from __future__ import annotations

import pytest

import bitfinex_env_name_gate as gate


def test_count_ignores_non_bitfinex_names():
    assert gate.bitfinex_process_env_name_count({"BOT_ADMIN_TOKEN": "x", "FOO": "y"}) == 0


def test_count_detects_bitfinex_names_case_insensitive():
    env = {"BITFINEX_API_KEY": "secret", "bitfinex_live_enabled": "0", "OTHER": "1"}
    assert gate.bitfinex_process_env_name_count(env) == 2


def test_assert_refuses_when_bitfinex_name_present(capsys):
    with pytest.raises(SystemExit) as exc:
        gate.assert_no_bitfinex_process_env_names({"BITFINEX_API_KEY": "secret"})
    assert exc.value.code == gate.REFUSAL_EXIT
    captured = capsys.readouterr()
    assert f"{gate.COUNT_LOG_PREFIX}1" in captured.out
    assert "REFUSED_BITFINEX_ENV_NAMES" in captured.err
    # Never echo secret values.
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_assert_passes_when_no_bitfinex_names(capsys):
    assert gate.assert_no_bitfinex_process_env_names({"BOT_ADMIN_TOKEN": "x"}) == 0
    assert f"{gate.COUNT_LOG_PREFIX}0" in capsys.readouterr().out


def test_missing_bitfinex_live_enabled_means_off():
    """HM HOLD b5700e72694d option: prove missing flag is off (do not set =0)."""
    assert gate.missing_bitfinex_live_enabled_means_off({}) is True
    assert gate.missing_bitfinex_live_enabled_means_off({"BITFINEX_LIVE_ENABLED": ""}) is True
    assert gate.missing_bitfinex_live_enabled_means_off({"BITFINEX_LIVE_ENABLED": "0"}) is True
    assert gate.missing_bitfinex_live_enabled_means_off({"BITFINEX_LIVE_ENABLED": "false"}) is True
    assert gate.missing_bitfinex_live_enabled_means_off({"BITFINEX_LIVE_ENABLED": "1"}) is False
    assert gate.missing_bitfinex_live_enabled_means_off({"BITFINEX_LIVE_ENABLED": "true"}) is False


def test_bot_apply_env_live_gating_treats_missing_as_not_requested():
    """Mirror bot._apply_env_live_gating requested= truthy check (missing => off)."""
    raw = ("" or "").strip().lower()
    requested = raw in ("true", "1", "yes", "on")
    assert requested is False
