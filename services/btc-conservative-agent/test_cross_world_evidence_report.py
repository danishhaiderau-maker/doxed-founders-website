import json
import math
import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research.cross_world_evidence import (  # noqa: E402
    REQUIRED_CAUSAL_IDENTITIES,
    WORLD_ORDER,
    build_cross_world_evidence_report,
)


def identity(**overrides):
    values = {
        "epoch_id": "epoch-1",
        "opportunity_id": "opportunity-1",
        "policy_signature": "policy-1",
        "schedule_id": "schedule-1",
        "tape_id": "tape-1",
        "fill_id": "fill-1",
    }
    values.update(overrides)
    return values


def report(world_rows):
    return build_cross_world_evidence_report(
        world_rows,
        generated_at="2026-08-26T00:00:00+00:00",
        source_revision="a" * 40,
        epoch_id="epoch-1",
    )


def test_missing_identity_is_not_computable_and_never_fuzzy_joined():
    left = {**identity(), "fill_state": "FILL", "net_pnl_usd": 1}
    right = {
        **identity(), "fill_state": "FILL", "net_pnl_usd": 1,
        "trade_id": "same-trade", "timestamp": 100, "price": 50,
    }
    right.pop("schedule_id")
    result = report({
        "IDEAL_TOUCH_DIAGNOSTIC": [left],
        "CONSERVATIVE_BBO_DEPTH": [right],
    })
    pair = result["pairwise"][0]
    assert pair["status"] == "NOT_COMPUTABLE"
    assert pair["explicit_identity_matches"] == 0
    assert result["join_summary"]["pairwise_computable_comparisons"] == 0
    assert result["worlds"]["CONSERVATIVE_BBO_DEPTH"]["missing_identity_counts"] == {
        "schedule_id": 1
    }
    assert "timestamp_proximity" in result["join_contract"]["prohibited_fallbacks"]


def test_csv_nan_is_missing_identity_not_a_manufactured_duplicate_key():
    missing = {**identity(), "fill_state": "FILL"}
    missing["epoch_id"] = math.nan
    result = report({"OBSERVED_PAPER": [missing, dict(missing)]})
    world = result["worlds"]["OBSERVED_PAPER"]
    assert world["rows_observed"] == 2
    assert world["rows_with_complete_explicit_identity"] == 0
    assert world["unique_joinable_rows"] == 0
    assert world["ambiguous_duplicate_identity_rows"] == 0
    assert world["missing_identity_counts"] == {"epoch_id": 2}


def test_complete_explicit_identity_exposes_agreement_and_disagreement():
    rows = {}
    for world in WORLD_ORDER:
        rows[world] = [{
            **identity(),
            "fill_state": "FILL",
            "direction": "LONG",
            "net_pnl_usd": -1 if world == "SHADOW_COUNTERFACTUAL" else 1,
        }]
    result = report(rows)
    summary = result["join_summary"]
    assert summary == {
        "distinct_pairwise_joined_identities": 1,
        "all_five_world_identity_matches": 1,
        "pairwise_computable_comparisons": 10,
        "pairwise_agreements": 6,
        "pairwise_disagreements": 4,
        "status": "COMPUTABLE",
    }
    shadow_pairs = [
        row for row in result["joined_rows"]
        if "SHADOW_COUNTERFACTUAL" in {row["left_world"], row["right_world"]}
    ]
    assert len(shadow_pairs) == 4
    assert all(row["status"] == "DISAGREE" for row in shadow_pairs)
    assert all(row["disagreement_fields"] == ["pnl_sign"] for row in shadow_pairs)
    assert result["qualification_effect"] == "NONE"
    assert result["live_policy_change_allowed"] is False


def test_duplicate_full_identity_is_ambiguous_not_arbitrarily_deduped():
    duplicate = {**identity(), "outcome": "FILL"}
    result = report({
        "IDEAL_TOUCH_DIAGNOSTIC": [duplicate, dict(duplicate)],
        "CONSERVATIVE_BBO_DEPTH": [duplicate],
    })
    assert result["worlds"]["IDEAL_TOUCH_DIAGNOSTIC"]["unique_joinable_rows"] == 0
    assert result["worlds"]["IDEAL_TOUCH_DIAGNOSTIC"]["ambiguous_duplicate_identity_rows"] == 2
    assert any(
        row["reason"] == "AMBIGUOUS_DUPLICATE_CAUSAL_IDENTITY"
        for row in result["not_computable"]
    )


