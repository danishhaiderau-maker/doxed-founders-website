"""Five named strategy-package loader refuses (R47) + dashboard auto-render + R53(c).

Mutation proofs (break the named check → this file fails):
- drop series_stamp require → missing-stamp case passes
- allow paper_only false → paper_only=false case passes
- skip credential scan → credential case passes
- widen evidence enum → invalid status case passes
- allow non-FWD_PASS into R53(c) → feeds_r53c_gate(LEAD) becomes True
"""
from __future__ import annotations

import copy
from pathlib import Path

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
            "oos_22m_summary": "fixture only",
            "combos_searched": 0,
            "fdr": None,
        },
        "paper_only": True,
    }
    base.update(overrides)
    return base


def test_bad_spec_refused():
    """Bad spec (schema-invalid sizing / missing required exit fields) hard-refuses."""
    bad = _valid_pkg()
    del bad["exit"]
    with pytest.raises(spl.StrategyPackageRefuse, match="bad spec|exit"):
        spl.validate_package_dict(bad, source="bad_spec", schema=SCHEMA)
    # Also refuse out-of-range margin.
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
    pkg2["paper_only"] = "true"  # string is not exactly True
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
    pkg["evidence"]["status"] = "PASS"  # not in LEAD|OOS_PASS|FWD_PASS
    with pytest.raises(spl.StrategyPackageRefuse, match="evidence.status"):
        spl.validate_package_dict(pkg, source="bad_evidence", schema=SCHEMA)


def test_fwd_pass_only_feeds_r53c_gate():
    lead = spl.validate_package_dict(_valid_pkg(), source="lead", schema=SCHEMA)
    assert spl.feeds_r53c_gate(lead) is False
    with pytest.raises(spl.StrategyPackageRefuse, match="R53\\(c\\)"):
        spl.assert_r53c_eligible(lead, source="lead")

    oos = _valid_pkg()
    oos["evidence"] = dict(oos["evidence"])
    oos["evidence"]["status"] = "OOS_PASS"
    oos_pkg = spl.validate_package_dict(oos, source="oos", schema=SCHEMA)
    assert spl.feeds_r53c_gate(oos_pkg) is False

    fwd = _valid_pkg()
    fwd["evidence"] = dict(fwd["evidence"])
    fwd["evidence"]["status"] = "FWD_PASS"
    fwd_pkg = spl.validate_package_dict(fwd, source="fwd", schema=SCHEMA)
    assert spl.feeds_r53c_gate(fwd_pkg) is True
    spl.assert_r53c_eligible(fwd_pkg, source="fwd")


def test_valid_package_loads_but_is_not_activated(tmp_path: Path):
    pkg = _valid_pkg()
    path = tmp_path / "example_fade.yaml"
    path.write_text(yaml.safe_dump(pkg, sort_keys=False), encoding="utf-8")
    loaded = spl.load_package_file(path, schema=SCHEMA)
    assert loaded["id"] == "example_fade"
    assert loaded["paper_only"] is True
    assert loaded["_activated"] is False


def test_dashboard_auto_renders_one_tile_per_registered_package(tmp_path: Path):
    """Dashboard tile registry renders a tile per registered package automatically."""
    a = _valid_pkg(id="pkg_a", name="Package A")
    b = _valid_pkg(id="pkg_b", name="Package B")
    b["evidence"] = dict(b["evidence"])
    b["evidence"]["status"] = "FWD_PASS"
    (tmp_path / "pkg_a.yaml").write_text(yaml.safe_dump(a, sort_keys=False), encoding="utf-8")
    (tmp_path / "pkg_b.yaml").write_text(yaml.safe_dump(b, sort_keys=False), encoding="utf-8")
    (tmp_path / "_ignored.yaml").write_text("id: nope\n", encoding="utf-8")  # underscore skip
    tiles = spl.build_dashboard_tile_specs(strategies_dir=tmp_path)
    assert [t["strategy_package_id"] for t in tiles] == ["pkg_a", "pkg_b"]
    assert all(t["strategy_package_activated"] is False for t in tiles)
    assert all(t["status"] == "NOT_ACTIVATED" for t in tiles)
    assert tiles[0]["feeds_r53c"] is False
    assert tiles[1]["feeds_r53c"] is True


def test_repo_strategies_dir_has_no_activated_packages():
    """R47: this PR must not activate any strategy package files."""
    paths = spl.iter_strategy_yaml_paths()
    assert paths == [], f"unexpected activated/registered packages: {paths}"
    tiles = spl.build_dashboard_tile_specs()
    assert tiles == []


def test_bot_pathway_specs_wire_strategy_package_tiles_without_roster_mix():
    """Dashboard auto-render is wired; packages never enter ACTIVE_TILE_ORDER lanes."""
    bot_src = (HERE / "bot.py").read_text(encoding="utf-8")
    assert "import strategy_package_loader" in bot_src
    assert 'strategy_package_tiles": strategy_package_tiles' in bot_src or (
        '"strategy_package_tiles": strategy_package_tiles' in bot_src
    )
    assert "build_dashboard_tile_specs()" in bot_src
    # JS concatenates package tiles after registry lanes (auto-render).
    assert "strategy_package_tiles || []" in bot_src
    assert "strategy_package && !spec.strategy_package_activated" in bot_src
    # Must not push packages into the frozen COMBO/ACTIVE lane loop.
    assert "ACTIVE_TILE_ORDER" in bot_src
    # Guard: loader refuse must not be swallowed into ACTIVE_TILE_REGISTRY edits.
    assert "ACTIVE_TILE_REGISTRY[" not in (
        HERE / "strategy_package_loader.py"
    ).read_text(encoding="utf-8")
