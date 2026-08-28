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

    # Tile headlines deliberately show one comparable fresh-collection
    # accounting row only. Shadow/counterfactual results stay in analyzer
    # reports and must never be mixed into account-like tile PnL.
    assert "statRow('Closed'" in source
    assert "statRow('PnL'" in source
    assert "statRow('EV/appr'" in source
    assert "statRow('Shadow trades'" not in source
    assert "statRow('Shadow PnL'" not in source
    assert "Counterfactual PnL (not account)" not in source

    assert "'calculating…'" in source
    assert 'unrealUsd < 0 ? \'-$\' : \'$\'' in source

    print("Dashboard research-status clarity tests passed")


if __name__ == "__main__":
    main()
