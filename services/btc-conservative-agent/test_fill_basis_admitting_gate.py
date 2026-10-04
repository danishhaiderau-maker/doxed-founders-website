"""A filled paper order's receipt always carries the fill_basis that admitted it.

state_monitor_loop and position_manager both run process_pending_orders. A
taker order admitted as MARKETABLE_AT_PLACEMENT by one thread was re-checked by
the other (placement_check False, no print yet), which overwrote
``venue_fill_gate`` with ``fill_basis: None`` before the fill receipt read it:
cft-cd29, ntt-9a31, gs1-7840 and pmr-9149 had null fill_basis in freeze21b.
"""
import research_v3_bridge as bridge

ADMIT = {"policy": "VENUE_EXECUTABLE_SHOWCASE_FILL_GATE_V2", "limit_price": 85170.0, "limit_generation": 0,
         "fill_basis": "MARKETABLE_AT_PLACEMENT", "fill_model": "REALISTIC_V1", "reason": "EXECUTABLE",
         "fill_id": "fill:abc", "best_bid": 85160.0, "best_ask": 85170.0}
RECHECK = {**ADMIT, "fill_basis": None, "reason": "INSUFFICIENT_EXECUTABLE_DEPTH", "fill_id": None}


def _receipt(order):
    return bridge._paper_fill_execution_receipt(order, {"qty": 0.0003}, {})


def test_concurrent_recheck_cannot_erase_the_admitting_basis():
    order = {"venue_fill_gate": dict(RECHECK), "venue_fill_gate_admitted": dict(ADMIT), "limit_price": 85170.0}
    receipt = _receipt(order)
    assert receipt["fill_basis"] == "MARKETABLE_AT_PLACEMENT"
    assert receipt["fill_id"] == "fill:abc"


def test_admitted_evidence_from_an_older_generation_is_not_reused():
    newer = {**RECHECK, "limit_generation": 1, "limit_price": 85180.0}
    order = {"venue_fill_gate": newer, "venue_fill_gate_admitted": dict(ADMIT)}
    assert _receipt(order)["fill_basis"] == bridge.FILL_BASIS_UNCLASSIFIED


def test_latest_gate_with_a_basis_wins_and_null_is_never_written():
    trade_through = {**ADMIT, "fill_basis": "TRADE_THROUGH"}
    assert _receipt({"venue_fill_gate": trade_through, "venue_fill_gate_admitted": dict(ADMIT)})[
        "fill_basis"] == "TRADE_THROUGH"
    assert _receipt({})["fill_basis"] == "UNCLASSIFIED"


def test_both_gate_sites_keep_the_admitting_evidence():
    import pathlib
    source = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    start = source.index("def _pending_limit_ready_for_fill(")
    helper = source[start:source.index("FILL_DIRECTION_REVALIDATE_AFTER_SEC = ", start)]
    assert helper.count('order["venue_fill_gate"] = evidence') == 2
    assert helper.count('order["venue_fill_gate_admitted"] = dict(evidence)') == 2
