"""Prevent research-only results from being presented as account orders or PnL."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT_SOURCE = ROOT / "bot.py"


def main() -> None:
    source = BOT_SOURCE.read_text(encoding="utf-8")

    assert "Verdicts are research evaluations, not orders" in source
    assert "executable orders appear only in Pending Orders above" in source
    assert 'title="No research verdict was recorded. This is not a pending order."' in source
    assert ">not evaluated</span>" in source
    assert ">pending</span>" not in source

    assert "Shadow PnL (not account)" in source
    assert "statRow('Shadow trades'" in source
    assert "statRow('Shadow PnL'" in source
    assert "statRow('Shadow EV/close'" in source

    print("Dashboard research-status clarity tests passed")


if __name__ == "__main__":
    main()
