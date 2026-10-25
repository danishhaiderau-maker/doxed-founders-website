import json
import tempfile
import time
from pathlib import Path

import pytest
import lifecycle_bundles

from lifecycle_bundles import (
    COMPLETION_SCHEMA, LifecycleKey,
    classify_completion, collect_lifecycle_rows, lifecycle_key,
    materialize_bundle, verify_bundle,
)


def completion(now, outcome="NO_FILL", **overrides):
    receipt = {
        "schema": COMPLETION_SCHEMA,
        "terminal": True,
        "entry_outcome": outcome,
        "entry_schedule_terminal": True,
        "position_closed_or_never_opened": True,
        "post_observation_complete": True,
        "terminal_ts": now - 10_000,
        "horizon_complete_ts": now - 2_000,
    }
    receipt.update(overrides)
    return receipt


def row(key, record_id, now, **extra):
    material = {
        "record_id": record_id,
        "ledger": "lifecycle",
        "epoch_id": key.collection_epoch_id,
        "episode_id": key.episode_id,
        "policy_signature": key.policy_signature,
        "research_lane": key.research_lane,
        "observed_ts": now - 10_000,
        "source_revision": "a" * 40,
        "deployed_revision": "b" * 40,
        "tile_config_signature": "c" * 64,
        "config_signature": "d" * 64,
        "bundle_completion": completion(now),
    }
    material.update(extra)
    if record_id in {"life-1", "qualification-horizon"}:
        receipt = material.get("bundle_completion")
        if isinstance(receipt, dict):
            completion_material = dict(receipt)
            receipt["completion_receipt_sha256"] = __import__("hashlib").sha256(
                lifecycle_bundles.canonical_json(completion_material).encode("utf-8")
            ).hexdigest()
            collected = {
                "schema": "lifecycle_evidence_collected_v1",
                "identity": key.as_dict(),
                "event_id": record_id,
                "provenance": {
                    "source_revision": material["source_revision"],
                    "deployed_revision": material["deployed_revision"],
                    "tile_config_signature": material["tile_config_signature"],
                    "config_signature": material["config_signature"],
                },
                "completion_receipt_sha256": receipt["completion_receipt_sha256"],
                "qualification_eligible_at": now - 2_000,
                "evidence_collected_at": now - 1_999,
            }
            collected["evidence_collected_receipt_sha256"] = __import__("hashlib").sha256(
                lifecycle_bundles.canonical_json(collected).encode("utf-8")
            ).hexdigest()
            material["evidence_collection_receipt"] = collected
    return material


def write_ledger(root, ledger, rows):
    path = Path(root) / "v3" / "ledgers" / f"{ledger}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in rows), encoding="utf-8")


def test_composite_identity_separates_policy_and_lane():
    base = {"epoch_id": "epoch-1", "episode_id": "episode-1"}
    a = lifecycle_key({**base, "policy_signature": "policy-a", "research_lane": "fixed"})
    b = lifecycle_key({**base, "policy_signature": "policy-b", "research_lane": "fixed"})
    c = lifecycle_key({**base, "policy_signature": "policy-a", "research_lane": "mfe"})
    assert len({a.identity_id, b.identity_id, c.identity_id}) == 3
    with pytest.raises(ValueError, match="policy_signature"):
        lifecycle_key({**base, "research_lane": "fixed"})


def test_collection_joins_only_exact_identity_and_does_not_guess_sparse_rows():
    now = time.time()
    key = LifecycleKey("epoch-1", "episode-1", "policy-a", "FIXED")
    exact = row(key, "life-1", now)
    sparse = {"record_id": "decision-sparse", "epoch_id": "epoch-1", "episode_id": "episode-1"}
    with tempfile.TemporaryDirectory() as tmp:
        write_ledger(tmp, "lifecycle", [exact])
        write_ledger(tmp, "decision", [sparse])
        grouped = collect_lifecycle_rows(tmp)
    assert list(grouped) == [key]
    assert [item["record_id"] for item in grouped[key]] == ["life-1"]


def test_completion_fails_closed_for_missing_receipt_horizon_and_fill_costs():
    now = 20_000.0
    assert classify_completion([], now=now)["blockers"] == ["COMPLETION_RECEIPT_MISSING"]
    key = LifecycleKey("e", "episode", "policy", "FIXED")
    immature = row(key, "a", now, bundle_completion=completion(
        now, terminal_ts=now - 100, horizon_complete_ts=now - 50,
    ))
    assert "LIFECYCLE_HORIZON_INCOMPLETE" in classify_completion([immature], now=now)["blockers"]
    filled = row(key, "b", now, bundle_completion=completion(now, outcome="PARTIAL_FILL"))
    report = classify_completion([filled], now=now)
    assert set(report["blockers"]) >= {
        "EXIT_EVIDENCE_INCOMPLETE", "COST_EVIDENCE_INCOMPLETE",
        "MFE_MAE_INCOMPLETE", "NET_PNL_UNRECONCILED",
    }


def test_explicit_unknown_requires_reason_and_remains_unknown():
    now = 20_000.0
    key = LifecycleKey("e", "episode", "policy", "FIXED")
    missing = row(key, "a", now, bundle_completion=completion(now, outcome="UNKNOWN"))
    assert "UNKNOWN_REASON_MISSING" in classify_completion([missing], now=now)["blockers"]
    proven = row(key, "b", now, bundle_completion=completion(
        now, outcome="UNKNOWN", unknown_reason="RAW_BBO_MISSING",
    ))
    report = classify_completion([proven], now=now)
    assert report["ready"]
    assert report["classification"] == "UNKNOWN"


