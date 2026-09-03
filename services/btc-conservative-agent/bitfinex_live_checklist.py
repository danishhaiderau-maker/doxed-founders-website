"""Bitfinex live-copy technical checklist — DO NOT ARM from this document.

Paper-only research may complete every box below and still remain disarmed.
Arming requires a later explicit user authorization after qualification.
"""

CHECKLIST_SCHEMA = "bitfinex_live_readiness_checklist_v1"

BITFINEX_LIVE_CHECKLIST = [
    {"id": "FORCE_PAPER_STILL_ON", "required": True, "arm_if_fail": False,
     "note": "force_paper_mode must remain true until explicit arm approval"},
    {"id": "LIVE_ARMED_FALSE", "required": True, "arm_if_fail": False,
     "note": "live_armed must be false during this plan"},
    {"id": "RELAY_OFF_OR_ALLOWLIST_EMPTY", "required": True, "arm_if_fail": False,
     "note": "no historical paper state may be copied"},
    {"id": "SIZE_0_20_TO_0_25_MARGIN_100X", "required": True, "arm_if_fail": False,
     "note": "margin input only; not a max-loss guarantee; fail closed on upward rounding"},
    {"id": "EXCHANGE_MIN_QTY_ACCEPTS_SIZE", "required": True, "arm_if_fail": False},
    {"id": "STOP_COVERAGE_REDUCE_ONLY", "required": True, "arm_if_fail": False},
    {"id": "PARTIAL_REDUCTION_PROVEN", "required": True, "arm_if_fail": False},
    {"id": "RECONCILIATION_GREEN", "required": True, "arm_if_fail": False},
    {"id": "ANALYZER_PARITY_CURRENT_EPOCH", "required": True, "arm_if_fail": False},
    {"id": "CONSERVATIVE_OOS_PASS", "required": True, "arm_if_fail": False},
    {"id": "EXPLICIT_USER_ARM_AUTH", "required": True, "arm_if_fail": False,
     "note": "not granted by this Phase 0-4 plan"},
]


def checklist_receipt(*, checks: dict | None = None) -> dict:
    """Return a fail-closed readiness receipt. Never sets live_armed."""
    rows = []
    for item in BITFINEX_LIVE_CHECKLIST:
        status = "UNKNOWN"
        if checks and item["id"] in checks:
            status = "PASS" if checks[item["id"]] else "FAIL"
        rows.append({**item, "status": status})
    blocked = [r["id"] for r in rows if r["status"] != "PASS"]
    return {
        "schema": CHECKLIST_SCHEMA,
        "status": "NOT_ARMED",
        "live_armed": False,
        "bitfinex_arm_allowed": False,
        "force_paper_mode_required": True,
        "checks": rows,
        "blockers": blocked,
        "note": "Checklist only. Completing checks does not arm Bitfinex.",
    }
