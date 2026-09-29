"""Normal V3 producer tests. All files are disposable pytest fixtures."""
import hashlib
import json
from pathlib import Path

import pytest

from research.entry_baseline_replay import materialize_v3_opportunity_replay
from research.policy_evidence_schema import canonical_json, generation_identity
from research_v3_contract import canonical_hash
from research.quantity_execution import build_signed_quantity_constraints
from research.declared_shadow_model import _baseline_context
from test_entry_baseline_replay import _episode, _row


def _sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _dataset(root, *, context=True, stage=True):
    episode = _episode(ask_qty=.4)
    episode.update(epoch_id="epoch-1", shared_ai_call_id="shared-1", event_id="event-1", deployed_revision="rev-1")
    constraints = build_signed_quantity_constraints(symbol="BTC", quantity_step="0.1", quantity_precision=1,
        min_lot="0.1", min_notional="1", captured_at="2026-09-01T00:00:00Z", source_revision="rev-1", source="TEST")
    episode["signed_quantity_constraints"] = constraints
    rows = {row["bucket_ts"]: row for row in episode.pop("market_microstructure_rows")}
    rows.update({ts: _row(ts, ask_qty=.4) for ts in range(101, 105)})
    segment = {"schema": "market_segment_v3", "symbol": "BTC", "timeframe": "1s",
               "start_ts": 100, "end_ts": 1900, "rows": [rows[ts] for ts in sorted(rows)]}
    segment_raw = canonical_json(segment).encode()
    segment_sha = hashlib.sha256(segment_raw).hexdigest()
    segment_relative = f"v3/market_segments/{segment_sha[:2]}/{segment_sha}.json"
    identity = {field: episode[field] for field in ("epoch_id", "opportunity_id", "episode_id", "shared_ai_call_id", "event_id")}
    binding = {**identity, "record_id": "segment-1", "context_role": "ENTRY_PATH",
               "coverage": {"conservative_bbo_depth_eligible": True},
               "segment_ref": {"sha256": segment_sha, "relative_path": segment_relative, "row_count": len(rows)}}
    baseline = episode["baseline_schedules"]["MARKET_ENTRY_AT_SIGNAL"]
    source_identity = {"epoch_id": "epoch-1", "source_revision": "rev-1", "deployed_revision": "rev-1",
                       "tile_config_signature": "tiles-1"}
    stage_row = {**identity, "schema": "compressed_chase_shadow_v1", "event": "STAGE", "stage_index": 0,
        "identity_complete": True, "missing_identity_fields": [], "direction": "LONG", "signal_ts": 100,
        "observed_ts": 100, "event_source_revision": "rev-1", "event_config_signature": "tiles-1",
        "requested_qty": 1, "requested_margin_usd": 10, "leverage": 10, "virtual_limit_price": 100,
        "signed_quantity_constraints": constraints, "policy_signature": "source-policy"}
    sizing = {**identity, "schema": "baseline_sizing_authorization_v1", "source_identity": source_identity,
        "baseline_id": "MARKET_ENTRY_AT_SIGNAL", "baseline_policy_signature": baseline["policy_signature"],
        "source_stage_zero_row_sha256": _sha(stage_row), "source_policy_signature": "source-policy",
        "declared_at_ts": 100, "sizing_mode": "FIXED_QUANTITY",
        "coverage_policy": {"sampling_interval_sec": 1, "first_sample_offset_sec": 1, "required_horizon_end_ts": 104}}
    atr = {**identity, "schema": "baseline_fill_atr_observation_v1", "source_identity": source_identity,
        "symbol": "BTC", "atr_basis": "EXPLICIT_AT_FILL_OBSERVATION", "observed_ts": 100,
        "available_at_ts": 100, "atr_pct": 1, "provenance": "EXACT_TEST_FILL_OBSERVATION"}
    files = {"v3/ledgers/opportunity.jsonl": (canonical_json(episode) + "\n").encode(),
             "v3/ledgers/market_segment.jsonl": (canonical_json(binding) + "\n").encode(),
             segment_relative: segment_raw}
    if stage:
        files["chase_offset_touch_grid.jsonl"] = (canonical_json(stage_row) + "\n").encode()
    if context:
        files["v3/ledgers/baseline_execution_context.jsonl"] = (canonical_json(sizing) + "\n" + canonical_json(atr) + "\n").encode()
    state = {}
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        state[name] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    (root / ".fly-sync-state.json").write_text(json.dumps(state), encoding="utf-8")
    manifest = {"dataset_epoch": "epoch-1", **source_identity,
        "dataset_checksum": _sha({"revision": "rev-1", "epoch": "epoch-1", "files": state})}
    manifest["entry_hash"] = _sha(manifest)
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    return generation_identity(manifest, analyzer_revision="analyzer-1"), manifest, files


