import json
from pathlib import Path

import lifecycle_pipeline
import research_v3_store
from lifecycle_bundles import verify_bundle
from research_v3_bridge import (
    dual_write_lifecycle_qualification_horizon,
    dual_write_paper_close,
    dual_write_paper_fill,
    dual_write_terminal_paper_schedule,
)


PROVENANCE = {
    "source_revision": "a" * 40,
    "deployed_revision": "b" * 40,
    "tile_config_signature": "c" * 64,
    "config_signature": "d" * 64,
}


def _write_tape(path: Path, start: int, end: int) -> None:
    rows = []
    for ts in range(start, end + 1):
        row = {
            "schema": "market_microstructure_1s_v1",
            "symbol": "tBTCF0:USTF0",
            "bucket_ts": ts,
            "last": 100.0 + (ts - start),
            "bid": 99.9,
            "ask": 100.1,
            "bid_qty": 2.0,
            "ask_qty": 3.0,
        }
        rows.append(json.dumps(row, separators=(",", ":"), sort_keys=True))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _sources():
    signal = {
        "trade_id": "bridge-e2e-fill",
        "episode_id": "episode-bridge-e2e",
        "created_ts_ts": 100.0,
        "raw_direction": "LONG",
        "final_direction": "LONG",
        "shared_ai_call_id": "scan-bridge-e2e",
        "symbol": "tBTCF0:USTF0",
        "research_lane": "CONTINUOUS",
        "policy_id": "CONTINUOUS",
        **PROVENANCE,
    }
    schedule = {
        "schema": "research_chase_schedule_v1",
        "authoritative": True,
        "intervals": [
            {
                "bucket_id": "b0",
                "start_ts": 100.0,
                "end_ts": 101.0,
                "limit_price": 100.0,
            }
        ],
        "terminal_ts": 101.0,
        "terminal_ts_exact": 101.0,
        "terminal_reason": "FILLED",
    }
    order = {
        **signal,
        "status": "FILLED",
        "qty": 0.2,
        "requested_qty": 0.2,
        "limit_price": 100.0,
        "research_chase_schedule": schedule,
        "chase_schedule_authoritative": True,
    }
    position = {**order, "entry_ts": 101.0, "entry": 100.0, "dir": "LONG"}
    outcome = {
        "trade_id": signal["trade_id"],
        "close_ts": 103.0,
        "exit": 102.0,
        "gross_pnl_usd": 1.0,
        "trading_fees_usd": 0.1,
        "funding_fees_usd": 0.05,
        "entry_slippage_cost_usd": 0.01,
        "exit_slippage_cost_usd": 0.02,
        "latency_cost_usd": 0.02,
        "net_pnl_usd": 0.85,
        "exit_reason": "ATR_TP_2_5",
    }
    return signal, order, position, outcome


def _patch_provenance(monkeypatch):
    monkeypatch.setattr(
        lifecycle_pipeline, "_collection_provenance", lambda: dict(PROVENANCE)
    )
    monkeypatch.setattr(
        research_v3_store, "_collection_provenance", lambda: dict(PROVENANCE)
    )


def test_bridge_lifecycle_progresses_transfer_then_qualification(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    monkeypatch.setattr(lifecycle_pipeline, "QUALIFICATION_HORIZON_SEC", 4.0)
    _write_tape(tmp_path / "market_microstructure_1s.jsonl", 100, 107)
    signal, order, position, outcome = _sources()

    dual_write_terminal_paper_schedule(
        order, signal, epoch_id="epoch-bridge-e2e", data_dir=tmp_path,
        lifecycle_final=True,
    )
    dual_write_paper_fill(
        order, signal, position, epoch_id="epoch-bridge-e2e", data_dir=tmp_path,
    )
    dual_write_paper_close(
        position, signal, outcome, epoch_id="epoch-bridge-e2e", data_dir=tmp_path,
    )

    for _attempt in range(2 * len(lifecycle_pipeline.LEDGER_NAMES)):
        early = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
            tmp_path, now=104.0
        )
        if early["transfer_bundle_count"] == 1:
            break
    assert early["transfer_bundle_count"] == 1
    transfer = early["results"][0]["transfer_bundle"]
    assert transfer["manifest"]["maturity"] == "TRANSFER_READY"
    assert transfer["manifest"]["qualification_ready"] is False
    assert transfer["manifest"]["ranking_eligible"] is False
    assert transfer["manifest"]["source_cleanup_authorized"] is False
    assert verify_bundle(transfer["path"])["passed"] is True

    dual_write_lifecycle_qualification_horizon(
        position,
        signal,
        outcome,
        entry_outcome="FULL_FILL",
        epoch_id="epoch-bridge-e2e",
        data_dir=tmp_path,
        lifecycle_horizon_sec=4.0,
    )

    for _attempt in range(2 * len(lifecycle_pipeline.LEDGER_NAMES)):
        completion = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
            tmp_path, now=300.0
        )
        if completion["completion_appended_count"] == 1:
            break
    assert completion["completion_appended_count"] == 1
    assert completion["bundle_count"] == 0
    assert completion["results"][0]["stage"] == "COMPLETION_PENDING_REINDEX"

    for _attempt in range(2 * len(lifecycle_pipeline.LEDGER_NAMES)):
        qualified = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
            tmp_path, now=300.0
        )
        if qualified["bundle_count"] == 1:
            break
    assert qualified["bundle_count"] == 1, json.dumps(qualified, indent=2)
    assert qualified["results"][0]["stage"] == "BUNDLE_MATERIALIZED_OR_VERIFIED"
    bundle = qualified["results"][0]["bundle"]
    assert bundle["manifest"]["schema"] == "research_lifecycle_bundle_v1"
    assert bundle["manifest"]["completion"]["ready"] is True
    assert verify_bundle(bundle["path"])["passed"] is True

    # The early transfer snapshot is immutable and never upgraded into a
    # qualification, ranking, profitability, or cleanup authority.
    repeated = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=301.0
    )
    assert repeated["candidate_count"] == 0
    assert transfer["manifest"]["qualification_ready"] is False
    assert transfer["manifest"]["ranking_eligible"] is False
    assert transfer["manifest"]["source_cleanup_authorized"] is False