def test_diagnostic_touch_agrees_with_fill_without_being_relabelled_as_fill():
    result = report({
        "IDEAL_TOUCH_DIAGNOSTIC": [{**identity(), "touched_limit": True}],
        "CONSERVATIVE_BBO_DEPTH": [{**identity(), "outcome": "FILL"}],
    })
    row = result["joined_rows"][0]
    assert row["status"] == "AGREE"
    assert row["left"]["entry_observed"] is True
    assert row["left"]["evidence_state"] is None
    assert row["right"]["entry_observed"] is True
    assert row["right"]["evidence_state"] == "FILL"


def test_nested_causal_identity_is_accepted_but_aliases_are_not():
    nested = {"causal_identity": identity(), "filled": True}
    aliases = {
        "collection_epoch_id": "epoch-1",
        "episode_id": "opportunity-1",
        "policy_signature": "policy-1",
        "chase_schedule_id": "schedule-1",
        "market_path_id": "tape-1",
        "fill_ids": ["fill-1"],
        "filled": True,
    }
    result = report({
        "IDEAL_TOUCH_DIAGNOSTIC": [nested],
        "CONSERVATIVE_BBO_DEPTH": [aliases],
    })
    assert result["worlds"]["IDEAL_TOUCH_DIAGNOSTIC"]["unique_joinable_rows"] == 1
    assert result["worlds"]["CONSERVATIVE_BBO_DEPTH"]["unique_joinable_rows"] == 0
    missing = result["worlds"]["CONSERVATIVE_BBO_DEPTH"]["missing_identity_counts"]
    assert set(missing) == set(REQUIRED_CAUSAL_IDENTITIES) - {"policy_signature"}


def test_engine_publishes_report_atomically_and_manifest_catalog_owns_it(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cross_world_engine_under_test", ROOT / "analyzer_research_engine_v62.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)

    full = {**identity(), "fill_state": "FILL", "net_pnl_usd": 1}
    reports = {
        engine.SAFE_POLICY_GENOME_V3_REPORT_FILE: {"ideal_touch_receipts": [full]},
        engine.CONSERVATIVE_FILL_DESCRIPTIVE_REPORT_FILE: {"receipts": [full]},
    }
    monkeypatch.setattr(engine, "_load_json_report", lambda name: reports.get(name, {}))
    loaded_jsonl = []
    def load_jsonl(name):
        loaded_jsonl.append(name)
        return [full]
    monkeypatch.setattr(engine, "_load_jsonl_rows", load_jsonl)
    monkeypatch.setattr(engine, "_cross_world_bitfinex_rows", lambda: [full])
    monkeypatch.setattr(engine, "_fresh_epoch_provenance", lambda: {"fresh_epoch_id": "epoch-1"})
    monkeypatch.setattr(engine, "robust_read_csv", lambda *_args: __import__("pandas").DataFrame([full]))
    monkeypatch.setattr(engine, "analyzer_report_path", lambda name: str(tmp_path / name))
    monkeypatch.setenv("SOURCE_GIT_REV", "b" * 40)

    payload = engine.cross_world_evidence_report()
    target = tmp_path / engine.CROSS_WORLD_EVIDENCE_REPORT_FILE
    assert target.is_file()
    assert not (tmp_path / f"{engine.CROSS_WORLD_EVIDENCE_REPORT_FILE}.tmp").exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert engine.CROSS_WORLD_EVIDENCE_REPORT_FILE in engine.ANALYZER_JSON_REPORT_FILES
    assert any(
        row[1] == engine.CROSS_WORLD_EVIDENCE_REPORT_FILE
        for row in engine.DEEP_DIVE_REPORT_CATALOG
    )
    assert engine.SHADOW_OUTCOME_FILE in loaded_jsonl
    assert engine.SHADOW_LANE_OUTCOME_FILE in loaded_jsonl
    assert engine.COUNTERFACTUAL_FILE in loaded_jsonl
    assert payload["source_inventory"]["shadow_counterfactual"] == [
        engine.SHADOW_OUTCOME_FILE,
        engine.SHADOW_LANE_OUTCOME_FILE,
        engine.COUNTERFACTUAL_FILE,
    ]


def test_cross_world_page_uses_canonical_world_inventory_fields():
    source = (ROOT / "research" / "research_dashboard.py").read_text(encoding="utf-8")
    assert "w.rows_observed??0" in source
    assert "w.rows_with_complete_explicit_identity??0" in source
    assert "w.unique_joinable_rows??0" in source
    assert "w.rows??w.row_count??0" not in source
