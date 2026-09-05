import copy
import hashlib
import json

import pytest

from research_v3_bridge import _paper_atr14_pct_3m, _paper_fill_atr_evidence, dual_write_paper_fill
from research_v3_candidates import _candidate_atr_projection
from research_v3_contract import canonical_json
from research_v3_store import V3EvidenceStore


def signed_observation(**changes):
    value = {"schema": "paper_fill_atr_observation_v1", "event_id": "trade-1",
             "atr_basis": "EXPLICIT_AT_FILL_OBSERVATION", "timeframe_sec": 180, "period": 14,
             "provenance": "closed-3m-candles-captured-at-fill", "source_candles_sha256": "a" * 64,
             "observed_ts": 1000, "available_at_ts": 1000, "last_closed_candle_ts": 990,
             "atr_pct": .083}
    value.update(changes)
    value["receipt_sha256"] = hashlib.sha256(canonical_json(value).encode()).hexdigest()
    return value


def evidence(position=None, order=None):
    return _paper_fill_atr_evidence(position or {}, order or {}, fill_ts=1000, event_id="trade-1")


@pytest.mark.parametrize("source,path", [
    ({"atr14_pct_at_fill": .083}, "atr14_pct_at_fill"),
    ({"atr14_pct_3m": .083}, "atr14_pct_3m"),
    ({"atr14_pct": .083}, "atr14_pct"),
    ({"cycle_3m_universe": {"atr14_pct_3m": .083}}, "cycle_3m_universe.atr14_pct_3m"),
    ({"context": {"cycle_3m_universe": {"atr14_pct_3m": .083}}}, "context.cycle_3m_universe.atr14_pct_3m"),
    ({"ai_input": {"cycle_3m_universe": {"atr14_pct_3m": .083}}}, "ai_input.cycle_3m_universe.atr14_pct_3m"),
])
def test_fallback_never_observed_fill(source, path):
    for position, order, label in ((source, {}, "position"), ({}, source, "order")):
        receipt = evidence(position, order)
        assert receipt["atr14_pct_at_fill"] == .083
        assert receipt["atr14_pct_basis"] == "UNVERIFIED_TIMING_FALLBACK"
        assert receipt["atr14_pct_source"] == f"{label}.{path}"
        assert receipt["atr14_fill_observation_verified"] is False


def test_numeric_precedence_unchanged():
    assert _paper_atr14_pct_3m({"atr14_pct_at_fill": .1, "atr14_pct_3m": .2}, {"atr14_pct_at_fill": .3}) == .1
    assert _paper_atr14_pct_3m({"atr14_pct_3m": .2}, {"atr14_pct_at_fill": .3}) == .2


@pytest.mark.parametrize("value", [float("inf"), float("nan"), float("-inf"), True, 10 ** 1000, -1, 0])
def test_invalid_numeric_falls_through_to_valid_source(value):
    assert _paper_atr14_pct_3m({"atr14_pct_at_fill": value}, {"atr14_pct_3m": .2}) == .2
    assert _candidate_atr_projection({"atr14_pct_at_fill": value}, {"atr14_pct_at_signal": .2}, {}, None)["atr14_pct"] == .2


@pytest.mark.parametrize("changes", [
    {"observed_ts": 999}, {"observed_ts": 1001}, {"available_at_ts": "1000.000000000000000001"},
    {"last_closed_candle_ts": 820}, {"last_closed_candle_ts": 1001}, {"available_at_ts": 980},
    {"observed_ts": float("inf")}, {"provenance": ""}, {"event_id": "other"},
    {"source_candles_sha256": None}, {"atr_pct": .1}, {"atr_pct": "0.083000000000000001"},
    {"atr_basis": "SIGNAL_TIME_3M_ATR14"},
])
def test_stale_future_mismatched_receipts_not_observed(changes):
    receipt = evidence({"atr14_pct_at_fill": .083, "atr14_fill_observation": signed_observation(**changes)})
    assert receipt["atr14_pct_at_fill"] == .083
    assert receipt["atr14_fill_observation_verified"] is False
    assert receipt["atr14_pct_basis"] == "UNVERIFIED_TIMING_FALLBACK"
    assert receipt["atr14_provenance_blockers"]


def test_explicit_exact_observation_and_candidate_projection():
    receipt = evidence({"atr14_pct_at_fill": .083, "atr14_fill_observation": signed_observation()})
    assert receipt["atr14_fill_observation_verified"] is True
    assert receipt["atr14_pct_basis"] == "FILL_TIME_3M_ATR14"
    projection = _candidate_atr_projection({**receipt, "fill_ts": 1000, "event_id": "trade-1"}, {}, {}, None)
    assert projection["atr14_fill_observation_verified"] is True
    assert projection["atr14_pct_basis"] == "FILL_TIME_3M_ATR14"


def test_old_label_or_number_is_not_repromoted_by_candidates():
    for source in ({"atr14_pct_at_fill": .083}, {"atr14_pct_at_fill": .083, "atr14_pct_basis": "FILL_TIME_3M_ATR14"}):
        assert _candidate_atr_projection(source, {}, {}, None)["atr14_pct_basis"] == "UNVERIFIED_TIMING_FALLBACK"
    assert _candidate_atr_projection({}, {"atr14_pct_at_signal": .1, "atr14_pct_basis": "SIGNAL_TIME_3M_ATR14"}, {}, None)["atr14_pct_basis"] == "SIGNAL_TIME_3M_ATR14"


def test_tampered_observation_hash_fails_and_raw_sources_unchanged(tmp_path):
    position = {"trade_id": "trade-1", "entry_ts": 1000, "entry": 100, "qty": 1,
                "atr14_pct_at_fill": .083, "atr14_fill_observation": signed_observation()}
    position["atr14_fill_observation"]["provenance"] = "tampered"
    before = copy.deepcopy(position)
    dual_write_paper_fill({"trade_id": "trade-1"}, {"trade_id": "trade-1", "shared_ai_call_id": "scan-1"}, position,
                         epoch_id="epoch-1", data_dir=str(tmp_path))
    assert position == before
    store = V3EvidenceStore(str(tmp_path), epoch_id="epoch-1")
    row = json.loads(store.ledger_path("execution").read_text().strip())
    assert row["atr14_pct_at_fill"] == .083
    assert row["atr14_pct_basis"] == "UNVERIFIED_TIMING_FALLBACK"
    assert "FILL_TIME_ATR_RECEIPT_HASH_INVALID" in row["atr14_provenance_blockers"]


def test_append_exact_measured_receipt_is_projected_without_history_rewrite(tmp_path):
    position = {"trade_id": "trade-1", "entry_ts": 1000, "entry": 100, "qty": 1,
                "atr14_pct_at_fill": .083, "atr14_fill_observation": signed_observation()}
    signal = {"trade_id": "trade-1", "shared_ai_call_id": "scan-1"}
    dual_write_paper_fill({"trade_id": "trade-1"}, signal, position, epoch_id="epoch-1", data_dir=str(tmp_path))
    store = V3EvidenceStore(str(tmp_path), epoch_id="epoch-1")
    before = store.ledger_path("execution").read_bytes()
    row = json.loads(before)
    assert row["atr14_pct_basis"] == "FILL_TIME_3M_ATR14"
    projected = _candidate_atr_projection(row, {}, {}, None)
    assert projected["atr14_fill_observation_verified"] is True
    assert store.ledger_path("execution").read_bytes() == before
