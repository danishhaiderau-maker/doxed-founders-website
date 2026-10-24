"""Delay step A: rows are stamped with the delay model that priced them; no behaviour change."""
import bot


def _label(src, monkeypatch):
    monkeypatch.setattr(bot, "_entry_fill_sim", lambda pos: {"delay_s": 1.5, "delay_source": src})
    monkeypatch.setattr(bot, "_is_paper_book_lane", lambda pos: False)
    return bot._entry_pricing_fields({}, {})["priced_delay_model"]


def test_labels(monkeypatch):
    assert _label("default_until_n10", monkeypatch) == "DEFAULT_1_5S"
    assert _label("lane_p50_n12", monkeypatch) == "PAPER_MEASURED"
    assert _label(None, monkeypatch) is None
    assert _label("weird", monkeypatch).startswith("OTHER:")


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


def test_max_and_q90_pool_labels_are_measured(monkeypatch):
    assert _label("lane_max_n12", monkeypatch) == "PAPER_MEASURED"
    assert _label("lane_q90_n25", monkeypatch) == "PAPER_MEASURED"
