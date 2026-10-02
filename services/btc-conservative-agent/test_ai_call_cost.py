"""DeepSeek per-call cost accounting and 24h persistence."""
from pathlib import Path

import pytest

import ai_call_cost as cost

BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_price_overrides(monkeypatch):
    for env in cost._ENV_OVERRIDES.values():
        monkeypatch.delenv(env, raising=False)


def test_cache_split_is_priced_per_bucket():
    row = cost.compute_cost("deepseek-chat", {
        "prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
        "prompt_cache_hit_tokens": 400_000, "prompt_cache_miss_tokens": 600_000,
    })
    assert row["cost_usd"] == pytest.approx(0.4 * 0.028 + 0.6 * 0.28 + 0.42)
    assert row["cache_split_reported"] is True and row["pricing_model_matched"] is True


def test_missing_cache_split_prices_all_prompt_as_miss():
    row = cost.compute_cost("deepseek-chat", {"prompt_tokens": 2_000_000, "completion_tokens": 0})
    assert row["cost_usd"] == pytest.approx(0.56)
    assert row["cache_miss_tokens"] == 2_000_000


def test_unknown_model_uses_default_and_is_flagged():
    row = cost.compute_cost("deepseek-flash", {"prompt_tokens": 1000, "completion_tokens": 1000})
    assert row["pricing_model_matched"] is False
    assert row["cost_usd"] > 0


def test_env_override(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_PRICE_OUTPUT_PER_M", "1.0")
    row = cost.compute_cost("deepseek-chat", {"completion_tokens": 1_000_000})
    assert row["cost_usd"] == pytest.approx(1.0) and row["pricing_env_override"] is True


def test_rolling_24h_totals_persist_across_restart(tmp_path):
    clock = {"now": 100_000.0}
    path = tmp_path / cost.STATE_FILE
    ledger = cost.AiCostLedger(path, clock=lambda: clock["now"])
    ledger.record("deepseek-chat", {"prompt_tokens": 1_000_000}, purpose="trading_direction")
    clock["now"] += 3600
    ledger.record("deepseek-chat", {"completion_tokens": 1_000_000}, purpose="trading_direction")
    restarted = cost.AiCostLedger(path, clock=lambda: clock["now"])
    snap = restarted.snapshot()
    assert snap["calls_24h"] == 2
    assert snap["cost_usd_24h"] == pytest.approx(0.28 + 0.42)
    assert snap["last_call_cost_usd"] == pytest.approx(0.42)
    assert snap["last_call_purpose"] == "trading_direction"
    clock["now"] += cost.WINDOW_SEC
    assert restarted.snapshot()["calls_24h"] == 1
    clock["now"] += 3600
    assert restarted.snapshot()["calls_24h"] == 0


def test_persist_failure_is_counted_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    ledger = cost.AiCostLedger(blocker / cost.STATE_FILE)
    ledger.record("deepseek-chat", {"prompt_tokens": 10})
    snap = ledger.snapshot()
    assert snap["calls_24h"] == 1 and snap["persist_failures"] == 1


def test_bot_records_cost_and_counts_deepseek_429():
    assert "_AI_COST_LEDGER.record(" in BOT_SOURCE
    assert '_RATE_LIMITS.hit("deepseek", purpose)' in BOT_SOURCE
    assert "**_ai_call_cost_fields(now)" in BOT_SOURCE
