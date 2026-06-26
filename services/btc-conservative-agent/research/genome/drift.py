"""Feature drift detector — compares recent vs baseline DNA quality."""
from __future__ import annotations

from typing import Any, Dict


def detect_drift(current: dict, baseline: dict) -> dict:
    cur_ev = float(current.get("ev") or 0)
    base_ev = float(baseline.get("ev") or 0)
    cur_n = int(current.get("sample_size") or 0)
    base_n = int(baseline.get("sample_size") or 0)
    ev_delta = round(cur_ev - base_ev, 4)
    drift_detected = abs(ev_delta) > 0.25 and min(cur_n, base_n) >= 10
    return {
        "drift_detected": drift_detected,
        "ev_delta_usd": ev_delta,
        "current_ev": cur_ev,
        "baseline_ev": base_ev,
        "current_sample": cur_n,
        "baseline_sample": base_n,
        "details": (
            [f"EV shifted {ev_delta:+.2f} USD/trade (recent vs earlier sample)"]
            if drift_detected
            else ["No significant drift in current sample split"]
        ),
    }
