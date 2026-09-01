import copy
import json

import lifecycle_completion_reconciler
import research_v3_store
from lifecycle_bundles import LifecycleKey, materialize_bundle
from lifecycle_completion_receipts import (
    build_evidence_collected_receipt,
)
from lifecycle_completion_reconciler import (
    evaluate_lifecycle_completion,
    reconcile_lifecycle_completions,
)
from research.lifecycle_bundle_inventory import build_lifecycle_bundle_inventory
from research.lifecycle_evidence_join import (
    build_lifecycle_evidence_index,
    join_lifecycle_evidence,
    verify_manifest_collection_receipt,
)


NOW = 20_000.0
KEY = LifecycleKey("epoch-1", "episode-1", "policy-1", "CONTINUOUS")
PROVENANCE = {
    "source_revision": "a" * 40,
    "deployed_revision": "a" * 40,
    "tile_config_signature": "b" * 64,
}


def _row(ledger, record_id, **extra):
    return {
        **KEY.as_dict(), "ledger": ledger, "record_id": record_id,
        "event_id": "trade-1", "observed_ts": 10_000.0,
        **PROVENANCE, **extra,
    }


def _terminal_rows():
    return [
        _row(
            "order_intent", "schedule",
            intent_kind="AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
            schedule_lifecycle_final=True, chase_schedule_authoritative=True,
            schedule_sha256="c" * 64, requested_qty=1.0,
            chase_schedule={
                "terminal_ts": 10_000.0, "terminal_reason": "TTL_EXPIRED",
            },
        ),
        _row("lifecycle", "terminal", terminal=True, terminal_no_fill=True),
        _row(
            "market_segment", "post", context_role="POST_EXIT_PATH",
            coverage={
                "complete": True, "gaps_absent": True,
                "complete_through_ts": 18_000.0,
            },
        ),
    ]


def _completion():
    result = evaluate_lifecycle_completion(KEY, _terminal_rows(), now=NOW)
    assert result["ready"] is True
    return result["receipt"]


def test_receipt_rejects_early_and_non_terminal_completion():
    completion = _completion()
    early = build_evidence_collected_receipt(
        completion, identity=KEY.as_dict(), event_id="trade-1",
        provenance=PROVENANCE, collected_at=18_179.999,
    )
    assert early["ready"] is False
    assert "EVIDENCE_COLLECTION_TOO_EARLY" in early["blockers"]

    non_terminal = copy.deepcopy(completion)
    non_terminal["terminal"] = False
    blocked = build_evidence_collected_receipt(
        non_terminal, identity=KEY.as_dict(), event_id="trade-1",
        provenance=PROVENANCE, collected_at=NOW,
    )
    assert blocked["ready"] is False
    assert "LIFECYCLE_NOT_TERMINAL" in blocked["blockers"]


def test_exact_post_exit_horizon_and_provenance_are_content_bound():
    receipt = build_evidence_collected_receipt(
        _completion(), identity=KEY.as_dict(), event_id="trade-1",
        provenance=PROVENANCE, collected_at=18_180.0,
    )["receipt"]
    assert receipt["qualification_eligible_at"] == 18_180.0
    assert receipt["evidence_collected_at"] == 18_180.0
    assert receipt["identity"] == KEY.as_dict()
    assert receipt["provenance"] == PROVENANCE
    assert len(receipt["evidence_collected_receipt_sha256"]) == 64


def _write_raw_rows(root, rows):
    path = root / "v3" / "ledgers" / "lifecycle.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    for ledger in ("order_intent", "market_segment"):
        selected = [row for row in rows if row["ledger"] == ledger]
        if selected:
            target = root / "v3" / "ledgers" / f"{ledger}.jsonl"
            target.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in selected), encoding="utf-8")
    # lifecycle rows were initially mixed into the temporary list above.
    lifecycle = [row for row in rows if row["ledger"] == "lifecycle"]
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in lifecycle), encoding="utf-8")


def test_reconciler_is_idempotent_and_collision_fails_closed(tmp_path, monkeypatch):
    _write_raw_rows(tmp_path, _terminal_rows())
    monkeypatch.setattr(lifecycle_completion_reconciler, "_collection_provenance", lambda: dict(PROVENANCE))
    monkeypatch.setattr(research_v3_store, "_provenance_cache", {
        "evidence_provenance_schema": "v3_collection_provenance_v1", **PROVENANCE,
    })
    first = reconcile_lifecycle_completions(tmp_path, epoch_id="epoch-1", now=NOW)
    second = reconcile_lifecycle_completions(tmp_path, epoch_id="epoch-1", now=NOW + 10)
    assert first["evidence_collected_written_count"] == 1
    assert second["evidence_collected_duplicate_count"] == 1

    path = tmp_path / "v3" / "ledgers" / "lifecycle.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    collection = next(row for row in rows if row.get("observation_status") == "EVIDENCE_COLLECTION_COMPLETE")
    collection["evidence_collection_receipt"]["provenance"]["source_revision"] = "d" * 40
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    collision = reconcile_lifecycle_completions(tmp_path, epoch_id="epoch-1", now=NOW + 20)
    assert collision["ready_count"] == 0
    assert "EVIDENCE_COLLECTION_RECEIPT_COLLISION" in collision["assessments"][0]["blockers"]


