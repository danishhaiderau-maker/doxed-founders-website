"""Transition matrix — regime / session transitions (analyzer-derived)."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List


def empty_transition_matrix() -> Dict[str, Any]:
    return {"states": [], "matrix": []}


def summarize_transitions(outcome_fingerprints: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not outcome_fingerprints:
        return empty_transition_matrix()
    sessions = Counter(str(f.get("session") or "?") for f in outcome_fingerprints)
    outcomes = Counter(str(f.get("outcome") or "?") for f in outcome_fingerprints)
    return {
        "states": list(sessions.keys()),
        "session_distribution": dict(sessions),
        "outcome_distribution": dict(outcomes),
        "matrix": [],
        "note": "Full transition matrix activates when lifecycle chain density ≥ 500 events",
    }
