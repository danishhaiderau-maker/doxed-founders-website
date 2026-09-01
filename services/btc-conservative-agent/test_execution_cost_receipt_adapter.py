from execution_cost_receipt_adapter import build_measured_execution_cost_receipt


def source(**overrides):
    value = {
        "entry_fee_usd": 0.1, "exit_fee_usd": 0.2,
        "entry_slippage_usd": 0.03, "exit_slippage_usd": 0.04,
        "canonical_economics": {
            "gross_pnl_usd": 2.0, "trading_fees_usd": 0.3,
            "funding_fees_usd": 0.1, "latency_cost_usd": 0.02,
            "net_pnl_usd": 1.6,
        },
    }
    value.update(overrides)
    return value


def test_maps_and_signs_authoritative_cost_fields_deterministically():
    first = build_measured_execution_cost_receipt(source(), source_receipt_ids=["life-1", "exec-1"])
    second = build_measured_execution_cost_receipt(source(), source_receipt_ids=["exec-1", "life-1"])
    assert first == second
    assert first["status"] == "MEASURED"
    assert first["slippage_usd"] == 0.07
    assert len(first["receipt_sha256"]) == 64


def test_explicit_measured_zero_is_not_missing():
    zero = source(entry_fee_usd=0, exit_fee_usd=0, entry_slippage_usd=0, exit_slippage_usd=0,
                  canonical_economics={"gross_pnl_usd": 0, "trading_fees_usd": 0,
                    "funding_fees_usd": 0, "latency_cost_usd": 0, "net_pnl_usd": 0})
    receipt = build_measured_execution_cost_receipt(zero, source_receipt_ids=["zero"])
    assert receipt["status"] == "MEASURED"
    assert "entry_fee_usd" in receipt["explicit_measured_zero_fields"]


def test_missing_measurement_stays_unknown():
    incomplete = source()
    incomplete.pop("exit_slippage_usd")
    receipt = build_measured_execution_cost_receipt(incomplete, source_receipt_ids=["exec"])
    assert receipt["status"] == "UNKNOWN"
    assert "UNKNOWN_EXIT_SLIPPAGE_USD_MISSING" in receipt["blockers"]


def test_fee_and_candidate_net_reconciliation_mismatch_stays_unknown():
    receipt = build_measured_execution_cost_receipt(
        source(exit_fee_usd=0.25), source_receipt_ids=["exec"], expected_net_pnl_usd=9,
    )
    assert receipt["status"] == "UNKNOWN"
    assert "UNKNOWN_TRADING_FEE_RECONCILIATION_MISMATCH" in receipt["blockers"]
    assert "UNKNOWN_CANDIDATE_NET_PNL_MISMATCH" in receipt["blockers"]
