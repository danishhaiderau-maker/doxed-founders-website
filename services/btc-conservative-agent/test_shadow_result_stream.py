import copy
import gzip
import hashlib
import json

import pytest

from research.shadow_result_stream import ShadowResultStreamWriter, verify_result_stream, digest, RANGE
from research.discovery_scorecard_publication import build_discovery_scorecard_publication
from test_discovery_scorecard_publication import GENERATION, inputs, shadow_inputs


def complete_report(root, count=121):
    baseline, report = shadow_inputs(root)
    template = report["results"][0]
    rows = []
    for i in range(count):
        row = copy.deepcopy(template)
        signature = f"composite-{i:04}"
        row.update(policy_signature=signature, composite_policy_signature=signature)
        row["terminal"]["policy_signature"] = signature
        row["terminal"]["receipt_sha256"] = digest({k: v for k, v in row["terminal"].items() if k != "receipt_sha256"})
        rows.append(row)
    report.update(results=rows, candidate_replay_count=count, complete_replay_count=count,
                  unknown_replay_count=0, results_total=count, results_truncated=False,
                  status="BUILT", blockers=[], candidate_policy_count=count,
                  candidate_artifact_sha256="c" * 64)
    return baseline, report


def attach(root, report, rows, name="shadow.jsonl.gz"):
    with ShadowResultStreamWriter(root, name, GENERATION) as sink:
        for row in rows:
            sink(row)
        report["result_stream"] = sink.finalize(report)
    return report


def test_full_stream_over_100_matches_uncapped_discovery(tmp_path):
    root, evaluator, _ = inputs(tmp_path)
    baseline, full = complete_report(root)
    reference = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
        evaluator_status=evaluator, baseline_report=baseline, shadow_terminal_report=full)
    capped = copy.deepcopy(full)
    capped.update(results=full["results"][:100], results_truncated=True,
                  status="BUILT_INCOMPLETE", blockers=["RESULT_STREAM_CONSUMER_NOT_BOUND"])
    attach(root, capped, full["results"])
    actual = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
        evaluator_status=evaluator, baseline_report=baseline, shadow_terminal_report=capped)
    assert actual["input_counts"] == reference["input_counts"]
    assert actual["scorecard"] == reference["scorecard"]
    assert actual["shadow_terminal_aggregate"]["full_result_stream_verified"] is True
    assert actual["shadow_terminal_verified_stream"]["complete_replay_count"] == 121
    assert actual["shadow_terminal_provenance_truncated"] is True
    assert actual["live_qualification"] is False


@pytest.mark.parametrize("fault", ["missing", "corrupt", "truncate", "generation", "report", "count", "incomplete"])
def test_bad_stream_never_consumed(tmp_path, fault):
    root, evaluator, _ = inputs(tmp_path)
    baseline, report = complete_report(root, 2)
    attach(root, report, report["results"])
    path = root / "shadow.jsonl.gz"
    if fault == "missing":
        path.unlink()
    elif fault == "corrupt":
        data = bytearray(path.read_bytes()); data[len(data)//2] ^= 1; path.write_bytes(data)
    elif fault == "truncate":
        path.write_bytes(path.read_bytes()[:-8])
    elif fault == "generation":
        report["result_stream"]["generation"]["generation_key"] = "other"
    elif fault == "report":
        report["reason_counts"] = {"invented": 1}
    elif fault == "count":
        report["result_stream"]["complete_replay_count"] += 1
    else:
        report["result_stream"]["complete"] = False
    result = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
        evaluator_status=evaluator, baseline_report=baseline, shadow_terminal_report=report)
    assert result["status"] == "UNKNOWN"
    assert result["scorecard"] is None


def unknown_report(n=231770000, candidates=21070):
    return {"generation": dict(GENERATION), "candidate_replay_count": n,
            "complete_replay_count": 0, "unknown_replay_count": n,
            "candidate_policy_count": candidates, "candidate_artifact_sha256": "c" * 64,
            "reason_counts": {"MODEL_MISSING": n}}


def unknown_range(i, candidates=21070):
    return {"schema": RANGE, "episode_id": f"e{i}", "opportunity_id": f"o{i}",
            "baseline_id": "b", "candidate_count": candidates, "candidate_artifact_sha256": "c" * 64,
            "status": "UNKNOWN", "blockers": ["MODEL_MISSING"]}


def test_231_million_unknowns_stay_ranges_and_exact(tmp_path):
    report = unknown_report()
    attach(tmp_path, report, (unknown_range(i) for i in range(11000)))
    with verify_result_stream(tmp_path, report, GENERATION) as index:
        assert len(index) == 0
        assert index.verified_summary["unknown_replay_count"] == 231770000
        assert index.verified_summary["range_record_count"] == 11000
    assert (tmp_path / "shadow.jsonl.gz").stat().st_size < 200000


