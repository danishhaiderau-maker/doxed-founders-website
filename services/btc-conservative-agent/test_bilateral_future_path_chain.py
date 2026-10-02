"""Synthetic collection-to-replay acceptance; never exchange or cost evidence."""
import json
import hashlib
from pathlib import Path
import subprocess
import sys

from research_v3_store import V3EvidenceStore
from research.entry_baseline_replay import materialize_v3_opportunity_replay


def test_rejected_bilateral_producer_matures_shared_tape_without_invented_execution(tmp_path):
    epoch, signal = "epoch-bilateral-test", 1700000000
    store = V3EvidenceStore(tmp_path, epoch_id=epoch)
    store.append("opportunity", {"record_id":"opportunity:bilateral", "episode_id":"episode:bilateral",
        "signal_ts":signal, "symbol":"BTCUSD", "signal_price":100,
        "raw_direction":"NO_TRADE", "direction":"NO_TRADE", "shared_ai_call_id":"call:bilateral",
        "signal_time_bbo":{"bid":99,"ask":101}})
    store.append("decision", {"record_id":"decision:bilateral", "episode_id":"episode:bilateral",
        "event_id":"event:bilateral", "primary_outcome":"REJECTED"})
    ledger = tmp_path / "v3" / "ledgers"
    opportunity = json.loads((ledger / "opportunity.jsonl").read_text().splitlines()[0])
    sides = opportunity["baseline_schedule_snapshot"]["directional_schedules"]
    assert set(sides) == {"LONG", "SHORT"}
    assert sides["LONG"]["capture_signature"] != sides["SHORT"]["capture_signature"]
    assert all(s["original_ai_direction"] == "NO_TRADE" and s["schedules"] for s in sides.values())
    # Complete chronological price tape deliberately lacks depth/trade evidence.
    tape = tmp_path / "market_microstructure_1s.jsonl"
    tape.write_text("".join(json.dumps({"bucket_ts":signal+i,"last":100,"bid":99,"ask":101})+"\n"
                            for i in range(-60,7201)), encoding="utf-8")
    result = tmp_path / "v3" / "receipts" / ("future-path-worker-" + "a"*32 + ".json")
    completed = subprocess.run([sys.executable, str(Path(__file__).with_name("research_v3_future_paths_worker.py")),
        "--data-dir",str(tmp_path),"--epoch-id",epoch,"--now-ts",str(signal+7300),
        "--max-batch","64","--result",str(result)], capture_output=True, timeout=30)
    assert completed.returncode == 0
    receipt = json.loads(result.read_text())
    assert receipt["complete_count"] == 1 and receipt["unknown_count"] == 0
    assert receipt["terminal_append_dispositions"] == {"written":1,"duplicate":0,"deferred":0,"blocked":0,"unknown":0}
    rows = [json.loads(line) for line in (ledger / "market_segment.jsonl").read_text().splitlines()]
    terminal = next(r for r in rows if r.get("segment_role") == "SIGNAL_TO_120M_FUTURE_PATH")
    assert terminal["decision_outcome"] == "REJECTED"
    assert terminal["future_path_status"] == "COMPLETE"
    assert terminal["segment_ref"]["sha256"]
    digest = terminal["segment_ref"]["sha256"]
    shared = tmp_path / "v3" / "market_segments" / digest[:2] / (digest + ".json")
    assert hashlib.sha256(shared.read_bytes()).hexdigest() == digest
    assert terminal["opportunity_id"] == opportunity["record_id"]
    assert all(side["opportunity_id"] == terminal["opportunity_id"] for side in sides.values())
    assert terminal["coverage"]["conservative_bbo_depth_eligible"] is False
    assert terminal["pre_entry_capture_status"] == "UNKNOWN"
    report = materialize_v3_opportunity_replay(tmp_path)
    assert report["same_opportunity_count"] == 1
    assert {r["direction"] for r in report["episode_receipts"]} == {"LONG","SHORT"}
    assert all(r["outcome_state"] == "UNKNOWN" for ep in report["episode_receipts"] for r in ep["results"])
    assert not (ledger / "execution.jsonl").exists()
