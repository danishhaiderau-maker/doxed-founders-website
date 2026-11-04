"""Strategy-package loader refuses (R47) + HM HOLD 75d49e057f60 fixes.

Named refuses: bad spec, missing series_stamp, paper_only=false, credential
field, invalid evidence status. Plus: OOS/FWD HOLDS+leak_suspect, hyphen ids,
optional cooldown, soft deps, empty-dir skip schema, numeric-bars gate.
"""
from __future__ import annotations

import copy
from pathlib import Path
from unittest import mock

import pytest
import yaml

import strategy_package_loader as spl

HERE = Path(__file__).resolve().parent
SCHEMA = spl.load_schema()


def _valid_pkg(**overrides) -> dict:
    base = {
        "id": "example_fade",
        "name": "Example fade",
        "thesis": "Fixture package for loader refuses — never activated.",
        "series_stamp": "series-ce-20261004-v31-freeze21b-fixture",
        "kind": "single",
        "members": ["example_fade"],
        "entry": {
            "signal": "FIXTURE_SIGNAL",
            "params": {"lookback_min": 15},
            "cooldown_min": 15,
        },
        "exit": {
            "composite": ["HARD_STOP", "TIME_EXIT"],
            "thesis_cut": False,
            "hard_stop_pct": 0.4,
            "tp": None,
            "max_hold_min": 60,
        },
        "regime_filter": {"name": "NONE", "params": {}},
        "sizing": {"margin_usd": 0.25, "leverage": 100},
        "daily_loss_cap_usd": 10.0,
        "costs": "REALISTIC_V1",
        "data_requires": ["book10", "trades"],
        "evidence": {
            "status": "LEAD",
            "window": "fixture",
            "oos_22m": "PENDING",
            "combos_searched": 0,
            "fdr": None,
            "leak_suspect": None,
        },
        "paper_only": True,
    }
    base.update(overrides)
    return base


def _oos_holds_pkg(**overrides) -> dict:
    pkg = _valid_pkg()
    pkg["evidence"] = {
        "status": "OOS_PASS",
        "window": "fixture-oos",
        "oos_22m": "HOLDS",
        "leak_suspect": False,
        "in_window": True,
        "bh_fdr_pass": True,
        "bonferroni_pass": True,
        "reality_check_p": 0.04,
        "deflated_sharpe": 0.1,
        "pbo": 0.2,
        "funding_sensitive": False,
        "combos_searched": 10,
        "fdr": 0.05,
    }
    pkg.update(overrides)
    return pkg


def test_bad_spec_refused():
    bad = _valid_pkg()
    del bad["exit"]
    with pytest.raises(spl.StrategyPackageRefuse, match="bad spec|exit"):
        spl.validate_package_dict(bad, source="bad_spec", schema=SCHEMA)
    bad2 = _valid_pkg()
    bad2["sizing"] = {"margin_usd": 1.0, "leverage": 100}
    with pytest.raises(spl.StrategyPackageRefuse, match="bad spec"):
        spl.validate_package_dict(bad2, source="bad_margin", schema=SCHEMA)


def test_missing_series_stamp_refused():
    pkg = _valid_pkg()
    del pkg["series_stamp"]
    with pytest.raises(spl.StrategyPackageRefuse, match="series_stamp"):
        spl.validate_package_dict(pkg, source="missing_stamp", schema=SCHEMA)
    pkg2 = _valid_pkg(series_stamp="   ")
    with pytest.raises(spl.StrategyPackageRefuse, match="series_stamp"):
        spl.validate_package_dict(pkg2, source="blank_stamp", schema=SCHEMA)


def test_paper_only_false_refused():
    pkg = _valid_pkg(paper_only=False)
    with pytest.raises(spl.StrategyPackageRefuse, match="paper_only"):
        spl.validate_package_dict(pkg, source="paper_only_false", schema=SCHEMA)
    pkg2 = _valid_pkg()
    pkg2["paper_only"] = "true"
    with pytest.raises(spl.StrategyPackageRefuse, match="paper_only"):
        spl.validate_package_dict(pkg2, source="paper_only_string", schema=SCHEMA)


def test_credential_field_present_refused():
    pkg = _valid_pkg()
    pkg["api_key"] = "should-never-load"
    with pytest.raises(spl.StrategyPackageRefuse, match="credential|api_key"):
        spl.validate_package_dict(pkg, source="cred_top", schema=SCHEMA)
    nested = _valid_pkg()
    nested["entry"] = copy.deepcopy(nested["entry"])
    nested["entry"]["params"] = {"bitfinex_api_secret": "nope"}
    with pytest.raises(spl.StrategyPackageRefuse, match="credential|bitfinex"):
        spl.validate_package_dict(nested, source="cred_nested", schema=SCHEMA)


