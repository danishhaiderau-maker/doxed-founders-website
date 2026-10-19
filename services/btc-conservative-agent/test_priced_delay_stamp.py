"""Delay step A: rows are stamped with the delay model that priced them; no behaviour change."""
import bot


def test_labels():
    assert bot._delay_model_label("default_until_n10") == "DEFAULT_1_5S"
    assert bot._delay_model_label("lane_p50_n12") == "PAPER_MEASURED"
    assert bot._delay_model_label(None) is None
    assert bot._delay_model_label("weird").startswith("OTHER:")


def test_entry_pricing_fields_stamp_without_changing_delay(monkeypatch):
    efs = {"delay_s": 1.5, "delay_source": "default_until_n10"}
    monkeypatch.setattr(bot, "_entry_fill_sim", lambda pos: efs)
    monkeypatch.setattr(bot, "_is_paper_book_lane", lambda pos: False)
    out = bot._entry_pricing_fields({}, {})
    assert out["delay_s"] == 1.5 and out["delay_source"] == "default_until_n10"
    assert out["priced_delay_s"] == 1.5 and out["priced_delay_model"] == "DEFAULT_1_5S"


def test_monitor_tiles_exposes_the_stamp():
    import monitor_tiles
    assert "priced_delay_model" in monitor_tiles.PRICING_KEYS and "priced_delay_s" in monitor_tiles.PRICING_KEYS