def _market_result(report):
    return next(row for row in report["episode_receipts"][0]["results"] if row["baseline_id"] == "MARKET_ENTRY_AT_SIGNAL")


def test_context_selection_uses_accepted_completion_not_top_level_trigger(monkeypatch):
    from copy import deepcopy
    from research import entry_baseline_replay as replay
    from test_baseline_execution_context import _fixture
    values = _fixture()
    receipt = deepcopy(values["entry_receipt"])
    attempt = receipt["quantity_attempts"][0]
    receipt["quantity_attempts"] = [
        {**attempt, "rounded_executable_quantity": .2, "execution_price": 100, "trigger_bucket_ts": 10},
        {**attempt, "rounded_executable_quantity": .2, "execution_price": 104, "trigger_bucket_ts": 12},
    ]
    late_atr = deepcopy(values["atr_evidence"])
    late_atr["row"].update(observed_ts="12", available_at_ts=12)
    episode = {**values["identity"],
        "_baseline_context_sources": [*values["stage_zero_evidence"], values["sizing_authorization"],
                                      values["atr_evidence"], late_atr],
        "_baseline_context_coverage": [{"object": values["coverage_evidence"]}],
        "_baseline_context_opportunity": {"selection_test_only": True}}
    selected = {}
    def capture(**kwargs):
        selected.update(kwargs)
        return {"status": "SELECTION_TEST_ONLY", "context": None, "reason_codes": []}
    monkeypatch.setattr(replay, "build_baseline_execution_context", capture)
    before = deepcopy(receipt)
    result = replay._execution_context(episode, {
        "baseline_id": values["identity"]["baseline_id"],
        "policy_signature": values["identity"]["baseline_policy_signature"],
        "conservative_receipt": receipt}, values["generation"])
    assert result["status"] == "SELECTION_TEST_ONLY"
    assert selected["atr_evidence"] is late_atr
    assert receipt == before


def test_context_selection_rejects_invalid_accepted_quantity_without_trigger_fallback():
    from research.entry_baseline_replay import _execution_context
    from test_baseline_execution_context import _fixture
    values = _fixture()
    values["entry_receipt"]["quantity_attempts"][0]["rounded_executable_quantity"] = .1
    result = _execution_context(values["identity"], {
        "baseline_id": values["identity"]["baseline_id"],
        "policy_signature": values["identity"]["baseline_policy_signature"],
        "conservative_receipt": values["entry_receipt"]}, values["generation"])
    assert result["status"] == "UNKNOWN"
    assert result["reason_codes"] == ["BASELINE_CONTEXT_ACCEPTED_FILL_QUANTITY_MISMATCH"]


def test_normal_disk_materializer_builds_signed_context_before_episode_hash(tmp_path):
    generation, manifest, files = _dataset(tmp_path)
    report = materialize_v3_opportunity_replay(tmp_path, generation=generation, canonical_manifest=manifest)
    result = _market_result(report)
    assert result["outcome_state"] == "PARTIAL_FILL"
    assert result["model_context_status"] == "SUPPORTED", result.get("model_context_blockers")
    context = _baseline_context(result, generation)
    assert float(context["margin_usd"]) == pytest.approx(4.04)
    assert context["requested_qty"] == "1.0"
    assert len(context["source_evidence_sha256"]) == 5
    receipt = report["episode_receipts"][0]
    assert receipt["generation"] == report["generation"] == generation
    assert receipt["receipt_id"] == canonical_hash("entry-baseline-episode", {k: v for k, v in receipt.items() if k != "receipt_id"})
    assert report["report_id"] == canonical_hash("entry-baseline-replay", {k: v for k, v in report.items() if k != "report_id"})
    assert all((tmp_path / name).read_bytes() == raw for name, raw in files.items())