def test_invalid_evidence_status_refused():
    pkg = _valid_pkg()
    pkg["evidence"] = dict(pkg["evidence"])
    pkg["evidence"]["status"] = "PASS"
    with pytest.raises(spl.StrategyPackageRefuse, match="evidence.status"):
        spl.validate_package_dict(pkg, source="bad_evidence", schema=SCHEMA)


def test_oos_fwd_require_holds_and_leak_suspect_false():
    no_holds = _oos_holds_pkg()
    no_holds["evidence"] = dict(no_holds["evidence"])
    no_holds["evidence"]["oos_22m"] = "FAILS"
    with pytest.raises(spl.StrategyPackageRefuse, match="HOLDS"):
        spl.validate_package_dict(no_holds, source="no_holds", schema=SCHEMA)

    leak = _oos_holds_pkg()
    leak["evidence"] = dict(leak["evidence"])
    leak["evidence"]["leak_suspect"] = True
    with pytest.raises(spl.StrategyPackageRefuse, match="leak_suspect"):
        spl.validate_package_dict(leak, source="leak", schema=SCHEMA)

    missing_leak = _oos_holds_pkg()
    missing_leak["evidence"] = dict(missing_leak["evidence"])
    del missing_leak["evidence"]["leak_suspect"]
    with pytest.raises(spl.StrategyPackageRefuse, match="leak_suspect"):
        spl.validate_package_dict(missing_leak, source="missing_leak", schema=SCHEMA)

    ok = spl.validate_package_dict(_oos_holds_pkg(), source="oos_ok", schema=SCHEMA)
    assert ok["evidence"]["oos_22m"] == "HOLDS"
    assert ok["evidence"]["leak_suspect"] is False


def test_fwd_pass_only_feeds_r53c_gate():
    lead = spl.validate_package_dict(_valid_pkg(), source="lead", schema=SCHEMA)
    assert spl.feeds_r53c_gate(lead) is False
    with pytest.raises(spl.StrategyPackageRefuse, match="R53\\(c\\)"):
        spl.assert_r53c_eligible(lead, source="lead")

    oos = spl.validate_package_dict(_oos_holds_pkg(), source="oos", schema=SCHEMA)
    assert spl.feeds_r53c_gate(oos) is False

    fwd = _oos_holds_pkg()
    fwd["evidence"] = dict(fwd["evidence"])
    fwd["evidence"]["status"] = "FWD_PASS"
    fwd_pkg = spl.validate_package_dict(fwd, source="fwd", schema=SCHEMA)
    assert spl.feeds_r53c_gate(fwd_pkg) is True
    spl.assert_r53c_eligible(fwd_pkg, source="fwd")


def test_hyphenated_id_and_optional_cooldown_accepted():
    """Strategist §8: hyphenated ids (ext2-lead-XX); cooldown_min optional."""
    pkg = _valid_pkg(id="ext2-lead-01", name="EXT2 lead 01")
    del pkg["entry"]["cooldown_min"]
    loaded = spl.validate_package_dict(pkg, source="hyphen", schema=SCHEMA)
    assert loaded["id"] == "ext2-lead-01"
    assert "cooldown_min" not in loaded["entry"]


def test_strategist_evidence_fields_accepted():
    pkg = _oos_holds_pkg()
    loaded = spl.validate_package_dict(pkg, source="§8-evidence", schema=SCHEMA)
    for key in (
        "in_window", "oos_22m", "bh_fdr_pass", "bonferroni_pass",
        "reality_check_p", "deflated_sharpe", "pbo", "funding_sensitive",
        "leak_suspect",
    ):
        assert key in loaded["evidence"]


def test_valid_package_loads_but_is_not_activated(tmp_path: Path):
    pkg = _valid_pkg()
    path = tmp_path / "example_fade.yaml"
    path.write_text(yaml.safe_dump(pkg, sort_keys=False), encoding="utf-8")
    loaded = spl.load_package_file(path, schema=SCHEMA)
    assert loaded["id"] == "example_fade"
    assert loaded["paper_only"] is True
    assert loaded["_activated"] is False


def test_dashboard_auto_renders_one_tile_per_registered_package(tmp_path: Path):
    a = _valid_pkg(id="pkg_a", name="Package A")
    b = _oos_holds_pkg(id="pkg_b", name="Package B")
    b["evidence"] = dict(b["evidence"])
    b["evidence"]["status"] = "FWD_PASS"
    (tmp_path / "pkg_a.yaml").write_text(yaml.safe_dump(a, sort_keys=False), encoding="utf-8")
    (tmp_path / "pkg_b.yaml").write_text(yaml.safe_dump(b, sort_keys=False), encoding="utf-8")
    (tmp_path / "_ignored.yaml").write_text("id: nope\n", encoding="utf-8")
    tiles = spl.build_dashboard_tile_specs(
        strategies_dir=tmp_path, enforce_numeric_bars_gate=False,
    )
    assert [t["strategy_package_id"] for t in tiles] == ["pkg_a", "pkg_b"]
    assert all(t["strategy_package_activated"] is False for t in tiles)
    assert tiles[0]["feeds_r53c"] is False
    assert tiles[1]["feeds_r53c"] is True


