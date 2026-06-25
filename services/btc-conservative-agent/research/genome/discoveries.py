"""Discovery engine — persistent knowledge, not throwaway reports."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from research.genome.library_store import GenomeLibraryStore
from research.genome.quality_score import summarize_trades


def _discovery_id_from_key(dna_key: str, existing: Dict[str, Dict[str, Any]]) -> str:
    if dna_key in existing:
        return str(existing[dna_key].get("discovery_id") or f"DNA-{hash(dna_key) % 1000:03d}")
    return f"DNA-{len(existing) + 1:03d}"


def _classify_discovery(
    summary: Dict[str, Any],
    prev: Dict[str, Any] | None,
) -> str:
    ev = float(summary.get("ev") or 0)
    n = int(summary.get("sample_size") or 0)
    if not prev:
        return "NEW_DISCOVERY"
    prev_ev = float((prev.get("metrics") or {}).get("ev_usd") or 0)
    prev_n = int(prev.get("observed_trades") or 0)
    if ev <= 0 and prev_ev > 0 and n >= 10:
        return "DISCOVERY_INVALIDATED"
    if ev < prev_ev - 0.25 and n >= prev_n:
        return "DISCOVERY_WEAKENED"
    if ev > prev_ev + 0.25 and n >= prev_n:
        return "DISCOVERY_STRENGTHENED"
    if n > prev_n + 5:
        return "REPEATING_DISCOVERY"
    return str(prev.get("status") or "REPEATING_DISCOVERY")


def generate_discoveries(
    outcome_fingerprints: List[Dict[str, Any]],
    store: GenomeLibraryStore,
    min_sample: int = 10,
) -> List[Dict[str, Any]]:
    """Evidence-based discoveries — persisted across analyzer cycles."""
    prev_map = store.load_discoveries()

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
    now = datetime.now(timezone.utc).isoformat()

    for key, rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        pnls = [float(r.get("pnl_usd") or 0) for r in rows]
        summary = summarize_trades([{"pnl_usd": p} for p in pnls])
        if summary["sample_size"] < min_sample:
            continue
        if summary["ev"] <= 0 and summary["sample_size"] < 30:
            continue

        prev = prev_map.get(key)
        status = _classify_discovery(summary, prev)
        sample = rows[0]
        disc_id = _discovery_id_from_key(key, prev_map)

        disc = {
            "discovery_id": disc_id,
            "title": status.replace("_", " "),
            "status": status,
            "first_observed": (prev or {}).get("first_observed") or now[:10],
            "last_observed": now,
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
            "research_confidence": summary["research_confidence"],
            "evidence": f"n={summary['sample_size']} EV={summary['ev']} WR={summary['win_rate']:.1%}",
            "supporting_genomes": [g for g in [key] if key],
            "recommendation": _recommendation(status, summary),
            "dna_key": key,
        }
        store.save_discovery(disc)
        discoveries.append(disc)
        if len(discoveries) >= 12:
            break

    return discoveries


def _recommendation(status: str, summary: Dict[str, Any]) -> str:
    conf = summary.get("research_confidence") or "LOW"
    if status == "DISCOVERY_INVALIDATED":
        return "Monitor kill criteria — advisory only."
    if status in ("NEW_DISCOVERY", "REPEATING_DISCOVERY", "DISCOVERY_STRENGTHENED"):
        if conf == "LOW":
            return "Collect more data — insufficient sample for strong claims."
        return "Continue observing — advisory only; analyzer never changes execution."
    if status == "DISCOVERY_WEAKENED":
        return "Discovery weakening — review on next cycle."
    return "Collect only."
