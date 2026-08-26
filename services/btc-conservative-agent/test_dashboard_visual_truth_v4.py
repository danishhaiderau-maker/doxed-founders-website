from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def test_connected_but_old_websocket_is_visibly_stale():
    assert "const wsStale = wsAgeSec != null && wsAgeSec > wsStaleSec;" in SOURCE
    assert "&& !wsConnected" not in SOURCE


def test_missing_lane_toggle_does_not_default_on():
    assert "Object.prototype.hasOwnProperty.call(m, spec.lane)" in SOURCE
    assert "return spec.default_enabled === true;" in SOURCE
    assert "return m[spec.lane] !== false;" not in SOURCE


def test_public_tile_area_has_truthful_owner_only_empty_state():
    assert "Owner-only tile details are unavailable in this view" in SOURCE
    assert "no tile status is inferred" in SOURCE


def test_dashboard_explains_shared_call_correlation():
    assert "child outcomes remain one correlated AI cluster" in SOURCE
