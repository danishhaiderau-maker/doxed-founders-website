import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fly_postdeploy_active_gate as gate

LANES = ["FAMILY_CHANDELIER_3", "FAMILY_ATR_TRAIL"]
WORKFLOW = (Path(__file__).resolve().parents[1] / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")
RETIRED = frozenset({"FAMILY_DANISH_CF", "FAMILY_CONTINUOUS_AUG_ORIGINAL"})


@pytest.fixture(autouse=True)
def _synthetic_checkout_registry(monkeypatch, request):
    if not request.node.name.startswith("test_checkout_registry_"):
        monkeypatch.setattr(gate, "checkout_registry", lambda: (list(LANES), RETIRED))


def _active_status(**overrides):
    status = {
        "source_git_rev": "abcdef123456",
        "execution_paused": False,
        "manual_admin_pause": False,
        "live_armed": False,
        "bitfinex_live_enabled": False,
        "force_paper_mode": True,
    }
    status.update(overrides)
    return status


def test_every_registry_lane_not_on_is_enabled_whatever_its_prior_state():
    current = {"FAMILY_CHANDELIER_3": False, "FAMILY_ATR_TRAIL": True, "RETIRED": False}
    assert gate.tile_enable_plan(current, LANES) == ["FAMILY_CHANDELIER_3"]
    assert gate.tile_enable_plan({}, LANES) == LANES


class _Bot:
    def __init__(self, enabled, relay_eligible=False, live_armed=False):
        self.enabled = dict(enabled)
        self.relay_eligible = relay_eligible
        self.live_armed = live_armed
        self.toggles = []

    def __call__(self, path, payload=None):
        if path == "/api/status":
            return {**_active_status(live_armed=self.live_armed), "pause_owner": "",
                    "active_tiles": [{"lane": lane, "relay_eligible": self.relay_eligible} for lane in LANES]}
        if path == "/api/state":
            return {"research_lane_enabled": dict(self.enabled)}
        assert path == "/api/toggle_research_lane" and payload["enabled"] is True
        self.toggles.append(payload["lane"])
        self.enabled[payload["lane"]] = True
        return {"lane": payload["lane"], "enabled": True}


def test_enable_all_registry_tiles_turns_every_tile_on_and_returns_receipt():
    bot = _Bot({"FAMILY_CHANDELIER_3": False})
    receipt = gate.enable_all_registry_tiles(bot)
    assert bot.toggles == LANES
    assert receipt["tiles_all_on"] is True and receipt["tiles_off"] == []
    assert receipt["pause_owner"] == "" and receipt["live_armed"] is False
    assert receipt["bitfinex_live_enabled"] is False


def test_enable_all_registry_tiles_refuses_relay_eligible_or_armed_state():
    with pytest.raises(SystemExit, match="relay-ineligible"):
        gate.enable_all_registry_tiles(_Bot({}, relay_eligible=True))
    with pytest.raises(SystemExit, match="disarmed"):
        gate.enable_all_registry_tiles(_Bot({}, live_armed=True))


def test_pause_owner_is_a_paper_active_violation():
    assert gate.paper_active_violations(_active_status(pause_owner="OPERATOR"), "abcdef123456") == [
        "PAUSE_OWNER:OPERATOR"]


def test_every_resume_path_forces_all_tiles_on():
    scripts = Path(__file__).resolve().parent
    for name in ("fly_failure_paper_resume.py", "fly_resume_bootstrap.py", "fly_resume_predeploy_abort.py"):
        assert "enable_all_registry_tiles" in (scripts / name).read_text(encoding="utf-8"), name
    for job in ("repair-execution-tail", "repair-lifecycle-cursor", "repair-lifecycle-tail",
                "restart-only", "recover-startup-crash", "recover-memory"):
        block = re.search(rf"\n  {re.escape(job)}:\n(.*?)(?=\n  [a-z0-9-]+:\n|\Z)", WORKFLOW, re.S).group(1)
        assert "fly_postdeploy_active_gate.py --tiles-only" in block, job


def test_active_state_requires_unpaused_paper_and_disarmed_live():
    assert gate.paper_active_violations(_active_status(), "abcdef123456") == []
    problems = gate.paper_active_violations(
        _active_status(execution_paused=True, execution_reason="ADMIN_MANUAL", live_armed=True, force_paper_mode=False),
        "abcdef123456",
    )
    assert "EXECUTION_PAUSED:ADMIN_MANUAL" in problems
    assert "LIVE_ARMED" in problems
    assert "FORCE_PAPER_MODE_OFF" in problems
    assert gate.paper_active_violations(_active_status(), "000000000000") == ["REVISION_MISMATCH"]


def test_relay_eligible_tiles_fail_the_gate():
    tiles = [{"lane": "A", "relay_eligible": False}, {"lane": "B", "relay_eligible": True}, {"lane": "C"}]
    assert gate.relay_eligible_tiles(tiles) == ["B", "C"]


def test_ai_cadence_counts_distinct_completions_after_start():
    assert gate.count_ai_completions([90.0, 110.0, 110.0, 150.0], started=100.0) == 2
    assert gate.count_ai_completions([90.0, 90.0], started=100.0) == 0


def test_ai_gate_samples_successful_responses_not_cycle_completions():
    timed_out_cycle = {"strategy_progress": {
        "scheduled_ai_cycle": {"completed_ts": 500.0},
        "ai_provider": {"last_ai_success_ts": 0.0, "consecutive_failures": 4},
    }}
    assert gate.ai_success_sample(timed_out_cycle) == 0.0
    assert gate.ai_success_sample({"strategy_progress": {"scheduled_ai_cycle": {"completed_ts": 500.0}}}) == 0.0
    ok = {"strategy_progress": {"ai_provider": {"last_ai_success_ts": 612.5}}}
    assert gate.ai_success_sample(ok) == 612.5


def test_gate_fails_when_cycles_complete_but_every_model_call_fails(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "t")
    monkeypatch.setenv("EXPECTED_REVISION", "abcdef123456")
    monkeypatch.setenv("POSTDEPLOY_ACTIVE_DEADLINE_SEC", "1")
    monkeypatch.setattr(gate, "POLL_SEC", 0)
    monkeypatch.setattr(gate, "enable_all_registry_tiles", lambda request: {})
    clock = iter(range(1000, 10_000))
    monkeypatch.setattr(gate.time, "time", lambda: float(next(clock)))

    def request(path, token, payload=None, timeout=30):
        if path == "/api/status":
            return {**_active_status(), "pause_owner": ""}
        return {"strategy_progress": {
            "scheduled_ai_cycle": {"completed_ts": float(next(clock))},
            "ai_provider": {"last_ai_success_ts": 0.0, "consecutive_failures": 9,
                            "last_error_class": "TIMEOUT"},
        }}

    monkeypatch.setattr(gate, "_request", request)
    with pytest.raises(SystemExit, match="successful model responses"):
        gate.main([])


def test_workflow_runs_gate_after_resume_unless_hold_or_operator_pause():
    assert "python scripts/fly_postdeploy_active_gate.py" in WORKFLOW
    step = WORKFLOW[WORKFLOW.index("Assert paper active with advancing AI cadence"):]
    step = step[: step.index("run: python scripts/fly_postdeploy_active_gate.py")]
    assert "steps.paper_resume.outputs.operator_pause_retained != 'true'" in step
    assert "inputs.keep_maintenance_pause == true" in step


def test_workflow_deploy_pauses_and_resumes_are_deploy_owned():
    assert "STICKY_ADMIN_MANUAL_PAUSE" not in WORKFLOW
    for line in WORKFLOW.splitlines():
        if '"/api/resume"' in line and "json.dumps" not in line and "post(" not in line:
            assert "DEPLOY_MAINTENANCE" in line, line
    held = WORKFLOW[WORKFLOW.index("Keep true-flat ADMIN_MANUAL after exact-revision acceptance"):]
    held = held[: held.index("Best-effort preserve safe paper maintenance")]
    assert 'post("/api/pause", {})' in held
    assert "operator_pause_retained" in held


def test_workflow_dispatch_inputs_stay_within_github_limit():
    block = re.search(r"\n  workflow_dispatch:\n    inputs:\n(.*?)(?=\n {0,4}\S)", WORKFLOW, re.S).group(1)
    inputs = re.findall(r"^      ([a-z0-9_]+):$", block, re.M)
    assert "mode" in inputs and len(inputs) <= 25


def test_keep_maintenance_pause_defaults_false():
    block = WORKFLOW[WORKFLOW.index("keep_maintenance_pause:"):]
    block = block[: block.index("lifecycle_reset_proof:")]
    assert "default: false" in block


class _HeldBot(_Bot):
    def __call__(self, path, payload=None):
        if path == "/api/toggle_research_lane":
            self.toggles.append((payload["lane"], payload["enabled"]))
            self.enabled[payload["lane"]] = payload["enabled"]
            return {"lane": payload["lane"], "enabled": payload["enabled"]}
        return super().__call__(path, payload)


def test_held_off_lanes_are_turned_off_and_required_off(monkeypatch):
    monkeypatch.setenv("PAPER_TILES_HOLD_OFF", " FAMILY_ATR_TRAIL ,")
    bot = _HeldBot({"FAMILY_CHANDELIER_3": False, "FAMILY_ATR_TRAIL": True})
    receipt = gate.enable_all_registry_tiles(bot)
    assert bot.toggles == [("FAMILY_ATR_TRAIL", False), ("FAMILY_CHANDELIER_3", True)]
    assert receipt["tiles_all_on"] is True and receipt["held_off"] == ["FAMILY_ATR_TRAIL"]
    assert receipt["held_not_off"] == []


def test_held_lane_left_on_fails_the_receipt():
    status = {"active_tiles": [{"lane": lane} for lane in LANES]}
    state = {"research_lane_enabled": dict.fromkeys(LANES, True)}
    receipt = gate.tiles_all_on_receipt(status, state, frozenset({"FAMILY_ATR_TRAIL"}))
    assert receipt["tiles_all_on"] is False and receipt["held_not_off"] == ["FAMILY_ATR_TRAIL"]
    assert gate.tiles_all_on_receipt(status, state, frozenset(LANES))["tiles_all_on"] is False


def test_hold_off_list_comes_from_repository_variable():
    assert "PAPER_TILES_HOLD_OFF: ${{ vars.PAPER_TILES_HOLD_OFF }}" in WORKFLOW
    assert gate.held_off_lanes({}) == frozenset()


def test_checkout_registry_is_the_freeze21b_roster_with_the_old_tiles_retired():
    lanes, retired = gate.checkout_registry()
    assert lanes == ["FAMILY_COMMITTED_FADE_TAKER_90", "FAMILY_NOTRADE_FOLLOW_TAKER_60",
                     "FAMILY_PREMIUM_REVERSION_60M", "FAMILY_RANDOM_CONTROL_TAKER_90",
                     "FAMILY_GS01_XV_PREMIUM_ATR_TP", "FAMILY_GS02_NOTRADE_REGIME_ENTRY",
                     "FAMILY_GS03_CVD_DIV_TAKER", "FAMILY_GS04_NOTRADE_ATR_TP",
                     "FAMILY_GSB1_CVD_DIV_REGIME", "FAMILY_GSB2_REGIME_SWITCHER",
                     "FAMILY_GSB3_COMMITTED_FADE_REGIME"]
    assert {"FAMILY_DANISH_CF", "FAMILY_DANISH_CF_NOES", "FAMILY_DANISH_CF_ALL_SESSIONS",
            "FAMILY_CONTINUOUS_AUG_ORIGINAL", "FAMILY_COMMITTED_FADE_MAKER_90",
            "FAMILY_NOTRADE_FOLLOW_MAKER_60", "FAMILY_XVENUE_SESSION_FOLLOW_60M"} <= retired


def test_running_roster_must_equal_the_checked_out_registry(monkeypatch):
    monkeypatch.setattr(gate, "checkout_registry", lambda: (["FAMILY_ATR_TRAIL", "FAMILY_CHANDELIER_3"], RETIRED))
    with pytest.raises(SystemExit, match="does not match the checked-out registry"):
        gate.enable_all_registry_tiles(_Bot({}))


def test_retired_lane_on_or_listed_fails_the_receipt():
    status = {"active_tiles": [{"lane": lane} for lane in LANES]}
    state = {"research_lane_enabled": {**dict.fromkeys(LANES, True), "FAMILY_DANISH_CF": True}}
    receipt = gate.tiles_all_on_receipt(status, state, expected=LANES, retired=RETIRED)
    assert receipt["tiles_all_on"] is False and receipt["retired_on"] == ["FAMILY_DANISH_CF"]
    listed = {"active_tiles": [{"lane": lane} for lane in [*LANES, "FAMILY_DANISH_CF"]]}
    clean = {"research_lane_enabled": dict.fromkeys(LANES, True)}
    receipt = gate.tiles_all_on_receipt(listed, clean, expected=LANES, retired=RETIRED)
    assert receipt["tiles_all_on"] is False and receipt["roster_matches_checkout"] is False
    assert receipt["retired_on_roster"] == ["FAMILY_DANISH_CF"]
    ok = gate.tiles_all_on_receipt(status, {"research_lane_enabled": {**clean["research_lane_enabled"],
                                                                     "FAMILY_DANISH_CF": False}},
                                   expected=LANES, retired=RETIRED)
    assert ok["tiles_all_on"] is True and ok["roster_matches_checkout"] is True


def test_freeze_override_is_forwarded_only_when_fully_declared():
    assert gate.freeze_override_payload({}) is None
    assert gate.freeze_override_payload({"RESEARCH_FREEZE_OVERRIDE": "BREAK_21_DAY_RESEARCH_FREEZE"}) is None
    assert gate.freeze_override_payload({"RESEARCH_FREEZE_OVERRIDE": "BREAK_21_DAY_RESEARCH_FREEZE",
                                         "RESEARCH_FREEZE_OVERRIDE_REASON": "KILL_RULE:X:K4"}) == {
        "confirmation": "BREAK_21_DAY_RESEARCH_FREEZE", "reason": "KILL_RULE:X:K4"}


def test_transient_ready_503_does_not_fail_the_gate(monkeypatch):
    """Reset run 37188730617 failed only because /ready blipped 503 for ~1 min."""
    import io
    import urllib.error

    monkeypatch.setenv("BOT_ADMIN_TOKEN", "t")
    monkeypatch.setenv("EXPECTED_REVISION", "abcdef123456")
    monkeypatch.setenv("POSTDEPLOY_ACTIVE_DEADLINE_SEC", "500")
    monkeypatch.setattr(gate, "POLL_SEC", 0)
    monkeypatch.setattr(gate.time, "sleep", lambda _s: None)
    monkeypatch.setattr(gate, "enable_all_registry_tiles", lambda request: {})
    clock = iter(range(1000, 10_000))
    monkeypatch.setattr(gate.time, "time", lambda: float(next(clock)))
    calls = {"ready": 0}

    def request(path, token, payload=None, timeout=30):
        if path == "/api/status":
            return {**_active_status(), "pause_owner": ""}
        calls["ready"] += 1
        if calls["ready"] <= 9:  # longer than one _retrying budget
            raise urllib.error.HTTPError(path, 503, "unready", {}, io.BytesIO(b"not json"))
        return {"strategy_progress": {"ai_provider": {"last_ai_success_ts": float(1000 + calls["ready"])}}}

    monkeypatch.setattr(gate, "_request", request)
    assert gate.main([]) == 0
    assert calls["ready"] >= 11
