import gzip
import hashlib
import json

from research.research_v3_report import (
    EXHAUSTIVE_POLICY_FILE,
    EXHAUSTIVE_POLICY_MANIFEST_FILE,
    _exclude_identity_aliases,
    _persist_exhaustive_policies,
    _source_revision,
)


def _candidate(policy_id, signature, *, supported, full, no_fill, unknown, pnl=None):
    wins = 1 if pnl is not None and pnl > 0 else 0
    losses = 1 if pnl is not None and pnl < 0 else 0
    return {
        "policy_id": policy_id,
        "policy_signature": signature,
        "policy_family": "FIXED_TARGET",
        "policy_spec": {
            "entry": {
                "entry_policy_id": "OFFSET_0.10_CHASE_no_chase",
                "offset_pct": 0.10,
                "chase_id": "no_chase",
            }
        },
        "episodes_total": 10,
        "oos_episodes": 3,
        "supported_conservative_episodes": supported,
        "full_fills": full,
        "partial_fills": 0,
        "no_fills": no_fill,
        "unsupported_episodes": unknown,
        "conservative_fill_rate": full / supported if supported else None,
        "qualification": "DESCRIPTIVE_ONLY",
        "evidence_world": "CONSERVATIVE_BBO_DEPTH_V1",
        "gates": {"sealed_holdout_pass": False},
        "validation": {
            "risk": {
                "wins": wins,
                "losses": losses,
                "net_pnl_usd": pnl,
                "max_drawdown_usd": pnl if pnl is not None and pnl < 0 else 0,
                "cvar95_usd": pnl,
                "longest_loss_streak": losses,
            }
        },
        "ideal_touch_diagnostic": {
            "touches": 1,
            "no_touches": 2,
            "wins": wins,
            "losses": losses,
            "oos_net_usd": pnl,
        },
    }


def test_exhaustive_artifact_retains_losing_and_unsupported_non_shortlisted_rows(tmp_path):
    losing = _candidate(
        "OFFSET_0.10_CHASE_no_chase|ATR_TP_2.5",
        "policy-losing",
        supported=3,
        full=1,
        no_fill=2,
        unknown=0,
        pnl=-0.25,
    )
    unsupported = _candidate(
        "OFFSET_0.10_CHASE_no_chase|CHANDELIER_3",
        "policy-unsupported",
        supported=0,
        full=0,
        no_fill=0,
        unknown=3,
    )
    manifest = _persist_exhaustive_policies(
        tmp_path,
        [unsupported, losing, dict(losing)],
        epoch_id="epoch-test",
        source_revision="rev-test",
        analyzer_generation_revision="analyzer-rev-test",
        tile_config_signature="tiles-test",
    )

    artifact = tmp_path / EXHAUSTIVE_POLICY_FILE
    rows = [json.loads(line) for line in gzip.open(artifact, "rt", encoding="utf-8")]
    assert manifest["row_count"] == 2
    assert manifest["analyzer_generation_revision"] == "analyzer-rev-test"
    assert manifest["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert json.loads((tmp_path / EXHAUSTIVE_POLICY_MANIFEST_FILE).read_text())["row_count"] == 2
    by_id = {row["policy_id"]: row for row in rows}
    assert by_id[losing["policy_id"]]["net_pnl_usd"] == -0.25
    assert by_id[losing["policy_id"]]["ev_per_independent_episode_usd"] == -0.08333333
    assert by_id[losing["policy_id"]]["funding_cost_usd"] is None
    assert "FUNDING_SLIPPAGE_FEE_AGGREGATES_UNAVAILABLE" in by_id[losing["policy_id"]]["unavailable_reasons"]
    assert by_id[unsupported["policy_id"]]["unknown_count"] == 3
    assert by_id[unsupported["policy_id"]]["net_pnl_usd"] is None
    assert "NO_SUPPORTED_CONSERVATIVE_EPISODES" in by_id[unsupported["policy_id"]]["unavailable_reasons"]


def test_shared_call_opportunity_aliases_are_not_counted_as_independent_siblings():
    opportunities = [
        {
            "episode_id": "episode-canonical",
            "shared_ai_call_id": "scan-one",
            "grouping_basis": "SHARED_AI_CALL",
            "signal_ts": 10,
            "symbol": "BTCUSD",
            "raw_direction": "LONG",
        },
        {
            "episode_id": "episode-fallback-alias",
            "shared_ai_call_id": "scan-one",
            "grouping_basis": "EPISODE_ID_FALLBACK",
            "signal_ts": 10,
            "symbol": "BTCUSD",
            "raw_direction": "LONG",
        },
    ]
    kept, excluded = _exclude_identity_aliases(opportunities)
    assert [row["episode_id"] for row in kept] == ["episode-canonical"]
    assert [row["episode_id"] for row in excluded] == ["episode-fallback-alias"]


def test_source_revision_expands_only_matching_canonical_prefix(tmp_path, monkeypatch):
    (tmp_path / "canonical_dataset_current.json").write_text(
        json.dumps({"source_revision": "abc123def456"}), encoding="utf-8"
    )
    monkeypatch.setenv("SOURCE_GIT_REV", "abc123def4567890")
    assert _source_revision(tmp_path) == "abc123def4567890"
    monkeypatch.setenv("SOURCE_GIT_REV", "ffffffffff000000")
    assert _source_revision(tmp_path) == "abc123def456"