def test_actual_stage_zero_resolved_even_without_future_context_producer(tmp_path):
    generation, manifest, _ = _dataset(tmp_path, context=False)
    report = materialize_v3_opportunity_replay(tmp_path, generation=generation)
    result = _market_result(report)
    assert result["outcome_state"] == "PARTIAL_FILL"
    assert result["model_context_status"] == "UNKNOWN"
    assert "BASELINE_CONTEXT_SIZING_AUTHORIZATION_MISSING" in result["model_context_blockers"]
    assert "BASELINE_CONTEXT_EXACT_FILL_ATR_MISSING" in result["model_context_blockers"]
    assert "BASELINE_CONTEXT_STAGE_ZERO_MISSING" not in result["model_context_blockers"]
    status = report["episode_receipts"][0]["model_context_source_resolution"]
    assert any(row["source_id"] == "chase_offset_touch_grid.jsonl" and row["status"] == "VERIFIED" for row in status)


@pytest.mark.parametrize("tamper", ["stage", "state", "manifest", "opportunity"])
def test_tampering_blocks_context_not_historical_entry_fill(tmp_path, tamper):
    generation, manifest, _ = _dataset(tmp_path)
    target = {"stage": "chase_offset_touch_grid.jsonl", "state": ".fly-sync-state.json",
              "manifest": "canonical_dataset_current.json", "opportunity": "v3/ledgers/opportunity.jsonl"}[tamper]
    path = tmp_path / target
    if tamper in {"stage", "opportunity"}:
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        payload = json.loads(path.read_text())
        if tamper == "state":
            payload["chase_offset_touch_grid.jsonl"]["size"] += 1
        else:
            payload["source_revision"] = "other"
        path.write_text(json.dumps(payload))
    result = _market_result(materialize_v3_opportunity_replay(tmp_path, generation=generation))
    assert result["outcome_state"] == "PARTIAL_FILL"
    assert result["model_context_status"] == "UNKNOWN"
    assert "execution_model_context" not in result


def test_generation_changes_hash_and_disabled_path_remains_compatible(tmp_path):
    generation, _, _ = _dataset(tmp_path)
    old = materialize_v3_opportunity_replay(tmp_path)
    assert "generation" not in old
    assert "model_context_status" not in _market_result(old)
    current = materialize_v3_opportunity_replay(tmp_path, generation=generation)
    assert current["report_id"] != old["report_id"]
    assert current["episode_receipts"][0]["receipt_id"] != old["episode_receipts"][0]["receipt_id"]


def test_foreign_event_stage_is_not_adopted(tmp_path):
    generation, _, _ = _dataset(tmp_path, context=False)
    # A genuine separately pinned foreign event is not a missing-data fallback.
    path = tmp_path / "chase_offset_touch_grid.jsonl"
    row = json.loads(path.read_text())
    row["event_id"] = "foreign"
    raw = (canonical_json(row) + "\n").encode()
    path.write_bytes(raw)
    state_path = tmp_path / ".fly-sync-state.json"
    state = json.loads(state_path.read_text())
    state[path.name] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    state_path.write_text(json.dumps(state))
    manifest_path = tmp_path / "canonical_dataset_current.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["dataset_checksum"] = _sha({"revision": "rev-1", "epoch": "epoch-1", "files": state})
    manifest["entry_hash"] = _sha({k: v for k, v in manifest.items() if k != "entry_hash"})
    manifest_path.write_text(json.dumps(manifest))
    generation = generation_identity(manifest, analyzer_revision="analyzer-1")
    result = _market_result(materialize_v3_opportunity_replay(tmp_path, generation=generation))
    assert "BASELINE_CONTEXT_STAGE_ZERO_MISSING" in result["model_context_blockers"]