@pytest.mark.parametrize("fault", ["duplicate", "wrong_artifact", "wrong_size", "empty_reason", "row_overlap"])
def test_ranges_fail_closed(tmp_path, fault):
    report = unknown_report(21070)
    row = unknown_range(1)
    rows = [row]
    if fault == "duplicate": rows.append(row)
    elif fault == "wrong_artifact": row["candidate_artifact_sha256"] = "d" * 64
    elif fault == "wrong_size": row["candidate_count"] -= 1
    elif fault == "empty_reason": row["blockers"] = []
    else: rows.append({"episode_id": "e1", "opportunity_id": "o1", "baseline_id": "b", "status": "UNKNOWN"})
    attach(tmp_path, report, rows)
    with pytest.raises(ValueError):
        verify_result_stream(tmp_path, report, GENERATION)


def test_stream_explicit_budget_never_samples(tmp_path):
    report = unknown_report(21070)
    attach(tmp_path, report, [unknown_range(1)])
    with pytest.raises(ValueError):
        verify_result_stream(tmp_path, report, GENERATION, max_bytes=10)


def test_disk_scorecard_exact_matched_episodes_and_duplicates():
    from discovery_cohort_scorecard import build_episode_matched_scorecard, build_disk_episode_matched_scorecard
    from test_discovery_cohort_scorecard import row
    rows = []
    for i in range(130):
        for world in ("OBSERVED_PAPER", "CONSERVATIVE_BBO"):
            item = row(world, f"e{i}")
            rows.append(item)
    assert build_disk_episode_matched_scorecard(rows) == build_episode_matched_scorecard(rows)
    rows.append(rows[0])
    assert build_disk_episode_matched_scorecard(rows) == build_episode_matched_scorecard(rows)
    with pytest.raises(ValueError, match="OUTPUT_BUDGET"):
        build_disk_episode_matched_scorecard(rows, max_output_bytes=10)


def test_explicit_separate_stream_publication_root(tmp_path):
    root, evaluator, _ = inputs(tmp_path)
    baseline, report = complete_report(root, 2)
    publication = tmp_path / "publication"
    attach(publication, report, report["results"])
    kwargs = dict(expected_generation=GENERATION, evaluator_status=evaluator,
                  baseline_report=baseline, shadow_terminal_report=report)
    assert build_discovery_scorecard_publication(root, **kwargs)["status"] == "UNKNOWN"
    assert build_discovery_scorecard_publication(root, stream_artifact_root=tmp_path / "wrong", **kwargs)["status"] == "UNKNOWN"
    actual = build_discovery_scorecard_publication(root, stream_artifact_root=publication, **kwargs)
    assert actual["shadow_terminal_verified_stream"]["complete_replay_count"] == 2


