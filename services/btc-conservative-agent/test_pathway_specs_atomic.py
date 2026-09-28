import json
from pathlib import Path

import bot


def test_bot_pathway_specs_are_complete_before_replace(tmp_path, monkeypatch):
    payload = {"schema": "pathway_specs_test", "lanes": [{"lane": "CONTINUOUS"}]}
    monkeypatch.setattr(bot, "build_static_pathway_lane_specs", lambda: payload)
    monkeypatch.setattr(
        bot,
        "_merge_pathway_specs_with_session_stats",
        lambda static, _existing: static,
    )
    observed = {}
    real_replace = bot.os.replace

    def checked_replace(source, destination):
        observed["candidate"] = json.loads(Path(source).read_text(encoding="utf-8"))
        assert Path(destination).name == bot.PATHWAY_LANE_SPECS_FILE
        real_replace(source, destination)

    monkeypatch.setattr(bot.os, "replace", checked_replace)
    result = bot.write_static_pathway_lane_specs(str(tmp_path))

    target = tmp_path / bot.PATHWAY_LANE_SPECS_FILE
    assert result == payload
    assert observed["candidate"] == payload
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert not list(tmp_path.glob("*.tmp"))
