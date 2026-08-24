import ast
from pathlib import Path

from combo_pathway_config import COMBO_EXECUTION_LANES, COMBO_LANE_SPECS
from experimental_tile_contract import effective_route, relay_event_blockers
from paper_policy_offset029_regime import account_risk_quantity, transition


def test_tile_and_relay_are_independent_gates():
    assert not effective_route(tile_enabled=False, relay_armed=True, relay_eligible=True)["paper_order_eligible"]
    paper = effective_route(tile_enabled=True, relay_armed=False, relay_eligible=True)
    assert paper["paper_order_eligible"] and not paper["bitfinex_copy_eligible"]
    copied = effective_route(tile_enabled=True, relay_armed=True, relay_eligible=True)
    assert copied["paper_order_eligible"] and copied["bitfinex_copy_eligible"]
    assert not copied["direct_exchange_submit"]


def test_regime_transition_never_widens_stop():
    result = transition(previous_regime="SIDEWAYS", observed_regime="STRONG_TREND",
                        current_stop_distance_atr=.75)
    assert result["applied_stop_atr"] == .75
    assert result["risk_widened"] is False


def test_position_size_obeys_margin_and_account_risk_caps():
    result = account_risk_quantity(equity_usd=500, account_risk_pct=.5,
                                   entry_price=75000, atr_abs=500,
                                   leverage=100, margin_cap_usd=2)
    assert result["quantity"] <= 200 / 75000
    assert result["quantity"] * 500 <= 2.5


def test_relay_event_requires_full_identity_and_supported_operation():
    event = {
        "research_lane": "LANE", "raw_policy_id": "RAW", "policy_id": "POLICY",
        "trade_id": "trade", "shared_ai_call_id": "scan", "epoch_id": "epoch",
        "operation": "PARTIAL_CLOSE",
    }
    assert relay_event_blockers(event, supported_operations=("ORDER_PLACED",)) == [
        "UNSUPPORTED_RELAY_OPERATION_PARTIAL_CLOSE"
    ]
    event.pop("epoch_id")
    assert "MISSING_EPOCH_ID" in relay_event_blockers(event, supported_operations=("PARTIAL_CLOSE",))


def test_v31_all_active_tiles_use_approved_quarter_dollar_margin_cap():
    assert COMBO_EXECUTION_LANES
    assert {COMBO_LANE_SPECS[lane]["margin_usd"] for lane in COMBO_EXECUTION_LANES} == {0.25}

    # Verify the exact production module without importing its process-start
    # side effects. This prevents a dashboard-only/config-only sizing change.
    module = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    fixed_margin = next(
        node.value.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "FIXED_MARGIN_USDT" for target in node.targets)
    )
    assert fixed_margin == 0.25
