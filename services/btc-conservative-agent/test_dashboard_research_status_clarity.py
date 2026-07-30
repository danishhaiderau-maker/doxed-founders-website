"""Prevent research-only results from being presented as account orders or PnL."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT_SOURCE = ROOT / "bot.py"


def main() -> None:
    source = BOT_SOURCE.read_text(encoding="utf-8")

    assert "Verdicts are research evaluations, not orders" in source
    assert "executable orders appear only in Pending Orders above" in source
    assert "legacy call — lane metadata unavailable" in source
    assert "verdict not recorded" not in source
    assert "AI call failed — no verdict" in source
    assert "evaluation not reached" in source
    assert "RESTORED_PRE_RESTART_NO_LANE_METADATA" in source
    assert ">not evaluated</span>" not in source
    assert ">pending</span>" not in source

    assert "Shadow PnL (not account)" in source
    assert "statRow('Shadow trades'" in source
    assert "statRow('Shadow PnL'" in source
    assert "statRow('Shadow EV/close'" in source
    assert "Counterfactual PnL (not account)" in source
    assert "spec.lane !== 'CONTINUOUS'" in source

    assert "'calculating…'" in source
    assert 'unrealUsd < 0 ? \'-$\' : \'$\'' in source

    print("Dashboard research-status clarity tests passed")


if __name__ == "__main__":
    main()