def test_repo_strategies_dir_has_no_activated_packages():
    paths = spl.iter_strategy_yaml_paths()
    assert paths == [], f"unexpected packages: {paths}"
    tiles = spl.build_dashboard_tile_specs()
    assert tiles == []


def test_numeric_bars_gate_blocks_repo_yaml(tmp_path: Path):
    (tmp_path / "blocked.yaml").write_text(
        yaml.safe_dump(_valid_pkg(), sort_keys=False), encoding="utf-8",
    )
    with pytest.raises(spl.StrategyPackageRefuse, match="numeric bars"):
        spl.load_registered_packages(tmp_path, enforce_numeric_bars_gate=True)
    assert spl.NUMERIC_BARS_FOLLOWUP_REQUIRED is True


def test_missing_deps_returns_no_package_tiles(tmp_path: Path):
    (tmp_path / "pkg.yaml").write_text(
        yaml.safe_dump(_valid_pkg(), sort_keys=False), encoding="utf-8",
    )
    with mock.patch.object(spl, "_yaml", None), mock.patch.object(spl, "_jsonschema", None):
        assert spl.deps_available() is False
        assert spl.missing_deps() == ("PyYAML", "jsonschema")
        assert spl.build_dashboard_tile_specs(
            strategies_dir=tmp_path, enforce_numeric_bars_gate=False,
        ) == []
        assert spl.load_registered_packages(
            tmp_path, enforce_numeric_bars_gate=False,
        ) == []


def test_empty_strategies_dir_skips_schema_load(tmp_path: Path):
    """HM HOLD item 3: no YAMLs → skip schema; no tiles."""
    assert list(tmp_path.glob("*.yaml")) == []
    with mock.patch.object(spl, "load_schema", side_effect=AssertionError("schema should not load")):
        assert spl.load_registered_packages(tmp_path) == []
        assert spl.build_dashboard_tile_specs(strategies_dir=tmp_path) == []


def test_build_dashboard_swallows_unexpected_loader_errors(tmp_path: Path):
    (tmp_path / "x.yaml").write_text("id: x\n", encoding="utf-8")
    with mock.patch.object(
        spl, "load_registered_packages", side_effect=RuntimeError("boom"),
    ):
        assert spl.build_dashboard_tile_specs(
            strategies_dir=tmp_path, enforce_numeric_bars_gate=False,
        ) == []


def test_bot_pathway_specs_catch_all_and_soft_fallback_wired():
    bot_src = (HERE / "bot.py").read_text(encoding="utf-8")
    assert "import strategy_package_loader" in bot_src
    assert "build_dashboard_tile_specs()" in bot_src
    assert "except Exception as exc" in bot_src
    assert "strategy_package_tiles || []" in bot_src
    assert "strategy_package && !spec.strategy_package_activated" in bot_src
    loader_src = (HERE / "strategy_package_loader.py").read_text(encoding="utf-8")
    assert "deps_available" in loader_src
    assert "NUMERIC_BARS_FOLLOWUP_REQUIRED" in loader_src
    assert "ACTIVE_TILE_REGISTRY[" not in loader_src


# --- Boss/Strategist schema extras (a)–(f) ---


def test_extra_a_holdout_spent_bool_and_optional_note():
    pkg = _valid_pkg(holdout_spent=True, holdout_spent_note="spent on LEAD window only")
    loaded = spl.validate_package_dict(pkg, source="holdout", schema=SCHEMA)
    assert loaded["holdout_spent"] is True
    assert "LEAD" in loaded["holdout_spent_note"]
    # bool alone OK
    ok = spl.validate_package_dict(_valid_pkg(holdout_spent=False), source="holdout_false", schema=SCHEMA)
    assert ok["holdout_spent"] is False


def test_extra_b_data_requires_extended_enum():
    extras = ["spot1s", "perp1s", "metrics", "depth", "basis1s", "bfx1s", "dvol"]
    pkg = _valid_pkg(data_requires=["book10", "trades", *extras])
    loaded = spl.validate_package_dict(pkg, source="data_req", schema=SCHEMA)
    for name in extras:
        assert name in loaded["data_requires"]
    bad = _valid_pkg(data_requires=["book10", "not_a_real_feed"])
    with pytest.raises(spl.StrategyPackageRefuse, match="bad spec"):
        spl.validate_package_dict(bad, source="bad_feed", schema=SCHEMA)


