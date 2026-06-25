"""Discovery engine — genome patterns → NEW DISCOVERY cards (advisory only)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from research.genome.quality_score import summarize_trades


def _discovery_id(seq: int) -> str:
    return f"DNA-{seq:03d}"


def generate_discoveries(
    outcome_fingerprints: List[Dict[str, Any]],
    min_sample: int = 10,
) -> List[Dict[str, Any]]:
    """Group by DNA fingerprint key; emit discoveries when sample + EV criteria met."""
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for fp in outcome_fingerprints:
        key = "|".join([
            "WEEKEND" if fp.get("is_weekend") else "WEEKDAY",
            str(fp.get("session") or "?"),
            str(fp.get("adx_bucket") or "?"),
            str(fp.get("spread_bucket") or "?"),
            str(fp.get("direction") or "?"),
        ])
        buckets[key].append(fp)

    discoveries: List[Dict[str, Any]] = []
    seq = 1
    now = datetime.now(timezone.utc).isoformat()
    for key, rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        pnls = [float(r.get("pnl_usd") or 0) for r in rows]
        summary = summarize_trades([{"pnl_usd": p} for p in pnls])
        if summary["sample_size"] < min_sample:
            continue
        sample = rows[0]
        ev = summary["ev"]
        conf = summary["research_confidence"]
        if ev <= 0 and summary["sample_size"] < 30:
            continue
        status = "SUPPORTED" if ev > 0 else "WATCH"
        discoveries.append({
            "discovery_id": _discovery_id(seq),
            "title": f"NEW DISCOVERY — {_discovery_id(seq)}",
            "first_observed": now[:7],
            "observed_trades": summary["sample_size"],
            "fingerprint": {
                "weekend": sample.get("is_weekend"),
                "session": sample.get("session"),
                "adx_bucket": sample.get("adx_bucket"),
                "spread_bucket": sample.get("spread_bucket"),
                "direction": sample.get("direction"),
                "regime": sample.get("regime"),
            },
            "metrics": {
                "win_rate": summary["win_rate"],
                "ev_usd": summary["ev"],
                "dna_quality": summary["dna_quality"],
                "confidence_interval_95": summary["confidence_interval_95"],
            },
            "research_confidence": conf,
            "status": status,
            "recommendation": (
                "Continue observing — advisory only; analyzer never changes execution."
                if status == "SUPPORTED"
                else "Monitor — negative EV in current sample."
            ),
            "dna_key": key,
        })
        seq += 1
        if seq > 12:
            break
    return discoveries
