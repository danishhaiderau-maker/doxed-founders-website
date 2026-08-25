"""Paper-only ATR trailing family tile."""
from family_policy_common import PolicySpec, account_risk_quantity as _size, chase_due as _chase, dashboard_policy as _dashboard, entry_fields as _entry, exit_action as _exit, exit_config as _config, marketable_quote_at_limit

POLICY_ID = "OFFSET_0.04_CHASE_all_on_s50_i60|ATR_TRAIL_SL_2_ARM_1.25_TRAIL_1"
LANE = "FAMILY_ATR_TRAIL"
SPEC = PolicySpec(policy_id=POLICY_ID, lane=LANE, label="ATR trail balance", family="ATR_TRAIL", entry_offset_pct=0.04, chase_windows=(0, 1, 2, 3, 4, 5), chase_interval_sec=60, chase_step=0.50, initial_stop_atr_k=2.0, trail_activation_atr_k=1.25, trail_atr_k=1.0)
CHASE_STEP = SPEC.chase_step
def entry_fields(direction, reference_price): return _entry(SPEC, direction, reference_price)
def chase_due(**kwargs): return _chase(SPEC, **kwargs)
def account_risk_quantity(**kwargs): return _size(SPEC, **kwargs)
def exit_action(**kwargs): return _exit(SPEC, **kwargs)
def exit_config(analyzer_sync_id): return _config(SPEC, analyzer_sync_id)
def dashboard_policy(): return _dashboard(SPEC)
