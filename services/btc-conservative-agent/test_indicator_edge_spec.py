"""Indicator Edge registry: validity, parity with the written spec, identity hash, engine health."""

import re
from pathlib import Path

import indicator_edge_spec as spec

SPEC_DOC = Path(__file__).resolve().parents[2] / "diagnostics" / "INDICATOR-EDGE-SPEC-20261004.md"


def test_registry_is_valid_and_has_52_numbered_indicators():
    assert spec.validate_spec() == []
    assert sorted(i["num"] for i in spec.INDICATORS) == list(range(1, 53))


def test_registry_matches_the_written_spec_appendix():
    rows = re.findall(r"^\| (\d+) \| ([A-Z0-9_]+) \|", SPEC_DOC.read_text(encoding="utf-8"), re.M)
    assert len(rows) == 52
    by_num = {i["num"]: i["id"] for i in spec.INDICATORS}
    assert {int(n): i for n, i in rows} == by_num


def test_feature_ids_are_unique_ordered_and_scored_subset_excludes_levels_and_regime_only():
    fids = spec.feature_ids()
    assert len(fids) == len(set(fids))
    assert all(re.fullmatch(r"[A-Z0-9_]+@[A-Z0-9]+:[A-Z]+", f) for f in fids)
    scored = spec.scored_feature_ids()
    unscored = {f.split("@")[0] for f in fids if f not in scored}
    assert unscored == {"ATR", "CHAIKIN_VOL", "FIB_EXT_4H", "VOL_EXPECTED"}
    assert spec.trial_count() == len(scored) * len(spec.SCORING_RULES["windows_min"])


def test_orientation_flips_reversion_and_divergence_only():
    assert spec.orientation("RSI@F:REVERSION") == -1
    assert spec.orientation("CVD@BFX:DIVERGENCE") == -1
    assert spec.orientation("RSI@F:TREND") == 1
    assert spec.orientation("BB@F:BREAKOUT") == 1


def test_feature_set_sha_is_stable_and_covers_scoring_rules():
    sha = spec.feature_set_sha()
    assert len(sha) == 64 and sha == spec.feature_set_sha()
    doc = spec.spec_document()
    assert doc["scoring_rules"]["round_trip_cost_bp"] == 2.0
    assert doc["scoring_rules"]["latencies_sec"] == [2, 9]
    assert doc["scoring_rules"]["windows_min"] == [3, 15, 60, 120]
    assert doc["trial_count"] == spec.trial_count()
    assert set(spec.REGIME_SPLITS) == {"vol_tercile", "trend_state", "session"}


def test_unavailable_inputs_are_declared():
    assert set(spec.UNAVAILABLE_INPUTS) == {"exchange_inflow_outflow", "dvol"}


def test_tile_package_rules_never_touch_the_registry():
    assert "never edits combo_pathway_config.py" in spec.TILE_PACKAGE_RULES["output"]
    assert "relay-ineligible" in spec.TILE_PACKAGE_RULES["registration"]
    assert "default OFF" in spec.TILE_PACKAGE_RULES["registration"]


def _live(now, **over):
    live = {"schema": spec.LIVE_SCHEMA, "written_ts": now - 2, "last_bar_ts": now - 200 - spec.BAR_SEC + 20,
            "feature_set_sha": spec.feature_set_sha(), "feature_set_version": spec.FEATURE_SET_VERSION,
            "boot_id": "ie-x", "history_bars": 900,
            "last_health": {"ok": True, "reasons": []},
            "stats": {"rows_written": 5, "write_failures": 0, "compute_failures": 0}}
    live.update(over)
    return live


def test_health_from_live_states():
    now = 1_791_000_000.0
    assert spec.health_from_live(None, now, enabled=False)["status"] == "DISABLED"
    assert spec.health_from_live(None, now)["status"] == "ENGINE_DOWN"
    assert spec.health_from_live(_live(now, written_ts=now - 120), now)["status"] == "ENGINE_DOWN"
    ok = spec.health_from_live(_live(now), now)
    assert ok["status"] == "OK" and ok["affects_orders"] is False and ok["feature_set_matches"] is True
    stalled = spec.health_from_live(_live(now, last_bar_ts=now - 3600), now)
    assert stalled["status"] == "STALLED" and stalled["last_bar_close_age_sec"] > spec.BAR_STALL_SEC
    assert spec.health_from_live(_live(now, last_bar_ts=None), now)["status"] == "STALLED"
    assert spec.health_from_live(_live(now, last_health={"ok": False, "reasons": ["TAPE_PARTIAL"]}),
                                 now)["status"] == "DEGRADED"
    assert spec.health_from_live(_live(now, feature_set_sha="0" * 64), now)["status"] == "DEGRADED"
    assert spec.health_from_live(_live(now, stats={"write_failures": 1}), now)["status"] == "DEGRADED"
