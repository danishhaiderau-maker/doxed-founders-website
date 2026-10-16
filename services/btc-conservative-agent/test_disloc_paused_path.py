"""#668 follow-up: _disloc_paused_path must resolve disloc_engine itself (it ran with a bare _dsl and raised
NameError, which made /api/monitor/tiles/disloc return {"error": "NameError"} after the 17 Oct deploy)."""
import os
import time

import bot


def test_paused_path_is_under_cwd_and_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import disloc_engine as dsl
    assert bot._disloc_paused_path() == os.path.join(str(tmp_path), dsl.PAUSED_SKIPS_FILE)


def test_tiles_disloc_route_builds_for_both_variants(monkeypatch):
    monkeypatch.setattr(bot, "_monitor_tiles_guard", lambda build, n=None: build(time.time()))
    for variant in ("base", "foi"):
        with bot.app.test_request_context(f"/api/monitor/tiles/disloc?variant={variant}"):
            out = bot.monitor_tiles_disloc()
        assert out["schema"] == "monitor_tiles_disloc_v1" and "paused_skips_durable" in out