def test_valid_hash_truncated_jsonl_still_rejected(tmp_path):
    report = unknown_report(21070)
    attach(tmp_path, report, [unknown_range(1)])
    path = tmp_path / "shadow.jsonl.gz"
    payload = gzip.decompress(path.read_bytes())[:-1]
    path.write_bytes(gzip.compress(payload))
    receipt = report["result_stream"]
    receipt.update(artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        compressed_bytes=path.stat().st_size, uncompressed_sha256=hashlib.sha256(payload).hexdigest(),
        uncompressed_bytes=len(payload))
    receipt["receipt_sha256"] = digest({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    with pytest.raises(ValueError, match="LINE_OR_BYTE_LIMIT"):
        verify_result_stream(tmp_path, report, GENERATION)


def test_swapped_generation_even_with_resigned_receipt_rejected(tmp_path):
    report = unknown_report(21070)
    attach(tmp_path, report, [unknown_range(1)])
    receipt = report["result_stream"]
    receipt["generation"]["generation_key"] = "another"
    receipt["receipt_sha256"] = digest({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    with pytest.raises(ValueError, match="GENERATION_MISMATCH"):
        verify_result_stream(tmp_path, report, GENERATION)


def test_disk_scorecard_preserves_tied_leader_world_order():
    from discovery_cohort_scorecard import build_episode_matched_scorecard, build_disk_episode_matched_scorecard
    from test_discovery_cohort_scorecard import row
    rows = [row("OBSERVED_PAPER", "e1"), row("CONSERVATIVE_BBO", "e1"), row("CONSERVATIVE_BBO", "e1")]
    rows[1].update(policy_id="other", policy_signature="other")
    assert build_disk_episode_matched_scorecard(rows) == build_episode_matched_scorecard(rows)


def test_actual_sqlite_long_key_growth_cannot_exceed_disk_budget(tmp_path):
    from research.shadow_result_stream import DiskRows
    with DiskRows(max_bytes=100000, temp_root=tmp_path) as index:
        with pytest.raises(ValueError, match="INDEX_BUDGET_EXCEEDED"):
            for i in range(3):
                index.append({"x": 1}, "k" * 8000 + str(i), "p" * 8000 + str(i))
        # This exact fixture formerly estimated 48,795 bytes while SQLite used
        # 114,688 bytes. Check the real allocation, not the estimate.
        assert index.db.execute("pragma max_page_count").fetchone()[0] == 24
        assert index.db.execute("pragma page_count").fetchone()[0] * 4096 <= 100000
        assert sum(p.stat().st_size for p in index.path.parent.iterdir()) <= 100000
        assert index.db.execute("pragma journal_mode").fetchone()[0] == "off"
        assert index.db.execute("pragma temp_store").fetchone()[0] == 2
    assert not list(tmp_path.iterdir())


def test_three_default_indexes_share_one_total_disk_ceiling():
    from research.shadow_result_stream import DiskRows, MAX_BYTES
    with DiskRows() as first, DiskRows() as second, DiskRows() as third:
        total = sum(item.db.execute("pragma max_page_count").fetchone()[0]
                    * item.db.execute("pragma page_size").fetchone()[0]
                    for item in (first, second, third))
        assert total <= MAX_BYTES


def test_discovery_shared_index_budget_fails_unknown_without_sampling(tmp_path):
    root, evaluator, _ = inputs(tmp_path)
    baseline, report = complete_report(root, 121)
    attach(root, report, report["results"])
    result = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
        evaluator_status=evaluator, baseline_report=baseline, shadow_terminal_report=report,
        index_budget_bytes=3 * 16384)
    assert result["status"] == "UNKNOWN"
    assert result["scorecard"] is None
    assert any("BUDGET" in reason for reason in result["blockers"])


def test_too_small_index_budget_cleans_owned_directory(tmp_path):
    from research.shadow_result_stream import DiskRows
    with pytest.raises(ValueError, match="INDEX_BUDGET"):
        DiskRows(max_bytes=4096, temp_root=tmp_path)
    assert not list(tmp_path.iterdir())


def test_group_queries_need_no_temporary_sort_and_bound_materialized_rows():
    from research.shadow_result_stream import DiskRows, GROUP_FIRST_SEEN_SQL, GROUP_SORTED_SQL, GROUP_ROWS_SQL
    with DiskRows() as index:
        for key in ("b", "a", "b", "c"):
            index.append({"key": key}, key)
        for statement, bindings in ((GROUP_FIRST_SEEN_SQL, ()), (GROUP_SORTED_SQL, ()),
                                    (GROUP_ROWS_SQL, ("b",))):
            assert not any("TEMP B-TREE" in row[-1].upper()
                           for row in index.db.execute("explain query plan " + statement, bindings))
        assert [key for key, _ in index.groups(insertion_order=True)] == ["b", "a", "c"]
        with pytest.raises(ValueError, match="GROUP_BUDGET"):
            list(index.groups(max_group_rows=1))


def test_index_oversized_keys_fail_explicitly():
    from research.shadow_result_stream import DiskRows, MAX_INDEX_KEY_BYTES
    with DiskRows() as index:
        with pytest.raises(ValueError, match="ROW_BUDGET"):
            index.append({}, "k" * (MAX_INDEX_KEY_BYTES + 1))


def test_full_stream_declared_provenance_matches_unsampled_reference(tmp_path):
    root, evaluator, _ = inputs(tmp_path)
    baseline, report = complete_report(root, 121)
    for row in report["results"]:
        row["terminal"].update(economics_evidence_basis="DECLARED_SIMULATION",
                               declared_contract_sha256="e" * 64)
        row["terminal"]["receipt_sha256"] = digest({k: v for k, v in row["terminal"].items() if k != "receipt_sha256"})
    kwargs = dict(expected_generation=GENERATION, evaluator_status=evaluator, baseline_report=baseline)
    reference = build_discovery_scorecard_publication(root, shadow_terminal_report=report, **kwargs)
    streamed = copy.deepcopy(report)
    streamed.update(results=report["results"][:100], results_truncated=True)
    attach(root, streamed, report["results"])
    actual = build_discovery_scorecard_publication(root, shadow_terminal_report=streamed, **kwargs)
    assert actual["scorecard"] == reference["scorecard"]
    assert actual["warnings"] == reference["warnings"]
    assert actual["input_counts"] == reference["input_counts"]


def test_disk_scorecard_mixed_assumptions_matches_reference():
    from discovery_cohort_scorecard import build_episode_matched_scorecard, build_disk_episode_matched_scorecard
    from test_discovery_cohort_scorecard import row
    rows = [row("CONSERVATIVE_BBO", f"e{i}", economics_evidence_basis="DECLARED_SIMULATION",
                declared_contract_sha256=letter * 64) for i, letter in enumerate(("a", "b"))]
    assert build_disk_episode_matched_scorecard(rows) == build_episode_matched_scorecard(rows)
