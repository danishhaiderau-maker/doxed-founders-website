import gzip
import monitor_streams as ms


def test_pages_end_on_newline_and_cursor_resumes(tmp_path):
    f = tmp_path / ms.STREAMS["liquidations"]
    f.write_bytes(b'{"a":1}\n{"a":2}\n{"a":3')
    h, body = ms.read_page(tmp_path, "liquidations", 0, 12)
    assert body == b'{"a":1}\n' and h["next_cursor"] == 8 and not h["eof"]
    h, body = ms.read_page(tmp_path, "liquidations", h["next_cursor"], 1000)
    assert body == b'{"a":2}\n' and h["next_cursor"] == 16


def test_unknown_stream_and_rotation(tmp_path):
    import pytest
    with pytest.raises(ms.BadRequest):
        ms.read_page(tmp_path, "../etc/passwd")
    f = tmp_path / ms.STREAMS["fill_markouts"]
    f.write_bytes(b'{"x":1}\n')
    h, body = ms.read_page(tmp_path, "fill_markouts", 999)
    assert h["rotated"] and body == b'{"x":1}\n'


def test_route_requires_monitor_auth_and_serves_gzip(tmp_path, monkeypatch):
    import bot
    (tmp_path / ms.STREAMS["market_context"]).write_bytes(b'{"f":1}\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_API_RATE_MAX_PER_IP", 10_000)
    client = bot.app.test_client()
    monkeypatch.setattr(bot, "monitor_integrity_authorized", lambda: False)
    assert client.get("/api/monitor/streams/market_context").status_code == 401
    monkeypatch.setattr(bot, "monitor_integrity_authorized", lambda: True)
    r = client.get("/api/monitor/streams/market_context?cursor=0")
    assert r.status_code == 200 and gzip.decompress(r.data) == b'{"f":1}\n'
    assert r.headers["X-Stream-Next-Cursor"] == "8"
    assert client.get("/api/monitor/streams").status_code == 200
