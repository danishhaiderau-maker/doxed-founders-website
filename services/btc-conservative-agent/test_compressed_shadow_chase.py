import hashlib
import json
from pathlib import Path

from chase_offset_touch_grid import (
    COMPRESSED_SHADOW_EXPIRY_SEC,
    COMPRESSED_SHADOW_POLICY_SIGNATURE,
    COMPRESSED_SHADOW_STAGE_SECONDS,
    arm_compressed_shadow_chase,
    poll_compressed_shadow_chase,
    recover_compressed_shadow_states,
)


def _armed():
    return arm_compressed_shadow_chase(
        trade_id="cont-test", direction="LONG", signal_price=100_000,
        signal_ts=1_000, initial_limit_price=99_900,
        shared_ai_call_id="scan-1", opportunity_id="opp-1",
        episode_id="episode-1", epoch_id="epoch-1",
        requested_qty=0.00025, requested_margin_usd=0.25, leverage=100,
        fee_profile="BITFINEX_ZERO", entry_fee_rate=0.0,
        exit_fee_rate=0.0,
    )


def test_signed_schedule_and_stage_zero_are_shadow_only():
    state, row = _armed()
    assert COMPRESSED_SHADOW_STAGE_SECONDS == (0, 60, 120, 240, 420, 600)
    assert COMPRESSED_SHADOW_EXPIRY_SEC == 780
    assert COMPRESSED_SHADOW_POLICY_SIGNATURE.startswith("policy-sha256-")
    assert len(COMPRESSED_SHADOW_POLICY_SIGNATURE.removeprefix("policy-sha256-")) == 64
    assert row["stage_index"] == 0
    assert row["stage_due_sec"] == 0
    assert row["places_order"] is False
    assert row["relay_eligible"] is False
    assert row["shared_ai_call_id"] == "scan-1"
    assert row["opportunity_id"] == "opp-1"
    assert row["episode_id"] == "episode-1"
    assert row["schedule_generation_id"].startswith("shadow-generation-")
    assert len(row["schedule_generation_id"].removeprefix("shadow-generation-")) == 64
    assert row["tape_evidence_path"] == "market_microstructure_1s.jsonl"
    assert row["event_id"].startswith("event:compressed-chase:")
    assert row["requested_qty"] == 0.00025
    assert row["requested_margin_usd"] == 0.25
    assert row["fee_profile"] == "BITFINEX_ZERO"
    assert row["entry_fee_rate"] == 0.0
    assert row["exit_fee_rate"] == 0.0
    assert row["slippage_model"] == "SIGNED_BBO_DEPTH_EXPLICIT_FEES_V1"
    assert state["expires_ts"] == 1780


def test_poll_emits_each_due_stage_once_with_bbo_and_terminal_expiry():
    state, _ = _armed()
    rows = poll_compressed_shadow_chase(
        state, now_ts=1421, last=100_200, bid=100_190, ask=100_210,
    )
    assert [row["stage_index"] for row in rows] == [1, 2, 3, 4]
    assert [row["coverage_status"] for row in rows] == [
        "COVERAGE_GAP_OVERDUE", "COVERAGE_GAP_OVERDUE",
        "COVERAGE_GAP_OVERDUE", "OBSERVED",
    ]
    assert rows[0]["bbo"] == {"bid": None, "ask": None, "last": None}
    assert rows[-1]["bbo"] == {"bid": 100_190, "ask": 100_210, "last": 100_200}
    assert rows[-1]["scheduled_due_ts"] == 1420
    assert rows[-1]["observed_delay_sec"] == 1
    assert all(row["eligible_at_stage"] is False for row in rows)
    assert poll_compressed_shadow_chase(
        state, now_ts=1421, last=100_200, bid=100_190, ask=100_210,
    ) == []
    final = poll_compressed_shadow_chase(
        state, now_ts=1780, last=100_300, bid=100_290, ask=100_310,
    )
    assert [row["stage_index"] for row in final[:-1]] == [5]
    assert final[-1]["event"] == "EXPIRED"
    assert final[-1]["stage_index"] is None
    assert poll_compressed_shadow_chase(state, now_ts=1800, last=100_300) == []


