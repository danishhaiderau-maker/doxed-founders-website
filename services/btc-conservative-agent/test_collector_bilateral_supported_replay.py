"""Synthetic prospective model: collector buckets to normal bilateral replay."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from microstructure_tape import build_bucket
from research_v3_store import V3EvidenceStore
from research.policy_evidence_schema import generation_identity
from research.entry_baseline_replay import materialize_v3_opportunity_replay
from test_declared_directional_context_integration import dataset, market_entries, sha


@pytest.mark.parametrize("defect", [None, "missing", "late"])
@pytest.mark.parametrize("ai_direction,verdict", [
    ("NO_TRADE", "REJECTED"), ("LONG", "APPROVED"),
    ("SHORT", "APPROVED"), ("LONG", "REJECTED"),
])
def test_actual_buckets_producer_worker_to_declared_bilateral_context(tmp_path, monkeypatch, defect, ai_direction, verdict):
    import research_v3_store as store_module
    revision = "a" * 40
    monkeypatch.setenv("SOURCE_GIT_REV", revision)
    monkeypatch.setattr(store_module, "_provenance_cache", None)
    # Reuse only an explicitly synthetic pre-signal declaration, not evidence rows.
    template = tmp_path / "template"
    dataset(template)
    opportunity = json.loads((template/"v3/ledgers/opportunity.jsonl").read_text())
    opportunity.pop("baseline_schedule_snapshot")
    opportunity.update(source_revision=revision, deployed_revision=revision)
    from research.quantity_execution import build_signed_quantity_constraints
    opportunity["research_baseline_context_declaration"]["signed_quantity_constraints"] = build_signed_quantity_constraints(
        symbol="BTC",quantity_step="0.1",quantity_precision=1,min_lot="0.1",min_notional="1",
        captured_at="1970-01-01T00:01:39Z",source_revision=revision,source="SYNTHETIC_TEST")
    opportunity.update(direction=ai_direction, raw_direction=ai_direction)
    if defect == "missing": opportunity.pop("research_baseline_context_declaration")
    if defect == "late": opportunity["research_baseline_context_declaration"]["declared_at_ts"] = 101
    root = tmp_path / "actual"
    store = V3EvidenceStore(root, epoch_id="epoch-1")
    assert store.append("opportunity", opportunity)["written"]
    collected = json.loads((root/"v3/ledgers/opportunity.jsonl").read_text())
    assert store.append("decision", {"record_id":"decision:1", "episode_id":"ep-1",
        "event_id":"event-1", "primary_outcome":verdict})["written"]
    rows = [build_bucket(bucket_ts=ts, bid=99 if ts<=100 else 105,
        ask=101 if ts<=100 else 105.1, bid_qty=.4 if ts==100 else 10,
        ask_qty=.4 if ts==100 else 10,last=100 if ts<=100 else 105,
        source_ts=ts,trades=(),symbol="BTC") for ts in range(40,7301)]
    (root/"market_microstructure_1s.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
    result = root/"v3/receipts/future-path-worker-test.json"
    run = subprocess.run([sys.executable,str(Path(__file__).with_name("research_v3_future_paths_worker.py")),
        "--data-dir",str(root),"--epoch-id","epoch-1","--now-ts","7400","--max-batch","64","--result",str(result)],
        capture_output=True,timeout=30)
    assert run.returncode == 0
    assert json.loads(result.read_text())["complete_count"] == 1
    # Synthetic canonical pinning of the actual produced immutable evidence.
    state = {}
    for path in sorted((root/"v3").rglob("*.json*")):
        if "receipts" in path.parts: continue
        raw = path.read_bytes()
        state[path.relative_to(root).as_posix()]={"size":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
    (root/".fly-sync-state.json").write_text(json.dumps(state))
    manifest = {"dataset_epoch":"epoch-1","epoch_id":"epoch-1","source_revision":revision,
        "deployed_revision":revision,"tile_config_signature":collected["tile_config_signature"],
        "dataset_checksum":sha({"revision":revision,"epoch":"epoch-1","files":state})}
    manifest["entry_hash"] = sha(manifest)
    (root/"canonical_dataset_current.json").write_text(json.dumps(manifest))
    generation = generation_identity(manifest,analyzer_revision="analyzer-1")
    import research.entry_baseline_replay as replay_module
    actual_context = replay_module._execution_context
    diagnostics = []
    def context(episode, result, generation):
        diagnostics.append((len(episode.get("_baseline_context_coverage") or []), episode.get("_baseline_context_pin_reasons")))
        return actual_context(episode,result,generation)
    monkeypatch.setattr(replay_module,"_execution_context",context)
    report = materialize_v3_opportunity_replay(root,generation=generation,canonical_manifest=manifest)
    entries = market_entries(report)
    assert {ep["direction"] for ep, _ in entries} == {"LONG","SHORT"}
    for _, entry in entries:
        if defect is None:
            assert entry.get("model_context_status") == "SUPPORTED", str(diagnostics[:3]) + json.dumps(entry.get("model_context_blockers"))
            assert entry["execution_model_context"]["qualification_eligible"] is False
            assert entry["conservative_receipt"]["measured_input_latency_sec"] is None
            assert entry["outcome_state"] == "PARTIAL_FILL" and entry["conservative_receipt"]["supported"] is True
        else:
            assert entry.get("model_context_status") == "UNKNOWN" or entry["outcome_state"] == "UNKNOWN"
            assert "execution_model_context" not in entry
    if defect is None:
        from test_conservative_shadow_report import _fixture
        from test_declared_shadow_model import contract
        from research.conservative_shadow_report import build_conservative_shadow_report
        _, candidates, artifact, _ = _fixture(tmp_path/"policies", model=False)
        artifact.update(evaluation_generation=generation, artifact_identity={
            "epoch_id":generation["epoch_id"],"source_revision":generation["source_revision"],
            "analyzer_generation_revision":generation["analyzer_revision"],"tile_config_signature":generation["tile_config_signature"]})
        terminals = build_conservative_shadow_report(root,expected_generation=generation,
            baseline_report=report,policy_candidates=candidates,policy_artifact_receipt=artifact,research_model=contract(generation))
        selected = [r for r in terminals["results"] if r.get("baseline_id")=="MARKET_ENTRY_AT_SIGNAL"]
        assert len(selected)==2
        assert all(r["status"]=="COMPLETE" for r in selected), selected
        assert all(r["terminal"]["economics_evidence_basis"]=="DECLARED_SIMULATION" for r in selected)
        assert terminals["live_qualification"] is False


def test_actual_direction_conflict_stays_rejected(tmp_path):
    generation, manifest = dataset(tmp_path)
    path = tmp_path/"v3/ledgers/opportunity.jsonl"
    row = json.loads(path.read_text())
    row["causal_identity"] = {"direction":"SHORT"}
    path.write_text(json.dumps(row)+"\n")
    report = materialize_v3_opportunity_replay(tmp_path)
    assert all("CONFLICTING_CAUSAL_IDENTITY:raw_ai_direction" in entry["rejection_codes"]
               for _, entry in market_entries(report))
