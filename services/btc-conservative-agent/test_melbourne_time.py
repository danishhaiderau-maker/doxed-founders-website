"""One shared display-time helper: Australia/Melbourne, labelled "Melbourne time"."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import melbourne_time as mt

SUMMER = datetime(2026, 10, 4, 8, 41, tzinfo=timezone.utc).timestamp()   # after DST starts
WINTER = datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc).timestamp()


def test_format_follows_daylight_saving_with_one_label() -> None:
    assert mt.format_melbourne(SUMMER) == "2026-10-04 19:41 Melbourne time"
    assert mt.format_melbourne(WINTER) == "2026-07-01 11:00 Melbourne time"
    assert mt.format_melbourne("2026-10-04T08:41:00Z", seconds=True) == "2026-10-04 19:41:00 Melbourne time"
    assert mt.format_melbourne(SUMMER * 1000, date=False) == "19:41 Melbourne time"
    assert mt.utc_offset_hours(SUMMER) == 11 and mt.utc_offset_hours(WINTER) == 10
    assert mt.abbrev_stamp(SUMMER) == "2026-10-04 19:41:00 AEDT"
    assert mt.abbrev_stamp(WINTER) == "2026-07-01 11:00:00 AEST"
    assert mt.format_melbourne(None) is None and mt.format_melbourne("junk", default="-") == "-"


def test_fallback_without_tzdata_applies_victorian_rule(monkeypatch) -> None:
    monkeypatch.setattr(mt, "_ZONE", None)
    assert mt.format_melbourne(SUMMER) == "2026-10-04 19:41 Melbourne time"
    assert mt.format_melbourne(WINTER) == "2026-07-01 11:00 Melbourne time"
    edge = lambda *a: mt.utc_offset_hours(datetime(*a, tzinfo=timezone.utc))  # noqa: E731
    assert edge(2026, 10, 3, 15, 59) == 10 and edge(2026, 10, 3, 16, 0) == 11
    assert edge(2027, 4, 3, 15, 59) == 11 and edge(2027, 4, 3, 16, 0) == 10


def test_displays_use_the_shared_helper_not_fixed_offsets() -> None:
    import runtime_uptime as ru
    import system_health_alerts as sha

    assert ru.clock(SUMMER, SUMMER) == {"melbourne": "19:41 Melbourne time", "aest": "19:41 Melbourne time",
                                        "utc": "08:41 UTC"}
    assert sha.times(SUMMER)["melbourne"] == "2026-10-04 19:41 Melbourne time"
    page = sha.render_alerts_html({"alerts": [], "counts": {}, "generated_at": None})
    assert "Times are Melbourne time" in page and "UTC+10" not in page and "AEST" not in page
    here = Path(__file__).resolve().parent
    for rel in ("runtime_uptime.py", "system_health_alerts.py", "system_health_banner.py",
                "../../scripts/self_aware/digest.py"):
        text = (here / rel).read_text(encoding="utf-8")
        assert "hours=10" not in text and '"AEST"' not in text and "UTC+10" not in text, rel


def test_research_dashboard_and_digest_labels() -> None:
    import sys

    from research.research_dashboard import format_melbourne_dt

    assert format_melbourne_dt(SUMMER) == "2026-10-04 19:41:00 Melbourne time"
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from self_aware import digest

    assert digest._aest(SUMMER) == "2026-10-04 19:41 Melbourne time"


def test_bot_interchange_stamp_and_dashboard_label() -> None:
    src = (Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8")
    assert "_mt.abbrev_stamp(dt, default=s)" in src
    assert "Melbourne time`;" in src and "new Date().toLocaleTimeString()" not in src