def test_bundle_is_content_addressed_idempotent_and_never_authorizes_cleanup():
    now = 20_000.0
    key = LifecycleKey("epoch-1", "episode-1", "policy-a", "FIXED")
    rows = [row(key, "life-1", now)]
    with tempfile.TemporaryDirectory() as tmp:
        first = materialize_bundle(tmp, key, rows, now=now)
        second = materialize_bundle(tmp, key, rows, now=now)
        verification = verify_bundle(first["path"])
        manifest = verification["manifest"]
    assert first["written"] is True
    assert second["duplicate"] is True
    assert verification["passed"]
    assert manifest["lifecycle_identity_id"] == key.identity_id
    assert manifest["source_cleanup_authorized"] is False
    assert manifest["files"][0]["row_count"] == 1
    assert len(manifest["cleanup_manifest_sha256"]) == 64


def test_qualification_bundle_is_not_published_without_evidence_collection_receipt():
    now = 20_000.0
    key = LifecycleKey("epoch-1", "episode-1", "policy-a", "FIXED")
    incomplete = row(key, "no-collection-receipt", now)
    with tempfile.TemporaryDirectory() as tmp:
        result = materialize_bundle(tmp, key, [incomplete], now=now)
        assert result["written"] is False
        assert result["maturity"] == "QUALIFICATION_PENDING"
        assert result["evidence_collection"]["ready"] is False
        assert result["evidence_collection"]["blockers"] == [
            "EVIDENCE_COLLECTION_RECEIPT_MISSING"
        ]
        assert not (Path(tmp) / "v3" / "lifecycle_bundles").exists()


def test_late_terminal_evidence_creates_new_content_bundle_instead_of_being_ignored():
    now = 20_000.0
    key = LifecycleKey("epoch-1", "episode-1", "policy-a", "FIXED")
    first_rows = [row(key, "life-1", now)]
    late_rows = [*first_rows, row(
        key, "late-cost-reconciliation", now,
        observed_ts=now - 1_900, bundle_completion=None,
    )]
    with tempfile.TemporaryDirectory() as tmp:
        first = materialize_bundle(tmp, key, first_rows, now=now)
        second = materialize_bundle(tmp, key, late_rows, now=now)
        assert Path(first["path"]).exists()
        assert Path(second["path"]).exists()
    assert first["bundle_id"] != second["bundle_id"]
    assert first["manifest"]["lifecycle_identity_id"] == second["manifest"]["lifecycle_identity_id"]


def test_every_event_row_must_carry_the_same_complete_provenance():
    now = 20_000.0
    key = LifecycleKey("epoch-1", "episode-1", "policy-a", "FIXED")
    sparse = row(key, "sparse", now)
    sparse.pop("source_revision")
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(
        ValueError, match="LIFECYCLE_PROVENANCE_NOT_UNIQUE:source_revision"
    ):
        materialize_bundle(tmp, key, [row(key, "complete", now), sparse], now=now)


def test_market_segment_context_ref_is_never_copied_r3():
    now = 20_000.0
    key = LifecycleKey("epoch-1", "episode-1", "policy-a", "FIXED")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        segment = root / "v3" / "market_segments" / "ab" / ("a" * 64 + ".json")
        segment.parent.mkdir(parents=True)
        segment.write_text(
            '{"end_ts":2,"rows":[{"ask":101,"bid":100,"ts":1}],"start_ts":1}',
            encoding="utf-8",
        )
        import hashlib
        digest = hashlib.sha256(segment.read_bytes()).hexdigest()
        renamed = segment.with_name(digest + ".json")
        segment.rename(renamed)
        rows = [row(key, "life-1", now, market_context_segment_refs=[{
            "relative_path": renamed.relative_to(root).as_posix(), "sha256": digest,
        }])]
        result = materialize_bundle(root, key, rows, now=now)
        # R3 rewrite (HM #873 a1): a market_context_segment_ref is not evidence; it is
        # never copied and never listed as a MARKET_SEGMENT bundle file.
        roles = {item["role"] for item in result["manifest"]["files"]}
        assert not (Path(result["path"]) / "market_segments").exists()
        assert "MARKET_SEGMENT" not in roles
        assert renamed.exists()  # source untouched


def test_singular_post_exit_segment_reference_is_never_omitted():
    # R3 rewrite: a market_segment_ref is not evidence; it is never copied into a bundle
    # and never listed as a MARKET_SEGMENT bundle role.
    now = 20_000.0
    key = LifecycleKey("epoch-1", "episode-1", "policy-a", "FIXED")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        envelope = '{"end_ts":2,"rows":[{"ask":101,"bid":100,"ts":1}],"start_ts":1}'
        import hashlib
        digest = hashlib.sha256(envelope.encode("utf-8")).hexdigest()
        segment = root / "v3" / "market_segments" / digest[:2] / f"{digest}.json"
        segment.parent.mkdir(parents=True)
        segment.write_text(envelope, encoding="utf-8")
        result = materialize_bundle(root, key, [row(
            key, "qualification-horizon", now,
            market_segment_ref={
                "relative_path": segment.relative_to(root).as_posix(),
                "sha256": digest,
            },
        )], now=now)
        roles = {item["role"] for item in result["manifest"]["files"]}
        assert not (Path(result["path"]) / "market_segments").exists()
        assert "MARKET_SEGMENT" not in roles
        assert segment.read_text(encoding="utf-8") == envelope  # source untouched


def test_truncated_source_ledger_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "v3" / "ledgers" / "lifecycle.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"record_id":"broken"}', encoding="utf-8")
        with pytest.raises(ValueError, match="TRUNCATED_JSONL_LINE"):
            collect_lifecycle_rows(tmp)
