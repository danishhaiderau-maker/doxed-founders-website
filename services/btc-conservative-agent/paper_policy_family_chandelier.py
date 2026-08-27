"""Paper-only Chandelier family tile."""
from family_policy_common import PolicySpec, account_risk_quantity as _size, chase_due as _chase, dashboard_policy as _dashboard, entry_fields as _entry, exit_action as _exit, exit_config as _config, marketable_quote_at_limit

POLICY_ID = "OFFSET_0.30_CHASE_w234_s50_i180|CHANDELIER_1.5"
LANE = "FAMILY_CHANDELIER_3"
SPEC = PolicySpec(policy_id=POLICY_ID, lane=LANE, label="Chandelier 1.5 ATR hypothesis", family="CHANDELIER", entry_offset_pct=0.30, chase_windows=(2, 3, 4), chase_interval_sec=180, chase_step=0.50, initial_stop_atr_k=2.0, chandelier_atr_k=1.5, trail_activation_atr_k=1.0)
CHASE_STEP = SPEC.chase_step
def entry_fields(direction, reference_price): return _entry(SPEC, direction, reference_price)
def chase_due(**kwargs): return _chase(SPEC, **kwargs)
def account_risk_quantity(**kwargs): return _size(SPEC, **kwargs)
def exit_action(**kwargs): return _exit(SPEC, **kwargs)
def exit_config(analyzer_sync_id): return _config(SPEC, analyzer_sync_id)
def dashboard_policy(): return _dashboard(SPEC)
