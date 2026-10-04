"""FREEZE21 contract: the declared registry and epoch stay frozen; reset and tile-OFF paths refuse without the override."""
import json
import os
import subprocess
import sys
import re
from pathlib import Path

import pytest

import combo_pathway_config as cfg
import research_freeze as rf

HERE = Path(__file__).resolve().parent
START = 1_790_000_000.0
MANIFEST = {"epoch_id": rf.FREEZE_DATA_EPOCH_ID, "started_at_ts": START}
OVERRIDE = {"confirmation": rf.OVERRIDE_CONFIRMATION, "reason": "KILL_RULE:FAMILY_COMMITTED_FADE_TAKER_90:K4"}
NO_ENV = {}


def _fly_epoch() -> str:
    text = (HERE / "fly.toml").read_text(encoding="utf-8")
    return re.search(r'^\s*DATA_EPOCH_ID\s*=\s*"([^"]+)"', text, re.M).group(1)


frozen = pytest.mark.skipif(rf.FREEZE_STATUS != "ACTIVE" or bool(rf.CODE_OVERRIDE),
                            reason="freeze lifted or code override declared")


@frozen
def test_registry_roster_order_and_signature_match_the_freeze_declaration():
    assert tuple(cfg.ACTIVE_TILE_ORDER) == rf.FREEZE_ROSTER
    assert cfg.RESEARCH_STACK_VERSION == rf.FREEZE_REGISTRY_VERSION
    for mode, flag in (("score_led", "1"), ("hypothesis", "")):
        env = {k: v for k, v in os.environ.items() if k != "SCORE_LED_PAPER_RESEARCH_ENABLED"}
        if flag:
            env["SCORE_LED_PAPER_RESEARCH_ENABLED"] = flag
        out = subprocess.run(
            [sys.executable, "-c", "import combo_pathway_config as c; print(c.active_tile_registry_signature())"],
            cwd=Path(__file__).resolve().parent, env=env, check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert out == rf.FREEZE_REGISTRY_SIGNATURES[mode], (
            f"the tile registry ({mode} mode) changed during the 21-day freeze: revert, or set "
            "research_freeze.CODE_OVERRIDE with the owner's approval")
    assert rf.FREEZE_REGISTRY_SIGNATURE == rf.FREEZE_REGISTRY_SIGNATURES["score_led"]


@frozen
def test_fly_toml_declares_the_one_freeze_epoch():
    assert _fly_epoch() == rf.FREEZE_DATA_EPOCH_ID


def test_every_frozen_tile_is_pre_registered_with_target_kill_and_day21():
    roles = []
    for lane in rf.FREEZE_ROSTER:
        pre = cfg.COMBO_LANE_SPECS[lane]["pre_registration"]
        assert pre["schema"] == "tile_pre_registration_freeze21_v1" and pre["freeze_id"] == rf.FREEZE_ID
        assert pre["target"]["min_distinct_hours"] >= 150 and pre["target"]["min_fills"] > 0
        assert {"k3_worst_trade_bp_below", "k6_defect_action", "k1_after_distinct_hours"} <= set(pre["kill"])
        assert pre["day21"]["decision_day"] == rf.FREEZE_DAYS
        assert {"pass", "fail", "inconclusive"} <= set(pre["day21"])
        assert cfg.COMBO_LANE_SPECS[lane]["default_enabled"] is True
        assert cfg.COMBO_LANE_SPECS[lane]["paper_only"] is True
        assert cfg.COMBO_LANE_SPECS[lane]["platform_relay_eligible"] is False
        roles.append(pre["role"])
    assert roles.count("HYPOTHESIS") == 3 and roles.count("CONTROL") == 1


def test_status_window_opening_active_complete():
    assert rf.freeze_status(None, START)["status"] == rf.NOT_STARTED
    assert rf.freeze_status({"epoch_id": "ce-other", "started_at_ts": START}, START)["status"] == rf.NOT_STARTED
    assert rf.freeze_status(MANIFEST, START + 60)["status"] == rf.OPENING
    active = rf.freeze_status(MANIFEST, START + 2 * 86400)
    assert active["status"] == rf.ACTIVE and active["guarded"] and active["day"] == 3
    assert active["ends_at_utc"] == rf._iso(START + 21 * 86400)
    assert rf.freeze_status(MANIFEST, START + 21 * 86400)["status"] == rf.COMPLETE
    mismatch = rf.freeze_status(MANIFEST, START + 86400, configured_epoch="ce-20261010-someone-else")
    assert mismatch["status"] == rf.EPOCH_MISMATCH and mismatch["guarded"]


def test_reset_refuses_during_the_freeze_without_override():
    verdict = rf.check(rf.ACTION_RESET, MANIFEST, START + 86400, env=NO_ENV)
    assert verdict["allowed"] is False and verdict["error"] == rf.ERROR
    assert rf.OVERRIDE_CONFIRMATION in verdict["summary"]
    assert rf.check(rf.ACTION_RESET, MANIFEST, START + 22 * 86400, env=NO_ENV)["allowed"] is True
    assert rf.check(rf.ACTION_RESET, None, START, env=NO_ENV)["allowed"] is True


def test_opening_hour_allows_only_the_boundary_reset():
    assert rf.check(rf.ACTION_RESET, MANIFEST, START + 600, env=NO_ENV)["allowed"] is True
    assert rf.check(rf.ACTION_PRE_START_WIPE, MANIFEST, START + 600, env=NO_ENV)["allowed"] is True
    assert rf.check(rf.ACTION_TILE_OFF, MANIFEST, START + 600, lane=rf.FREEZE_ROSTER[0],
                    env=NO_ENV)["allowed"] is False
    assert rf.check(rf.ACTION_RESET, MANIFEST, START + 3601, env=NO_ENV)["allowed"] is False


def test_override_needs_the_confirmation_and_a_reason():
    now = START + 86400
    ok = rf.check(rf.ACTION_RESET, MANIFEST, now, override=OVERRIDE, env=NO_ENV)
    assert ok["allowed"] is True and ok["override"]["source"] == "request"
    for bad in ({"confirmation": "yes", "reason": "long enough reason"},
                {"confirmation": rf.OVERRIDE_CONFIRMATION, "reason": "short"},
                {"confirmation": rf.OVERRIDE_CONFIRMATION}, "BREAK_21_DAY_RESEARCH_FREEZE"):
        assert rf.check(rf.ACTION_RESET, MANIFEST, now, override=bad, env=NO_ENV)["allowed"] is False
    env = {rf.OVERRIDE_ENV: rf.OVERRIDE_CONFIRMATION, rf.OVERRIDE_REASON_ENV: "owner approved re-run 2026-10-10"}
    via_env = rf.check(rf.ACTION_RESET, MANIFEST, now, env=env)
    assert via_env["allowed"] is True and via_env["override"]["source"] == "env"


def test_tile_on_for_the_roster_is_allowed_but_off_and_foreign_on_need_override():
    now = START + 86400
    lane = rf.FREEZE_ROSTER[1]
    assert rf.check(rf.ACTION_TILE_ON, MANIFEST, now, lane=lane, env=NO_ENV)["allowed"] is True
    assert rf.check(rf.ACTION_TILE_OFF, MANIFEST, now, lane=lane, env=NO_ENV)["allowed"] is False
    assert rf.check(rf.ACTION_TILE_ON, MANIFEST, now, lane="FAMILY_SOMETHING_NEW", env=NO_ENV)["allowed"] is False
    assert rf.check(rf.ACTION_TILE_OFF, MANIFEST, now, lane=lane, override=OVERRIDE, env=NO_ENV)["allowed"] is True


def test_clean_epoch_pre_start_execute_refuses_inside_the_freeze(tmp_path, monkeypatch, capsys):
    import clean_epoch_wipe

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    manifest_path = runtime / "data_epoch.json"
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(clean_epoch_wipe.data_epoch, "load_manifest", lambda path: dict(MANIFEST))
    monkeypatch.setattr(clean_epoch_wipe.time, "time", lambda: START + 86400)
    monkeypatch.delenv(rf.OVERRIDE_ENV, raising=False)

    class Args:
        manifest = None
        scope = "fly"
        data_root = str(tmp_path)

    refusal = clean_epoch_wipe._research_freeze_refusal(Args, START + 86400)
    assert refusal and refusal["error"] == rf.ERROR
    assert clean_epoch_wipe._research_freeze_refusal(Args, START + 600) is None
    monkeypatch.setenv(rf.OVERRIDE_ENV, rf.OVERRIDE_CONFIRMATION)
    monkeypatch.setenv(rf.OVERRIDE_REASON_ENV, "owner approved pre-start wipe")
    assert clean_epoch_wipe._research_freeze_refusal(Args, START + 86400) is None


def test_bot_reset_and_toggle_paths_route_through_the_freeze_guard():
    source = (HERE / "bot.py").read_text(encoding="utf-8")
    body = source.split("def perform_fresh_collection_reset(", 1)[1].split("\ndef ", 1)[0]
    assert "_research_freeze_check(_research_freeze.ACTION_RESET" in body
    for call in re.findall(r"perform_fresh_collection_reset\(([^)]*)\)", source.split("def perform_fresh_collection_reset(", 1)[1]):
        assert "freeze_override" in call, call
    toggle = source.split("def toggle_research_lane():", 1)[1].split("\n@app.route", 1)[0]
    assert "_research_freeze.ACTION_TILE_OFF" in toggle and "freeze_override" in toggle
    assert '"research_freeze": _research_freeze_public()' in source


def test_status_payload_is_json_serialisable():
    json.dumps(rf.freeze_status(MANIFEST, START + 86400, rf.FREEZE_DATA_EPOCH_ID))