def test_analyzer_inventory_reports_collection_receipt_coverage(tmp_path):
    completion = _completion()
    collected = build_evidence_collected_receipt(
        completion, identity=KEY.as_dict(), event_id="trade-1",
        provenance=PROVENANCE, collected_at=NOW,
    )["receipt"]
    rows = _terminal_rows() + [_row(
        "lifecycle", "lifecycle:trade-1:bundle-completion", terminal=True,
        observation_status="LIFECYCLE_BUNDLE_COMPLETE",
        bundle_completion=completion,
    ), _row(
        "lifecycle", "lifecycle:trade-1:evidence-collected", terminal=True,
        observation_status="EVIDENCE_COLLECTION_COMPLETE",
        evidence_collected_at=NOW, evidence_collection_receipt=collected,
    )]
    result = materialize_bundle(tmp_path, KEY, rows, now=NOW)
    assert result["written"] is True
    report = build_lifecycle_bundle_inventory(tmp_path)
    assert report["evidence_collection"] == {
        "schema": "lifecycle_evidence_collected_v1",
        "receipt_count": 1,
        "missing_count": 0,
        "coverage_complete": True,
    }
    index = build_lifecycle_evidence_index(tmp_path / "v3")
    assert index["valid_unique_count"] == 1
    joined = join_lifecycle_evidence(index, {
        "epoch_id": "epoch-1", "episode_id": "episode-1",
        "policy_signature": "policy-1", "research_lane": "CONTINUOUS",
    })
    assert joined["status"] == "VERIFIED"
    assert joined["receipt"]["evidence_collected_receipt_sha256"] == collected["evidence_collected_receipt_sha256"]


def test_analyzer_join_fails_closed_for_missing_duplicate_and_mismatch(tmp_path):
    missing = join_lifecycle_evidence({"by_key": {}}, {
        "epoch_id": "epoch-1", "episode_id": "episode-1",
        "policy_signature": "policy-1", "research_lane": "CONTINUOUS",
    })
    assert missing == {
        "status": "UNKNOWN",
        "reason_codes": ["UNKNOWN_LIFECYCLE_EVIDENCE_RECEIPT_MISSING"],
    }
    key = ("epoch-1", "episode-1", "policy-1", "CONTINUOUS")
    duplicate = join_lifecycle_evidence({"by_key": {key: [{"valid": True}, {"valid": True}]}}, {
        "epoch_id": "epoch-1", "episode_id": "episode-1",
        "policy_signature": "policy-1", "research_lane": "CONTINUOUS",
    })
    assert duplicate["reason_codes"] == ["UNKNOWN_LIFECYCLE_EVIDENCE_RECEIPT_DUPLICATE"]

    completion = _completion()
    collected = build_evidence_collected_receipt(
        completion, identity=KEY.as_dict(), event_id="trade-1",
        provenance=PROVENANCE, collected_at=NOW,
    )["receipt"]
    manifest = {
        "schema": "research_lifecycle_bundle_v1", "identity": KEY.as_dict(),
        "provenance": PROVENANCE, "completion": completion,
        "evidence_collection": {"ready": True, "receipt": collected},
    }
    tampered = copy.deepcopy(manifest)
    tampered["evidence_collection"]["receipt"]["event_id"] = "tampered"
    events = [{"bundle_completion": completion}]
    verified = verify_manifest_collection_receipt(tampered, events)
    assert verified["valid"] is False
    assert "EVIDENCE_COLLECTION_RECEIPT_SHA256_MISMATCH" in verified["blockers"]

    rebound = copy.deepcopy(manifest)
    rebound["evidence_collection"]["receipt"]["completion_receipt_sha256"] = "d" * 64
    receipt = rebound["evidence_collection"]["receipt"]
    material = dict(receipt)
    material.pop("evidence_collected_receipt_sha256")
    import hashlib
    from research_v3_contract import canonical_json
    receipt["evidence_collected_receipt_sha256"] = hashlib.sha256(
        canonical_json(material).encode("utf-8")
    ).hexdigest()
    verified = verify_manifest_collection_receipt(rebound, events)
    assert "EVIDENCE_COLLECTION_COMPLETION_BINDING_MISMATCH" in verified["blockers"]
