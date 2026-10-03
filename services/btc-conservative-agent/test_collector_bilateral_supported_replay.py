"""Synthetic prospective model: collector buckets to normal bilateral replay."""
from copy import deepcopy
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


ROUTE_MATRIX = (
    "MARKET_ENTRY_AT_SIGNAL",
    "NO_CHASE_LIMIT",
    "CHASE_WINDOW_0",
    "FINAL_MARKET_AFTER_EXPIRY",
)


def _collector_worker_terminal_pipeline(root, *, ai_direction="LONG", verdict="APPROVED",
                                        omitted_bucket_ts=None):
    """One real producer/worker/replay/terminal fixture shared by route assertions."""
    import research_v3_store as store_module
    revision = "a" * 40
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("SOURCE_GIT_REV", revision)
        patch.setattr(store_module, "_provenance_cache", None)
        template = root / "template"
        dataset(template)
        opportunity = json.loads((template / "v3/ledgers/opportunity.jsonl").read_text())
        opportunity.pop("baseline_schedule_snapshot")
        opportunity.update(
            source_revision=revision,
            deployed_revision=revision,
            direction=ai_direction,
            raw_direction=ai_direction,
            raw_ai_decision=verdict,
            family_policy_decision=verdict,
            execution_disposition=verdict,
        )
        from research.quantity_execution import build_signed_quantity_constraints
        opportunity["research_baseline_context_declaration"]["signed_quantity_constraints"] = (
            build_signed_quantity_constraints(
                symbol="BTC", quantity_step="0.1", quantity_precision=1,
                min_lot="0.1", min_notional="1", captured_at="1970-01-01T00:01:39Z",
                source_revision=revision, source="SYNTHETIC_TEST",
            )
        )
        data_root = root / "actual"
        store = V3EvidenceStore(data_root, epoch_id="epoch-1")
        assert store.append("opportunity", opportunity)["written"]
        collected = json.loads((data_root / "v3/ledgers/opportunity.jsonl").read_text())
        assert store.append("decision", {
            "record_id": "decision:1", "episode_id": "ep-1",
            "event_id": "event-1", "primary_outcome": verdict,
        })["written"]
        rows = [
            build_bucket(
                bucket_ts=ts, bid=99 if ts <= 100 else 105,
                ask=101 if ts <= 100 else 105.1,
                bid_qty=.4 if ts == 100 else 10,
                ask_qty=.4 if ts == 100 else 10,
                last=100 if ts <= 100 else 105,
                source_ts=ts, trades=(), symbol="BTC",
            )
            for ts in range(40, 7301) if ts != omitted_bucket_ts
        ]
        (data_root / "market_microstructure_1s.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )
        worker_result = data_root / "v3/receipts/future-path-worker-matrix.json"
        run = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("research_v3_future_paths_worker.py")),
             "--data-dir", str(data_root), "--epoch-id", "epoch-1", "--now-ts", "7400",
             "--max-batch", "64", "--result", str(worker_result)],
            capture_output=True, timeout=30,
        )
        assert run.returncode == 0, run.stderr.decode(errors="replace")
        assert json.loads(worker_result.read_text())["complete_count"] == 1
        state = {}
        for path in sorted((data_root / "v3").rglob("*.json*")):
            if "receipts" in path.parts:
                continue
            raw = path.read_bytes()
            state[path.relative_to(data_root).as_posix()] = {
                "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            }
        (data_root / ".fly-sync-state.json").write_text(json.dumps(state))
        manifest = {
            "dataset_epoch": "epoch-1", "epoch_id": "epoch-1",
            "source_revision": revision, "deployed_revision": revision,
            "tile_config_signature": collected["tile_config_signature"],
            "dataset_checksum": sha({"revision": revision, "epoch": "epoch-1", "files": state}),
        }
        manifest["entry_hash"] = sha(manifest)
        (data_root / "canonical_dataset_current.json").write_text(json.dumps(manifest))
        generation = generation_identity(manifest, analyzer_revision="analyzer-1")
        baseline = materialize_v3_opportunity_replay(
            data_root, generation=generation, canonical_manifest=manifest,
        )
        from test_conservative_shadow_report import _fixture
        from test_declared_shadow_model import contract
        from research.conservative_shadow_report import build_conservative_shadow_report
        _, candidates, artifact, _ = _fixture(root / "policies", model=False)
        artifact.update(evaluation_generation=generation, artifact_identity={
            "epoch_id": generation["epoch_id"],
            "source_revision": generation["source_revision"],
            "analyzer_generation_revision": generation["analyzer_revision"],
            "tile_config_signature": generation["tile_config_signature"],
        })
        terminal = build_conservative_shadow_report(
            data_root, expected_generation=generation, baseline_report=baseline,
            policy_candidates=candidates, policy_artifact_receipt=artifact,
            research_model=contract(generation),
        )
        return {
            "baseline": baseline, "terminal": terminal, "root": data_root,
            "generation": generation, "candidates": candidates, "artifact": artifact,
        }


@pytest.fixture(scope="module")
def collector_route_matrix(tmp_path_factory):
    return _collector_worker_terminal_pipeline(tmp_path_factory.mktemp("collector-route-matrix"))


@pytest.mark.parametrize("baseline_id,expected_entry,expected_terminal", (
    ("MARKET_ENTRY_AT_SIGNAL", {"LONG": "PARTIAL_FILL", "SHORT": "PARTIAL_FILL"},
     {"LONG": "COMPLETE", "SHORT": "COMPLETE"}),
    ("NO_CHASE_LIMIT", {"LONG": "NO_FILL", "SHORT": "FULL_FILL"},
     {"SHORT": "COMPLETE"}),
    ("CHASE_WINDOW_0", {"LONG": "NO_FILL", "SHORT": "FULL_FILL"},
     {"SHORT": "COMPLETE"}),
    ("FINAL_MARKET_AFTER_EXPIRY", {"LONG": "UNKNOWN", "SHORT": "UNKNOWN"}, {}),
))
def test_registered_route_bilateral_producer_worker_terminal_matrix(
    collector_route_matrix, baseline_id, expected_entry, expected_terminal,
):
    baseline = collector_route_matrix["baseline"]
    terminal = collector_route_matrix["terminal"]
    source_rows = []
    for episode in baseline["episode_receipts"]:
        result = next(row for row in episode["results"] if row["baseline_id"] == baseline_id)
        source_rows.append((episode, result))
    assert len(source_rows) == 2
    assert {episode["direction"] for episode, _ in source_rows} == {"LONG", "SHORT"}
    assert {episode["opportunity_id"] for episode, _ in source_rows} == {"opp-1"}
    assert {episode["source_episode_id"] for episode, _ in source_rows} == {"ep-1"}
    assert all(result["baseline_id"] == baseline_id for _, result in source_rows)
    assert all(episode["raw_ai_decision"] == "APPROVED" for episode, _ in source_rows)
    assert {episode["direction"]: result["outcome_state"]
            for episode, result in source_rows} == expected_entry
    if baseline_id == "FINAL_MARKET_AFTER_EXPIRY":
        assert all(result["rejection_codes"] == ["DECLARED_BASELINE_SCHEDULE_MISSING"]
                   for _, result in source_rows)
    terminal_rows = [
        row for row in terminal["results"] if row.get("baseline_id") == baseline_id
    ]
    terminal_by_episode = {row["episode_id"]: row for row in terminal_rows}
    assert {
        episode["direction"]: terminal_by_episode[episode["episode_id"]]["status"]
        for episode, _ in source_rows if episode["episode_id"] in terminal_by_episode
    } == expected_terminal
    assert all(row["opportunity_id"] == "opp-1" for row in terminal_rows)
    assert baseline["same_opportunity_count"] == 1
    assert baseline["directional_episode_count"] == 2
    coverage = terminal["coverage_denominators"]
    assert coverage["source_opportunity_count"] == 1
    assert coverage["directional_arms_are_independent_samples"] is False
    assert coverage["source_candidate_attempts_are_independent_samples"] is False


def test_asymmetric_long_complete_short_unknown_keeps_unknown_non_profitable(
    collector_route_matrix,
):
    from research.conservative_shadow_report import build_conservative_shadow_report
    from test_declared_shadow_model import contract
    baseline = deepcopy(collector_route_matrix["baseline"])
    short = next(row for row in baseline["episode_receipts"] if row["direction"] == "SHORT")
    entry = next(row for row in short["results"]
                 if row["baseline_id"] == "MARKET_ENTRY_AT_SIGNAL")
    entry.pop("execution_model_context", None)
    entry["model_context_status"] = "UNKNOWN"
    entry["model_context_blockers"] = ["DIRECTION_SPECIFIC_BASELINE_EXECUTION_CONTEXT_REQUIRED"]
    terminal = build_conservative_shadow_report(
        collector_route_matrix["root"],
        expected_generation=collector_route_matrix["generation"],
        baseline_report=baseline,
        policy_candidates=collector_route_matrix["candidates"],
        policy_artifact_receipt=collector_route_matrix["artifact"],
        research_model=contract(collector_route_matrix["generation"]),
    )
    rows = [row for row in terminal["results"]
            if row.get("baseline_id") == "MARKET_ENTRY_AT_SIGNAL"]
    episode_direction = {
        row["episode_id"]: row["direction"] for row in baseline["episode_receipts"]
    }
    by_direction = {episode_direction[row["episode_id"]]: row for row in rows}
    assert by_direction["LONG"]["status"] == "COMPLETE"
    assert by_direction["SHORT"]["status"] == "UNKNOWN"
    assert by_direction["SHORT"]["net_pnl_usd"] is None
    assert by_direction["SHORT"]["blockers"] == ["BASELINE_EXECUTION_MODEL_CONTEXT_MISSING"]
    assert terminal["profitability_supported"] is False


@pytest.mark.parametrize("defect", [None, "missing", "late"])
@pytest.mark.parametrize("ai_direction,verdict", [
    ("NO_TRADE", "REJECTED"), ("LONG", "APPROVED"),
    ("SHORT", "APPROVED"), ("LONG", "REJECTED"),
    ("NO_TRADE", "NO_TRADE"),
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
    opportunity.update(
        direction=ai_direction, raw_direction=ai_direction,
        raw_ai_decision=verdict, family_policy_decision=verdict,
        execution_disposition=verdict,
    )
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
    for episode, entry in entries:
        assert episode["raw_ai_decision"] == verdict
        assert episode["original_ai_direction"] == ai_direction
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
