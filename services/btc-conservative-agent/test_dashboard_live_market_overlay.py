"""Ensure active dashboard overlays cannot retain stale boot-time market health."""

from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def main() -> None:
    for field in (
        "price_ts",
        "price_source",
        "data_source",
        "data_quality",
        "ws_ready",
        "ws_age",
        "ws_last_tick",
    ):
        assert f'"{field}": state.get("{field}")' in SOURCE or (
            field == "ws_ready"
            and '"ws_ready": bool(state.get("ws_ready", False))' in SOURCE
        )
        assert f'"{field}",' in SOURCE

    print("Dashboard live-market overlay tests passed")


if __name__ == "__main__":
    main()
