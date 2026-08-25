import ast
from pathlib import Path

from combo_pathway_config import COMBO_EXECUTION_LANES, COMBO_LANE_SPECS
from experimental_tile_contract import effective_route, relay_event_blockers


def test_tile_and_relay_are_independent_gates():
    assert not effective_route(tile_enabled=False, relay_armed=True, relay_eligible=True)["paper_order_eligible"]
    paper = effective_route(tile_enabled=True, relay_armed=False, relay_eligible=True)
    assert paper["paper_order_eligible"] and not paper["bitfinex_copy_eligible"]
    copied = effective_route(tile_enabled=True, relay_armed=True, relay_eligible=True)
    assert copied["paper_order_eligible"] and copied["bitfinex_copy_eligible"]
    assert not copied["direct_exchange_submit"]


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


def test_only_patient_chase_is_registered_as_candidate():
    assert tuple(COMBO_EXECUTION_LANES) == ("OFFSET_029_ATR_TP_25",)
    assert set(COMBO_LANE_SPECS) == {"OFFSET_029_ATR_TP_25"}


def test_dashboard_margin_display_preserves_quarter_dollar_precision():
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "entry.get('margin_usd', shared['margin_usd']):.2f" in source
    assert "entry.get('margin_usd', shared['margin_usd']):.0f" not in source