def test_stage_source_is_read_once_not_once_per_baseline(tmp_path, monkeypatch):
    generation, _, _ = _dataset(tmp_path)
    original = Path.open
    reads = []
    def observed(path, *args, **kwargs):
        if path.name == "chase_offset_touch_grid.jsonl":
            reads.append(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    report = materialize_v3_opportunity_replay(tmp_path, generation=generation)
    assert _market_result(report)["model_context_status"] == "SUPPORTED"
    assert len(reads) == 1


def test_changed_generation_during_materialization_cannot_publish(tmp_path, monkeypatch):
    from research import entry_baseline_replay as replay
    generation, _, _ = _dataset(tmp_path)
    original = replay.materialize_same_opportunity_replay
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path = tmp_path / ".fly-sync-state.json"
        path.write_bytes(path.read_bytes() + b" ")
        return result
    monkeypatch.setattr(replay, "materialize_same_opportunity_replay", changed)
    with pytest.raises(ValueError, match="PINNED_GENERATION_CHANGED_DURING_REPLAY"):
        materialize_v3_opportunity_replay(tmp_path, generation=generation)


def _repin_dataset(root):
    state_path = root / ".fly-sync-state.json"
    state = json.loads(state_path.read_text())
    for name in state:
        path = root / name
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        state[name] = {"size": path.stat().st_size, "sha256": digest}
    state_path.write_text(json.dumps(state))
    manifest_path = root / "canonical_dataset_current.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["dataset_checksum"] = _sha({"revision": "rev-1", "epoch": "epoch-1", "files": state})
    manifest["entry_hash"] = _sha({key: value for key, value in manifest.items() if key != "entry_hash"})
    manifest_path.write_text(json.dumps(manifest))
    return generation_identity(manifest, analyzer_revision="analyzer-1")


def test_ledgers_above_16_mib_keep_verified_stage_and_opportunity_context(tmp_path, monkeypatch):
    _, _, _files = _dataset(tmp_path)
    # Match the observed Fly opportunity size; valid whitespace is included
    # in the FULL source hash and cannot be skipped by a selected-row digest.
    source_size = 18_920_143
    for name in ("chase_offset_touch_grid.jsonl", "v3/ledgers/opportunity.jsonl"):
        path = tmp_path / name
        row_bytes = path.read_bytes()
        remaining = source_size - len(row_bytes)
        with path.open("wb") as handle:
            while remaining:
                count = min(64 * 1024, remaining)
                handle.write(b" " * (count - 1) + b"\n")
                remaining -= count
            handle.write(row_bytes)
        assert path.stat().st_size == source_size
    generation = _repin_dataset(tmp_path)
    original = Path.read_bytes
    def forbid_whole_ledger(path):
        assert path.suffix != ".jsonl", "Large ledgers must not be materialized as raw_bytes"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", forbid_whole_ledger)
    report = materialize_v3_opportunity_replay(tmp_path, generation=generation)
    result = _market_result(report)
    assert result["model_context_status"] == "SUPPORTED", result["model_context_blockers"]
    context = _baseline_context(result, generation)
    proofs = context["verified_ledger_row_membership"]
    for name in ("chase_offset_touch_grid.jsonl", "v3/ledgers/opportunity.jsonl"):
        proof = next(row for row in proofs if row["source_id"] == name)
        assert proof["byte_offset"] > 16 * 1024 * 1024
        assert proof["line_number"] > 1
        assert proof["verification_basis"] == "VERIFIED_FULL_LEDGER_SHA256_STREAM_V1"


def test_opportunity_changed_after_stream_verification_cannot_get_row_proof(tmp_path, monkeypatch):
    from research.baseline_execution_context import VerifiedLedgerRowIndex
    generation, _, _ = _dataset(tmp_path)
    original = VerifiedLedgerRowIndex.add_source
    def changed(index, root, relative, **kwargs):
        result = original(index, root, relative, **kwargs)
        if relative == "v3/ledgers/opportunity.jsonl":
            path = root / relative
            row = json.loads(path.read_text())
            row["requested_qty"] = 1.1
            path.write_text(json.dumps(row) + "\n")
        return result
    monkeypatch.setattr(VerifiedLedgerRowIndex, "add_source", changed)
    with pytest.raises(ValueError, match="BASELINE_CONTEXT_SOURCE_CHANGED_DURING_REPLAY"):
        materialize_v3_opportunity_replay(tmp_path, generation=generation)
