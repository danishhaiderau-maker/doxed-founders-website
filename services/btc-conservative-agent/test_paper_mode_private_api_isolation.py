"""Paper showcase must not consume Bitfinex private-auth nonces at startup."""

from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def main() -> None:
    skip_marker = (
        'elif (os.getenv("FORCE_PAPER_MODE") or "").strip().lower() '
        'in ("1", "true", "yes", "on"):'
    )
    assert skip_marker in SOURCE
    assert "Bitfinex private startup probe skipped" in SOURCE
    assert SOURCE.index(skip_marker) < SOURCE.index("balance = bitfinex_private.fetch_balance()")
    print("Paper-mode private API isolation tests passed")


if __name__ == "__main__":
    main()