def test_receipts_keep_independent_identity_dimensions():
    state, first = _armed()
    second = poll_compressed_shadow_chase(state, now_ts=1060, last=100_100)[0]
    keys = (
        "shared_ai_call_id", "opportunity_id", "episode_id", "policy_id",
        "policy_signature", "epoch_id", "event_id", "requested_qty",
        "entry_fee_rate", "exit_fee_rate", "slippage_model",
    )
    assert all(first[key] is not None and first[key] != "" for key in keys)
    assert {key: first[key] for key in keys} == {key: second[key] for key in keys}


def test_missing_identity_is_not_fabricated_and_is_ineligible():
    state, row = arm_compressed_shadow_chase(
        trade_id="trade-only", direction="LONG", signal_price=100,
        signal_ts=1000, initial_limit_price=99.9, shared_ai_call_id="",
        opportunity_id="", episode_id="", epoch_id="",
    )
    assert row["identity_complete"] is False
    assert set(row["missing_identity_fields"]) == {
        "shared_ai_call_id", "opportunity_id", "episode_id", "epoch_id",
    }
    assert row["shared_ai_call_id"] == ""
    assert row["opportunity_id"] == ""
    assert row["eligible_at_stage"] is False
    assert state["trade_id"] == "trade-only"


def test_restart_recovery_does_not_duplicate_stages_or_terminal():
    state, first = _armed()
    stage1 = poll_compressed_shadow_chase(
        state, now_ts=1060, last=100100, bid=100090, ask=100110,
        bbo_fresh=True, direction_revalidation_result="VALID",
        direction_revalidation_reason="SAME_LONG_DIRECTION",
    )[0]
    recovered = recover_compressed_shadow_states([first, stage1], now_ts=1100)["cont-test"]
    rows = poll_compressed_shadow_chase(
        recovered, now_ts=1120, last=100120, bid=100110, ask=100130,
        bbo_fresh=True, direction_revalidation_result="VALID",
        direction_revalidation_reason="SAME_LONG_DIRECTION",
    )
    assert [row["stage_index"] for row in rows] == [2]
    final_rows = poll_compressed_shadow_chase(recovered, now_ts=1780, last=100200)
    assert [row["event"] for row in final_rows].count("EXPIRED") == 1
    terminal = final_rows[-1]
    assert recover_compressed_shadow_states([first, stage1, terminal], now_ts=1800) == {}


def test_canonical_bot_signal_engine_and_manifest_are_in_parity():
    root = Path(__file__).resolve().parents[2]
    bot = (root / "services/btc-conservative-agent/bot.py").read_text(encoding="utf-8")
    engine = (root / "services/btc-signal-engine/engine.py").read_text(encoding="utf-8")
    assert bot.replace("\r\n", "\n") == engine.replace("\r\n", "\n")
    manifest = json.loads(
        (root / "services/btc-signal-engine/manifest.json").read_text(encoding="utf-8")
    )
    expected = hashlib.sha256(bot.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]
    assert manifest["source"] == "services/btc-conservative-agent/bot.py"
    assert manifest["signal_hash"] == expected


def test_runtime_owns_one_shadow_schedule_per_shared_ai_call():
    root = Path(__file__).resolve().parents[2]
    source = (root / "services/btc-conservative-agent/bot.py").read_text(
        encoding="utf-8"
    )
    assert source.count("_arm_shared_compressed_shadow_chase(") == 2
    helper = source.split("def _arm_shared_compressed_shadow_chase", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "if not ai_decision_should_execute(ai):" in helper
    assert "with _compressed_shadow_lock:" in helper
    assert "call_id in _compressed_shadow_seen_call_ids" in helper
    assert 'places_order=False' not in helper  # module owns immutable isolation flags
    process = source.split("def process_signal", 1)[1]
    arm_at = process.index("_arm_shared_compressed_shadow_chase(ctx, ai)")
    family_at = process.index("spawn_combo_lanes_from_ai_scan(")
    continuous_at = process.index("spawn_continuous_lane_from_ai_scan(")
    assert arm_at < family_at < continuous_at
