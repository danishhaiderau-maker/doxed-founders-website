"""Paper-only fixed ATR target family tile."""
from family_policy_common import PolicySpec, account_risk_quantity as _size, chase_due as _chase, dashboard_policy as _dashboard, entry_fields as _entry, exit_action as _exit, exit_config as _config, marketable_quote_at_limit

POLICY_ID = "OFFSET_0.27_CHASE_w234_s50_i180|ATR_TP_2.5_SCENARIO_C"
LANE = "FAMILY_ATR_TARGET_2_5"
SPEC = PolicySpec(policy_id=POLICY_ID, lane=LANE, label="ATR target 2.5 + Scenario C", family="ATR_TARGET", entry_offset_pct=0.27, chase_windows=(2, 3, 4), chase_interval_sec=180, chase_step=0.50, initial_stop_atr_k=None, thesis_cut_margin_pct=-12.0, thesis_window_sec=300, atr_target_k=2.5, trail_ladder=((8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120)), ladder_label="8→5, 12→10, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120", ladder_profile_id="SCENARIO_C_RUNNER_8_v8_20260820")
CHASE_STEP = SPEC.chase_step
def entry_fields(direction, reference_price): return _entry(SPEC, direction, reference_price)
def chase_due(**kwargs): return _chase(SPEC, **kwargs)
def account_risk_quantity(**kwargs): return _size(SPEC, **kwargs)
def exit_action(**kwargs): return _exit(SPEC, **kwargs)
def exit_config(analyzer_sync_id): return _config(SPEC, analyzer_sync_id)
def dashboard_policy(): return _dashboard(SPEC)
