"""Explicit paper experiment admission; never mutates the original AI verdict."""
from copy import deepcopy
import math

TREATMENT_ID = "SCORE_LED_PAPER_V1"


def project_score_led_paper(ai, *, spec, force_paper, live_armed, inverted=False):
    """Return (child execution projection, reason); retain full original AI.

    This is not an AI approval, confidence estimate, fill, or live permission.
    A valid higher directional score is the sole *admission* experiment.
    Existing downstream market/order/protection gates remain authoritative.
    """
    if spec.get("admission_treatment") != TREATMENT_ID:
        return None, "SCORE_LED_NOT_ENABLED"
    if (force_paper is not True or live_armed is not False or inverted
            or spec.get("paper_only") is not True
            or spec.get("platform_relay_eligible") is not False
            or spec.get("live_copy_eligible") is not False):
        return None, "SCORE_LED_PAPER_BOUNDARY_REQUIRED"
    if not isinstance(ai, dict) or not ai or ai.get("ai_error"):
        return None, "SCORE_LED_AI_ERROR"
    if str(ai.get("decision") or "").upper() in {"AI_ERROR", "ERROR"}:
        return None, "SCORE_LED_AI_ERROR"
    scores = []
    factors = ai.get("factors") if isinstance(ai.get("factors"), dict) else {}
    for key in ("long_score", "short_score"):
        value = ai.get(key, factors.get(key))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, "SCORE_LED_SCORE_MISSING_OR_MALFORMED"
        if not math.isfinite(value) or not 0 <= value <= 100:
            return None, "SCORE_LED_SCORE_OUT_OF_RANGE"
        scores.append(value)
    if scores[0] == scores[1]:
        return None, "SCORE_LED_SCORE_TIE"
    child = deepcopy(ai)
    child["original_ai_snapshot"] = deepcopy(ai)
    child["raw_decision"] = ai.get("raw_decision") or ai.get("decision")
    child["admission_treatment"] = TREATMENT_ID
    child["admission_is_ai_approval"] = False
    child["direction"] = "LONG" if scores[0] > scores[1] else "SHORT"
    child["candidate_direction"] = child["direction"]
    # Compatibility execution projection only. Original verdict is immutable
    # under original_ai_snapshot, and shared AI history is never replaced.
    child["decision"] = "APPROVE"
    child["approved"] = True
    child["execution_tier"] = "APPROVE"
    child["research_soft"] = "APPROVE"
    child["paper_only"] = True
    child["platform_relay_eligible"] = False
    return child, "SCORE_LED_HIGHER_DIRECTION_PAPER_ONLY"
