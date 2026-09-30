"""Decision page: selector verdict and forward-trial status are current-generation only and never faked."""
from datetime import datetime, timezone

from research import decision_view as dv

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
CURRENT = {"status": "CURRENT_GENERATION", "blockers": []}
SELECTOR = {
    "verdict": "NOT_ENOUGH_DATA",
    "verdict_text": "NOT ENOUGH DATA: fixed_oos_closes n=8 (<30)",
    "oos": {"fixed": {"n": 8, "mean_usd": -0.0134, "ci95_usd": [-0.0383, 0.0116]},
            "dynamic": {"n": 8, "mean_usd": -0.0134, "ci95_usd": [-0.0383, 0.0116]},
            "dynamic_minus_fixed_paired": {"n": 8, "mean_usd": 0.0, "ci95_usd": [0.0, 0.0]}},
    "per_regime": [{"regime": "BEAR|ADX_LT20|ATR_LOW", "oos_dynamic": {"n": 5}, "oos_dynamic_regime_specific_n": 0,
                    "gate": "NOT_ENOUGH_DATA (n<30)", "closes_by_tile": {"A": 23, "B": 14}}],
    "pick_counts": {"DYNAMIC_FALLBACK_TO_FIXED": 11},
}
TRIAL = {"status": "NO_QUALIFYING_CANDIDATE", "status_text": "Freeze refused: no tile passes every gate",
         "candidates": [{"lane": "A", "n": 45, "ci95_usd": [-0.02, -0.001], "failed_gates": ["ev_ci95_above_zero"]}]}


def _html(selector, trial):
    payload = dv.build_decision_payload(
        tile_order=("A",), registry={"A": {"label": "Tile A"}}, funnel_report={"lanes": {}}, ai_coverage=None,
        generation={"generated_at": NOW.isoformat(), "current": True}, alarms=[], freshness_rows=[],
        selector=selector, forward_trial=trial)
    return payload, dv.render_decision_html(payload, nav_links=())


def test_not_enough_data_selector_and_refused_freeze_render_honestly():
    payload, html = _html(dv.selector_view(SELECTOR, CURRENT), dv.forward_trial_view(TRIAL, CURRENT))
    assert payload["selector"]["state"] == dv.INSUFFICIENT
    assert 'id=\'decisionSelectorVerdict\'' in html and "NOT ENOUGH DATA: fixed_oos_closes n=8" in html
    assert "decisionSelectorRegimes" in html and "BEAR|ADX_LT20|ATR_LOW" in html
    assert "NO_QUALIFYING_CANDIDATE" in html and "ev_ci95_above_zero" in html
    assert "cannot change runtime" in html


def test_reports_outside_the_current_generation_are_no_data():
    stale = {"status": "UNAVAILABLE_CURRENT_GENERATION", "blockers": ["EPOCH_ID_MISMATCH"]}
    selector = dv.selector_view(SELECTOR, stale)
    trial = dv.forward_trial_view(TRIAL, stale)
    assert selector["state"] == dv.NO_DATA and "EPOCH_ID_MISMATCH" in selector["text"]
    assert trial["state"] == dv.NO_DATA and trial["status"] is None
    _payload, html = _html(selector, trial)
    assert "NOT ENOUGH DATA: fixed_oos_closes" not in html and "NO_QUALIFYING_CANDIDATE" not in html
    assert html.count("no data yet (EPOCH_ID_MISMATCH)") == 2


def test_missing_sections_default_to_no_data():
    payload, html = _html(None, None)
    assert payload["selector"]["state"] == dv.NO_DATA and payload["forward_trial"]["state"] == dv.NO_DATA
    assert "Fixed tile vs dynamic selector" in html and "Forward trial" in html


def test_active_trial_shows_daily_rows_and_drift():
    trial = {"status": "INVALIDATED_IDENTITY_DRIFT", "status_text": "Frozen A vs control B",
             "manifest": {"manifest_id": "forward-trial-freeze-abc"},
             "tracker": {"drift": ["REGISTRY_SIGNATURE_CHANGED"], "daily": [
                 {"day": 1, "candidate": {"n": 3, "mean_usd": 0.01}, "control": {"n": 2, "mean_usd": None},
                  "ev_diff_usd": None}]}}
    view = dv.forward_trial_view(trial, CURRENT)
    assert view["manifest_id"] == "forward-trial-freeze-abc"
    _payload, html = _html(None, view)
    assert "decisionTrialDaily" in html and "REGISTRY_SIGNATURE_CHANGED" in html
