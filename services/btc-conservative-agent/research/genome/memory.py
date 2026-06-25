"""Genome Memory — update living genome entities (persistence, half-life, frequency)."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from research.genome.evidence import ledger_snapshot, stability_from_ledger
from research.genome.fingerprints import fingerprint_key, market_fingerprint
from research.genome.identity import genome_identity_label
from research.genome.library_store import GenomeLibraryStore
from research.genome.quality_score import dna_quality, summarize_trades


def _genome_id_from_key(key: str, seq: int) -> str:
    return f"GENOME-{seq:03d}"


def _dominant(counter: Counter) -> Optional[str]:
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def _half_life_days(first_seen: str, last_seen: str, observations: int) -> Optional[float]:
    try:
        t0 = datetime.fromisoformat(str(first_seen).replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
        span = max((t1 - t0).total_seconds() / 86400.0, 0.01)
        if observations <= 1:
            return round(span, 1)
        return round(span / max(observations - 1, 1), 1)
    except (TypeError, ValueError):
        return None


def merge_cluster_into_library(
    store: GenomeLibraryStore,
    cluster_candidates: List[Dict[str, Any]],
    existing: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge this cycle's fingerprint buckets into persistent genome entities."""
    existing_by_fp = {g.get("fingerprint_key"): g for g in existing if g.get("fingerprint_key")}
    id_map = {g.get("fingerprint_key"): g.get("genome_id") for g in existing if g.get("fingerprint_key")}
    next_seq = len(existing) + 1
    updated: List[Dict[str, Any]] = []

    for cand in cluster_candidates:
        fp_key = str(cand.get("fingerprint_key") or "")
        if not fp_key:
            continue
        rep = cand.get("representative") or {}
        trade_summary = summarize_trades(
            [{"pnl_usd": cand.get("ev_usd") or 0}] * max(int(cand.get("trade_count") or 0), 0)
        ) if cand.get("trade_count") else {"sample_size": 0, "ev": 0, "win_rate": 0, "dna_quality": 0, "research_confidence": "LOW"}

        prev = existing_by_fp.get(fp_key) or {}
        sessions = Counter(prev.get("_session_counts") or {})
        sessions[str(rep.get("session") or "?")] += int(cand.get("market_observations") or 1)
        weekends = Counter(prev.get("_weekend_counts") or {})
        weekends["weekend" if rep.get("is_weekend") else "weekday"] += int(cand.get("market_observations") or 1)

        genome_id = id_map.get(fp_key) or _genome_id_from_key(fp_key, next_seq)
        if fp_key not in id_map:
            next_seq += 1
            id_map[fp_key] = genome_id

        ev = float(cand.get("ev_usd") or trade_summary.get("ev") or 0)
        n = int(cand.get("trade_count") or trade_summary.get("sample_size") or 0)
        wr = float(trade_summary.get("win_rate") or 0)

        body = {
            "genome_id": genome_id,
            "identity": genome_identity_label(representative=rep),
            "fingerprint_key": fp_key,
            "representative": rep,
            "centroid": cand.get("centroid") or {},
            "new_observations": int(cand.get("market_observations") or 1),
            "trade_count": n,
            "average_ev": round(ev, 4),
            "median_ev": round(ev, 4),
            "win_rate": round(wr, 4),
            "dna_quality": dna_quality(n, wr, ev),
            "research_confidence": trade_summary.get("research_confidence") or "LOW",
            "dominant_session": _dominant(sessions),
            "dominant_weekday": _dominant(weekends),
            "dominant_regime": rep.get("regime"),
            "dominant_adx_bucket": rep.get("adx_bucket"),
            "dominant_spread_bucket": rep.get("spread_bucket"),
            "stability_score": round(min(1.0, n / 100.0), 3),
            "current_status": "ACTIVE" if n >= 10 else "COLLECTING",
            "_session_counts": dict(sessions),
            "_weekend_counts": dict(weekends),
            "previous_ev": prev.get("average_ev"),
            "strengthening": (
                ev > float(prev.get("average_ev") or ev) + 0.1
                if prev.get("average_ev") is not None and n >= 5
                else None
            ),
        }
        body["half_life_days"] = _half_life_days(
            prev.get("first_seen") or datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat(),
            int(prev.get("observations") or 0) + body["new_observations"],
        )
        merged = store.upsert_genome(genome_id, fp_key, body)
        store.append_ledger(
            "genome",
            genome_id,
            ledger_snapshot(
                "genome",
                genome_id,
                datetime.now(timezone.utc).isoformat(),
                {
                    "win_rate": merged.get("win_rate"),
                    "ev_usd": merged.get("average_ev"),
                    "trade_count": merged.get("trade_count"),
                    "dna_quality": merged.get("dna_quality"),
                    "trend": merged.get("strengthening"),
                },
            ),
        )
        history = store.load_ledger("genome", genome_id)
        merged["evidence_ledger"] = history[-8:]
        merged["stability"] = stability_from_ledger(history)
        updated.append(merged)

    return updated
