"""Human-readable genome and discovery identity labels."""
from __future__ import annotations

from typing import Any, Dict, Optional

_ADX_SHORT = {
    "LOW_ADX": "LOW_VOL",
    "MID_ADX": "MID_VOL",
    "HIGH_ADX": "HIGH_VOL",
}

_SPREAD_SHORT = {
    "SPREAD5+": "SPREAD5P",
    "SPREAD4": "SPREAD4",
    "SPREAD3": "SPREAD3",
    "SPREAD2": "SPREAD2",
    "SPREAD1": "SPREAD1",
    "SPREAD0": "SPREAD0",
}


def _norm(fp: Dict[str, Any], key: str, default: str = "") -> str:
    val = fp.get(key)
    if val is None or val == "":
        return default
    return str(val).upper().replace(" ", "_")


def genome_identity_label(
    fingerprint: Optional[Dict[str, Any]] = None,
    *,
    representative: Optional[Dict[str, Any]] = None,
) -> str:
    """Descriptive label, e.g. WEEKEND_US_HIGH_VOL_SPREAD5P_SHORT."""
    fp = dict(fingerprint or {})
    if representative:
        fp = {**representative, **fp}
    parts: list[str] = []
    if fp.get("is_weekend") is True or fp.get("weekend") is True:
        parts.append("WEEKEND")
    elif fp.get("is_weekend") is False or fp.get("weekend") is False:
        parts.append("WEEKDAY")
    session = _norm(fp, "session")
    if session and session not in ("UNKNOWN", "?"):
        parts.append(session)
    adx = _ADX_SHORT.get(str(fp.get("adx_bucket") or ""), _norm(fp, "adx_bucket"))
    if adx and adx not in ("?", "UNKNOWN"):
        parts.append(adx)
    spread = _SPREAD_SHORT.get(str(fp.get("spread_bucket") or ""), _norm(fp, "spread_bucket"))
    if spread and spread not in ("?", "UNKNOWN"):
        parts.append(spread)
    regime = _norm(fp, "regime")
    if regime and regime not in ("?", "UNKNOWN"):
        parts.append(regime)
    direction = _norm(fp, "direction")
    if direction and direction not in ("?", "UNKNOWN"):
        parts.append(direction)
    if fp.get("funding_window"):
        parts.append("FUNDING")
    return "_".join(parts) if parts else "UNLABELED_GENOME"


def discovery_identity_label(fingerprint: Dict[str, Any]) -> str:
    return genome_identity_label(fingerprint)
