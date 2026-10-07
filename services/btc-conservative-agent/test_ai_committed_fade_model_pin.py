"""PHASE02: the committed-fade tiles' AI model is version-pinned.

The shared ``trading_direction`` call that drives the committed-fade tiles
(H-A, GS-06) must stay reproducible across provider model renames. DeepSeek
retired ``deepseek-v4-flash`` (2026-10-01) and answers those requests as
``deepseek-flash`` (DeepSeek-V4.1-Flash); that served id is the explicit pin.
"""
import os

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import pytest

import bot
from combo_pathway_config import ACTIVE_TILE_REGISTRY, COMBO_LANE_SPECS

COMMITTED_FADE_LANES = (
    "FAMILY_COMMITTED_FADE_TAKER_90",   # H-A
    "FAMILY_GS06_COMMITTED_FADE_ATR_TP",  # GS-06
)


def test_committed_fade_model_is_pinned_to_the_served_id():
    # The pin is the DeepSeek-V4.1-Flash served id, not a floating "latest".
    assert bot.DEEPSEEK_DEFAULT_MODEL == "deepseek-flash"
    assert "deepseek-flash" in bot.DEEPSEEK_SUPPORTED_MODELS
    assert bot.DEEPSEEK_RETIRED_MODEL_ALIASES.get("deepseek-v4-flash") == "deepseek-flash"


def test_model_resolution_returns_the_pinned_id_and_fails_closed():
    original = os.environ.get("DEEPSEEK_MODEL")
    try:
        os.environ.pop("DEEPSEEK_MODEL", None)
        assert bot._deepseek_model() == "deepseek-flash"
        os.environ["DEEPSEEK_MODEL"] = "deepseek-v4-flash"
        assert bot._deepseek_model() == "deepseek-flash"  # retired alias pins to the served id
        os.environ["DEEPSEEK_MODEL"] = "some-future-untracked-id"
        with pytest.raises(RuntimeError):
            bot._deepseek_model()
    finally:
        if original is None:
            os.environ.pop("DEEPSEEK_MODEL", None)
        else:
            os.environ["DEEPSEEK_MODEL"] = original


def test_committed_fade_tiles_consume_the_shared_ai_call():
    # Both committed-fade tiles fade the shared AI's committed call; their
    # directional behaviour is exactly what the pinned model produces.
    for lane in COMMITTED_FADE_LANES:
        assert lane in ACTIVE_TILE_REGISTRY, lane
        entry = COMBO_LANE_SPECS[lane]["entry_policy"]
        assert entry["direction_source"] == "INVERTED_SCORE_LED_SIDE", lane


def test_no_tile_carries_a_per_tile_model_override():
    # The pin is global; no active tile may silently redirect its AI call to a
    # different model id.
    for lane, spec in ACTIVE_TILE_REGISTRY.items():
        entry = spec.get("entry_policy") or {}
        for key in ("ai_model", "deepseek_model", "model", "model_version"):
            assert key not in entry, f"{lane} carries a per-tile model override: {key}"
