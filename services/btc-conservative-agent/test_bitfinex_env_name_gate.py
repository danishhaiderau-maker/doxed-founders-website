"""CI proof: BITFINEX_* KEY/SECRET/TOKEN refused; LIVE_ENABLED='0' exempt (R10)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import bitfinex_env_name_gate as gate

_HERE = Path(__file__).resolve().parent


def test_count_ignores_non_bitfinex_names():
    assert gate.bitfinex_process_env_name_count({"BOT_ADMIN_TOKEN": "x", "FOO": "y"}) == 0


def test_count_detects_key_secret_token_case_insensitive():
    env = {
        "BITFINEX_API_KEY": "secret",
        "BITFINEX_API_SECRET": "s",
        "BITFINEX_READ_TOKEN": "t",
        "bitfinex_live_enabled": "0",
        "OTHER": "1",
    }
    # LIVE_ENABLED=0 exempt; three credential names refused.
    assert gate.bitfinex_process_env_name_count(env) == 3


def test_live_enabled_exact_zero_exempt():
    assert gate.bitfinex_process_env_name_count({"BITFINEX_LIVE_ENABLED": "0"}) == 0
    assert gate.live_enabled_exact_zero_is_exempt({"BITFINEX_LIVE_ENABLED": "0"}) is True
    assert gate.assert_no_bitfinex_process_env_names({"BITFINEX_LIVE_ENABLED": "0"}) == 0


@pytest.mark.parametrize("bad", ["1", "true", "false", "", "00", " 0", "0 "])
def test_live_enabled_any_other_value_refuses(bad, capsys):
    with pytest.raises(SystemExit) as exc:
        gate.assert_no_bitfinex_process_env_names({"BITFINEX_LIVE_ENABLED": bad})
    assert exc.value.code == gate.REFUSAL_EXIT
    captured = capsys.readouterr()
    assert f"{gate.COUNT_LOG_PREFIX}1" in captured.out
    assert "REFUSED_BITFINEX_ENV_NAMES" in captured.err
    # Never echo the raw env value as its own token (R10); count digits are ok.
    if bad and bad not in ("1", "0"):
        assert f"={bad}" not in captured.out
        assert f"={bad}" not in captured.err
        assert bad not in captured.err


def test_assert_refuses_when_api_key_present(capsys):
    with pytest.raises(SystemExit) as exc:
        gate.assert_no_bitfinex_process_env_names({"BITFINEX_API_KEY": "secret"})
    assert exc.value.code == gate.REFUSAL_EXIT
    captured = capsys.readouterr()
    assert f"{gate.COUNT_LOG_PREFIX}1" in captured.out
    assert "REFUSED_BITFINEX_ENV_NAMES" in captured.err
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_assert_passes_when_no_refused_names(capsys):
    assert (
        gate.assert_no_bitfinex_process_env_names(
            {"BOT_ADMIN_TOKEN": "x", "BITFINEX_LIVE_ENABLED": "0"}
        )
        == 0
    )
    assert f"{gate.COUNT_LOG_PREFIX}0" in capsys.readouterr().out


def test_non_credential_bitfinex_name_without_key_secret_token_allowed():
    # Ruling targets KEY/SECRET/TOKEN (+ LIVE_ENABLED non-0). Harmless names pass.
    assert gate.bitfinex_process_env_name_count({"BITFINEX_WALLET_LABEL": "paper"}) == 0


def test_inject_fake_key_subprocess_refusal():
    """Executing refusal: child process with injected fake key exits 79."""
    code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(_HERE)!r})\n"
        "import bitfinex_env_name_gate as g\n"
        "os.environ['BITFINEX_API_KEY'] = 'fake-not-real'\n"
        "os.environ['BITFINEX_LIVE_ENABLED'] = '0'\n"
        "g.assert_no_bitfinex_process_env_names()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(_HERE),
        check=False,
    )
    assert result.returncode == gate.REFUSAL_EXIT, result.stderr
    assert "BITFINEX_ENV_NAME_COUNT count=1" in result.stdout
    assert "REFUSED_BITFINEX_ENV_NAMES" in result.stderr
    assert "fake-not-real" not in result.stdout
    assert "fake-not-real" not in result.stderr


def test_inject_live_zero_only_subprocess_passes():
    code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(_HERE)!r})\n"
        "import bitfinex_env_name_gate as g\n"
        "os.environ.pop('BITFINEX_API_KEY', None)\n"
        "os.environ['BITFINEX_LIVE_ENABLED'] = '0'\n"
        "raise SystemExit(0 if g.assert_no_bitfinex_process_env_names() == 0 else 1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(_HERE),
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "BITFINEX_ENV_NAME_COUNT count=0" in result.stdout


def test_bot_apply_env_live_gating_treats_zero_as_not_requested(monkeypatch):
    """Call real bot._apply_env_live_gating with BITFINEX_LIVE_ENABLED=0."""
    import ast
    import types

    bot_path = _HERE / "bot.py"
    tree = ast.parse(bot_path.read_text(encoding="utf-8"))
    fn_node = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_env_live_gating"
        ),
        None,
    )
    assert fn_node is not None, "_apply_env_live_gating missing in bot.py"
    module = types.ModuleType("_gating_probe")
    state: dict = {}
    state_lock = __import__("threading").RLock()

    class _Logger:
        def warning(self, *a, **k):
            return None

    module.__dict__.update(
        state=state,
        state_lock=state_lock,
        logger=_Logger(),
        os=os,
        save_persistent_config=lambda: None,
    )
    exec(  # noqa: S102 — test extracts one bot function
        compile(ast.Module(body=[fn_node], type_ignores=[]), str(bot_path), "exec"),
        module.__dict__,
    )
    monkeypatch.setenv("BITFINEX_LIVE_ENABLED", "0")
    monkeypatch.setenv("FORCE_PAPER_MODE", "1")
    state["bitfinex_live_enabled"] = True
    state["live_armed"] = True
    module._apply_env_live_gating()
    assert state["bitfinex_live_enabled"] is False
    assert state["live_armed"] is False
    assert state["live_startup_requested"] is False


def test_bot_apply_env_live_gating_true_sets_startup_requested(monkeypatch):
    import ast
    import types

    bot_path = _HERE / "bot.py"
    tree = ast.parse(bot_path.read_text(encoding="utf-8"))
    fn_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_apply_env_live_gating"
    )
    module = types.ModuleType("_gating_probe")
    state: dict = {}
    module.__dict__.update(
        state=state,
        state_lock=__import__("threading").RLock(),
        logger=type("L", (), {"warning": staticmethod(lambda *a, **k: None)})(),
        os=os,
        save_persistent_config=lambda: None,
    )
    exec(  # noqa: S102
        compile(ast.Module(body=[fn_node], type_ignores=[]), str(bot_path), "exec"),
        module.__dict__,
    )
    monkeypatch.setenv("BITFINEX_LIVE_ENABLED", "1")
    monkeypatch.delenv("FORCE_PAPER_MODE", raising=False)
    module._apply_env_live_gating()
    assert state["live_startup_requested"] is True
    assert state["bitfinex_live_enabled"] is False
    assert state["live_armed"] is False


def test_btc_conservative_agent_wires_gate():
    text = (_HERE / "btc_conservative_agent.py").read_text(encoding="utf-8")
    assert "assert_no_bitfinex_process_env_names" in text
    assert "_refuse_bitfinex_process_env_names" in text


def test_analyzer_v62_wires_gate_on_main():
    text = (_HERE / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    assert "assert_no_bitfinex_process_env_names" in text
    # Gate runs under the __main__ entry (launched process), not at import.
    main_idx = text.rfind('if __name__ == "__main__":')
    gate_idx = text.find("assert_no_bitfinex_process_env_names", main_idx)
    assert main_idx > 0 and gate_idx > main_idx
