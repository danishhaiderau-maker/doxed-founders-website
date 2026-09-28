import json

from research.quantity_execution import build_signed_quantity_constraints
from research_v3_bridge import dual_write_paper_order_intent
from research_v3_store import V3EvidenceStore


def test_paper_order_intent_preserves_quantity_receipt_in_execution_basis(tmp_path):
    constraints = build_signed_quantity_constraints(
        symbol="tBTCF0:USTF0", quantity_step="0.00000001",
        quantity_precision=8, min_lot="0.0001", min_notional="5",
        captured_at="2026-08-30T03:00:00+00:00",
        source_revision="b" * 40, source="CCXT_BITFINEX_MARKET_METADATA:BTCF0:USTF0",
    )
    status = {"supported": True, "receipt": constraints, "reasons": []}
    dual_write_paper_order_intent(
        {
            "trade_id": "family-quantity-1", "created_ts": 1001,
            "signal_dir": "LONG", "limit_price": 80000, "qty": 0.0002,
            "research_lane": "FAMILY_TEST", "paper_only": True,
            "market_microstructure_symbol": "tBTCF0:USTF0",
            "signed_quantity_constraints": constraints,
            "quantity_constraints_status": status,
        },
        {
            "trade_id": "family-quantity-1", "shared_ai_call_id": "scan-quantity-1",
            "created_ts_ts": 1000, "symbol": "tBTCF0:USTF0",
            "raw_direction": "LONG", "research_lane": "FAMILY_TEST",
            "policy_id": "TEST", "paper_only": True,
        },
        epoch_id="epoch-v3-test", data_dir=str(tmp_path),
    )
    rows = [json.loads(line) for line in V3EvidenceStore(
        str(tmp_path), epoch_id="epoch-v3-test"
    ).ledger_path("order_intent").read_text(encoding="utf-8").splitlines()]
    basis = rows[-1]["execution_basis"]
    assert basis["requested_qty"] == 0.0002
    assert basis["requested_qty_provenance"] == "SOURCE_TICKET_QTY"
    assert basis["market_microstructure_symbol"] == "tBTCF0:USTF0"
    assert basis["signed_quantity_constraints"] == constraints
    assert basis["quantity_constraints_status"] == status


def test_paper_order_intent_marks_missing_constraints_unsupported(tmp_path):
    dual_write_paper_order_intent(
        {"trade_id": "family-quantity-2", "created_ts": 1001, "qty": 0.0002,
         "limit_price": 80000, "research_lane": "FAMILY_TEST", "paper_only": True},
        {"trade_id": "family-quantity-2", "shared_ai_call_id": "scan-quantity-2",
         "created_ts_ts": 1000, "symbol": "tBTCF0:USTF0", "raw_direction": "LONG",
         "research_lane": "FAMILY_TEST", "policy_id": "TEST", "paper_only": True},
        epoch_id="epoch-v3-test", data_dir=str(tmp_path),
    )
    rows = [json.loads(line) for line in V3EvidenceStore(
        str(tmp_path), epoch_id="epoch-v3-test"
    ).ledger_path("order_intent").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["execution_basis"]["quantity_constraints_status"] == {
        "supported": False, "receipt": None,
        "reasons": ["VENUE_QUANTITY_CONSTRAINTS_UNAVAILABLE"],
    }
