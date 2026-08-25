import json
from unittest import mock

import execution_funnel


def test_order_funnel_reports_effective_source_ttl(tmp_path):
    output = tmp_path / "execution_funnel.jsonl"
    with (
        mock.patch.object(execution_funnel, "FUNNEL_FILE", str(output)),
        mock.patch.object(execution_funnel.time, "time", return_value=1_000.0),
        mock.patch.object(execution_funnel, "state_price", return_value=63_000.0),
        mock.patch.dict(execution_funnel.os.environ, {"LIMIT_ORDER_MAX_AGE_SEC": "7200"}),
    ):
        execution_funnel.funnel_on_order(
            {"trade_id": "cont-ttl", "expires_ts": 2_500.0},
            {"trade_id": "cont-ttl", "created_ts": 1_000.0, "limit_price": 63_100.0},
        )

    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["ttl_sec"] == 1_500
    assert row["effective_ttl_sec"] == 1_500
    assert row["effective_expires_ts"] == 2_500.0
    assert row["source_signal_expires_ts"] == 2_500.0
    assert row["order_max_age_sec"] == 7_200


def test_order_funnel_falls_back_to_order_age_ceiling(tmp_path):
    output = tmp_path / "execution_funnel.jsonl"
    with (
        mock.patch.object(execution_funnel, "FUNNEL_FILE", str(output)),
        mock.patch.object(execution_funnel.time, "time", return_value=1_000.0),
        mock.patch.object(execution_funnel, "state_price", return_value=63_000.0),
        mock.patch.dict(execution_funnel.os.environ, {"LIMIT_ORDER_MAX_AGE_SEC": "7200"}),
    ):
        execution_funnel.funnel_on_order(
            {"trade_id": "cont-order-age"},
            {"trade_id": "cont-order-age", "created_ts": 1_000.0, "limit_price": 63_100.0},
        )

    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["ttl_sec"] == 7_200
    assert row["source_signal_expires_ts"] is None


def test_close_funnel_is_idempotent_in_process_and_after_restart(tmp_path):
    output = tmp_path / "execution_funnel.jsonl"
    with mock.patch.object(execution_funnel, "FUNNEL_FILE", str(output)):
        execution_funnel._closed_ids_by_path.pop(str(output.resolve()), None)
        execution_funnel.funnel_on_close("trade-closed-once", "ATR TP 2.5X", 1.25, 60)
        execution_funnel.funnel_on_close("trade-closed-once", "ATR TP 2.5X", 1.25, 60)

        # Simulate a fresh process: the durable guard must recover the prior
        # terminal identity from disk rather than trusting only memory.
        execution_funnel._closed_ids_by_path.pop(str(output.resolve()), None)
        execution_funnel.funnel_on_close("trade-closed-once", "ATR TP 2.5X", 1.25, 60)

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["stage"] == "CLOSED"
    assert rows[0]["trade_id"] == "trade-closed-once"
