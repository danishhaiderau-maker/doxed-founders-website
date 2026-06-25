"""Discovery engine — persistent knowledge with statistical evidence."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from research.genome.evidence import (
    build_explanation,
    ledger_snapshot,
    stability_from_ledger,
    statistical_evidence,
)
from research.genome.identity import discovery_identity_label
from research.genome.library_store import GenomeLibraryStore


def _discovery_id_from_key(dna_key: str, existing: Dict[str, Dict[str, Any]]) -> str:
    if dna_key in existing:
        return str(existing[dna_key].get("discovery_id") or f"DNA-{hash(dna_key) % 1000:03d}")
    return f"DNA-{len(existing) + 1:03d}"


def _classify_discovery(stats: Dict[str, Any], prev: Dict[str, Any] | None) -> str:
    ev = float(stats.get("expected_value_usd") or 0)
    n = int(stats.get("sample_size") or 0)
    if not prev:
        return "NEW_DISCOVERY"
    prev_ev = float((prev.get("statistical_evidence") or prev.get("metrics") or {}).get("expected_value_usd")
                    or (prev.get("metrics") or {}).get("ev_usd") or 0)
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
    benchmark_ev: float = 0.0,
    min_sample: int = 10,
) -> List[Dict[str, Any]]:
    """Evidence-based discoveries — persisted with ledger history."""
    prev_map = store.load_discoveries()
    now = datetime.now(timezone.utc).isoformat()

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

    for key, rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        pnls = [float(r.get("pnl_usd") or 0) for r in rows]
        stats = statistical_evidence(pnls)
        if stats["sample_size"] < min_sample:
            continue
        if stats["expected_value_usd"] <= 0 and stats["sample_size"] < 30:
            continue

        prev = prev_map.get(key)
        disc_id = _discovery_id_from_key(key, prev_map)
        status = _classify_discovery(stats, prev)
        sample = rows[0]
        fp = {
            "weekend": sample.get("is_weekend"),
            "session": sample.get("session"),
            "adx_bucket": sample.get("adx_bucket"),
            "spread_bucket": sample.get("spread_bucket"),
            "direction": sample.get("direction"),
            "regime": sample.get("regime"),
        }
        identity = discovery_identity_label(fp)

        wr = round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0
        history = store.load_ledger("discovery", disc_id)
        store.append_ledger(
            "discovery",
            disc_id,
            ledger_snapshot(
                "discovery",
                disc_id,
                now,
                {
                    "win_rate": wr,
                    "ev_usd": stats["expected_value_usd"],
                    "sample_size": stats["sample_size"],
                    "dna_quality": stats["dna_quality"],
                },
            ),
        )
        history = store.load_ledger("discovery", disc_id)
        stability = stability_from_ledger(history)

        disc = {
            "discovery_id": disc_id,
            "identity": identity,
            "title": identity.replace("_", " "),
            "status": status,
            "first_observed": (prev or {}).get("first_observed") or now[:10],
            "last_observed": now,
            "observed_trades": stats["sample_size"],
            "fingerprint": {
                "weekend": fp["weekend"],
                "session": fp["session"],
                "adx_bucket": fp["adx_bucket"],
                "spread_bucket": fp["spread_bucket"],
                "direction": fp["direction"],
                "regime": fp["regime"],
            },
            "statistical_evidence": stats,
            "metrics": {
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0,
                "ev_usd": stats["expected_value_usd"],
                "dna_quality": stats["dna_quality"],
                "confidence_interval_95": stats["confidence_interval_95"],
            },
            "stability": stability,
            "evidence_ledger": history[-8:],
            "research_confidence": stats["research_confidence"],
            "recommendation": _recommendation(status, stats),
            "dna_key": key,
        }
        disc["explanation"] = build_explanation(
            discovery=disc,
            stats=stats,
            stability=stability,
            benchmark_ev=benchmark_ev,
        )
        store.save_discovery(disc)
        discoveries.append(disc)
        if len(discoveries) >= 12:
            break

    return discoveries


def _recommendation(status: str, stats: Dict[str, Any]) -> str:
    if not stats.get("recommendation_allowed"):
        return "Collect only — sample too small or confidence LOW."
    if status == "DISCOVERY_INVALIDATED":
        return "Monitor kill criteria — advisory only."
    if status in ("NEW_DISCOVERY", "REPEATING_DISCOVERY", "DISCOVERY_STRENGTHENED"):
        if not stats.get("statistically_significant"):
            return "Continue collecting — EV positive but not yet statistically significant."
        return "Continue observing — advisory only; analyzer never changes execution."
    if status == "DISCOVERY_WEAKENED":
        return "Discovery weakening — review on next cycle."
    return "Collect only."