def test_extra_c_trail_pct_allows_absent_stop_and_refuses_empty_exit():
    # Stop absent OK when trail_pct set.
    trail_only = _valid_pkg()
    trail_only["exit"] = {
        "composite": ["ATR_TRAIL", "TIME_EXIT"],
        "thesis_cut": False,
        "trail_pct": 1.5,
        "tp": None,
        "max_hold_min": 90,
    }
    del trail_only["exit"]  # rebuild cleanly without hard_stop
    trail_only["exit"] = {
        "composite": ["ATR_TRAIL"],
        "thesis_cut": False,
        "trail_pct": 1.5,
        "tp": None,
        "max_hold_min": 90,
    }
    loaded = spl.validate_package_dict(trail_only, source="trail", schema=SCHEMA)
    assert "hard_stop_pct" not in loaded["exit"]
    assert loaded["exit"]["trail_pct"] == 1.5

    # Stop absent OK when only max_hold_min set.
    hold_only = _valid_pkg()
    hold_only["exit"] = {
        "composite": ["TIME_EXIT"],
        "thesis_cut": False,
        "tp": None,
        "max_hold_min": 60,
    }
    spl.validate_package_dict(hold_only, source="hold_only", schema=SCHEMA)

    # No exit at all → refuse.
    no_exit = _valid_pkg()
    del no_exit["exit"]
    with pytest.raises(spl.StrategyPackageRefuse, match="exit"):
        spl.validate_package_dict(no_exit, source="no_exit", schema=SCHEMA)

    # Exit present but empty of stop/trail/hold → refuse.
    empty_mech = _valid_pkg()
    empty_mech["exit"] = {
        "composite": ["NONE"],
        "thesis_cut": False,
        "tp": None,
    }
    with pytest.raises(spl.StrategyPackageRefuse, match="no stop/trail/max_hold|bad spec"):
        spl.validate_package_dict(empty_mech, source="empty_exit", schema=SCHEMA)


def test_extra_d_max_stop_bp_refuses_31bp_at_100x():
    """Boss: loader enforces stop_bp <= max_stop_bp; test 31 bp @ 100x."""
    over = _valid_pkg()
    over["sizing"] = {"margin_usd": 0.25, "leverage": 100, "max_stop_bp": 30}
    over["exit"] = dict(over["exit"])
    over["exit"]["hard_stop_pct"] = 0.31  # → 31 bp
    assert spl.resolve_stop_bp(over["exit"]) == pytest.approx(31.0)
    with pytest.raises(spl.StrategyPackageRefuse, match="max_stop_bp|31"):
        spl.validate_package_dict(over, source="31bp", schema=SCHEMA)

    # Explicit stop_bp 31 also refused.
    over2 = _valid_pkg()
    over2["sizing"] = {"margin_usd": 0.25, "leverage": 100, "max_stop_bp": 30}
    over2["exit"] = {
        "composite": ["HARD_STOP"],
        "thesis_cut": False,
        "stop_bp": 31,
        "tp": None,
        "max_hold_min": 60,
    }
    with pytest.raises(spl.StrategyPackageRefuse, match="max_stop_bp"):
        spl.validate_package_dict(over2, source="31bp_explicit", schema=SCHEMA)

    # 30 bp at cap passes.
    ok = _valid_pkg()
    ok["sizing"] = {"margin_usd": 0.25, "leverage": 100, "max_stop_bp": 30}
    ok["exit"] = dict(ok["exit"])
    ok["exit"]["hard_stop_pct"] = 0.30
    spl.validate_package_dict(ok, source="30bp_ok", schema=SCHEMA)


def test_extra_e_costs_note_free_text():
    pkg = _valid_pkg(
        costs={
            "model": "REALISTIC_V1",
            "note": "REALISTIC_V1 proxy, no book walk",
        }
    )
    loaded = spl.validate_package_dict(pkg, source="costs_note", schema=SCHEMA)
    assert loaded["costs"]["model"] == "REALISTIC_V1"
    assert "no book walk" in loaded["costs"]["note"]
    # String form still accepted.
    spl.validate_package_dict(_valid_pkg(costs="REALISTIC_V1"), source="costs_str", schema=SCHEMA)


def test_extra_f_oos_22m_null_or_not_run():
    for label in (None, "NOT_RUN"):
        pkg = _valid_pkg()
        pkg["evidence"] = dict(pkg["evidence"])
        pkg["evidence"]["oos_22m"] = label
        loaded = spl.validate_package_dict(pkg, source=f"oos_{label}", schema=SCHEMA)
        assert loaded["evidence"]["oos_22m"] == label
    # OOS_PASS still requires HOLDS (null refused).
    bad = _oos_holds_pkg()
    bad["evidence"] = dict(bad["evidence"])
    bad["evidence"]["oos_22m"] = None
    with pytest.raises(spl.StrategyPackageRefuse, match="HOLDS"):
        spl.validate_package_dict(bad, source="oos_null_pass", schema=SCHEMA)
