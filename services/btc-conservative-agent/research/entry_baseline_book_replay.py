"""Same-opportunity entry baseline replay on the shared ``book_limit_entry`` helper (RK R1 ruling, 25 Oct 2026).

Replaces the retired 1 s ``market_microstructure_1s`` / ``evaluate_limit_fill`` pricing of
``entry_baseline_replay_report``.  Every baseline schedule of every current-epoch opportunity is walked at full
requested size against the real 10-level book (best bid/ask and depth) plus the episode's *measured* delay, resting
on real trade prints, exactly like the canonical CONSERVATIVE entry (research_v3_candidates).

* Current epoch only: an opportunity row from any other epoch is hard-rejected (never replayed, listed by reason).
* Every result records the price age (``entry_book_age_ms``) of the book row it was priced on.
* Rows that cannot be priced on the book are listed in ``non_book_rows`` by reason and trade/episode id.
* The report is stamped with the book/trades source paths, their sha256 and the epoch.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SCHEMA = "entry_baseline_book_replay_v1"
PRICE_SOURCE = "BFX_BOOK_10L_1S_WALK"
OUTCOMES = frozenset({"FULL_FILL", "PARTIAL_FILL", "NO_FILL"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _schedules(row: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = row.get("baseline_schedule_snapshot")
    schedules = snapshot.get("schedules") if isinstance(snapshot, Mapping) else row.get("baseline_schedules")
    return schedules if isinstance(schedules, Mapping) else {}


def _row_epoch(row: Mapping[str, Any]) -> str:
    causal = row.get("causal_identity") if isinstance(row.get("causal_identity"), Mapping) else {}
    return str(row.get("epoch_id") or row.get("dataset_epoch") or causal.get("dataset_epoch") or "")


def _ids(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"episode_id": row.get("episode_id"),
            "opportunity_id": row.get("opportunity_id") or row.get("record_id"),
            "trade_id": row.get("trade_id") or row.get("linked_trade_id")}


def _intervals(schedule: Any) -> list[dict[str, Any]]:
    out = []
    for index, interval in enumerate(schedule if isinstance(schedule, list) else []):
        if not isinstance(interval, Mapping):
            return []
        start, end, limit = (_num(interval.get(k)) for k in ("start_ts", "end_ts", "limit_price"))
        if start is None or end is None or limit is None or end <= start or limit <= 0:
            return []
        out.append({"bucket_id": str(interval.get("bucket_id") or index), "start_ts": start,
                    "end_ts": end, "limit_price": limit})
    return out


def source_provenance(data_dir: str | Path, epoch_id: str) -> dict[str, Any]:
    from research_v3_book_walk import BOOK_FILE, TRADES_FILE, rotation_paths
    files = []
    for name in (BOOK_FILE, TRADES_FILE):
        for path in rotation_paths(data_dir, name):
            files.append({"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size})
    return {"epoch_id": epoch_id, "price_source": PRICE_SOURCE, "files": files}


def read_opportunities(data_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(data_dir) / "v3" / "ledgers" / "opportunity.jsonl"
    if not path.is_file():
        raise FileNotFoundError("V3_OPPORTUNITY_LEDGER_MISSING")
    rows = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)  # a corrupt line fails the whole report closed
            if not isinstance(row, dict):
                raise ValueError(f"V3_OPPORTUNITY_ROW_INVALID:{number}")
            rows.append(row)
    return rows


def build_entry_baseline_book_replay(
    data_dir: str | Path, generation: Mapping[str, Any], *,
    opportunities: Iterable[Mapping[str, Any]] | None = None,
    tape_loader: Callable[[Any, Any], Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from book_limit_entry import book_limit_entry
    epoch = str((generation or {}).get("epoch_id") or "").strip()
    if not epoch:
        raise ValueError("ENTRY_BASELINE_GENERATION_EPOCH_MISSING")
    rows = list(read_opportunities(data_dir) if opportunities is None else opportunities)
    rejected_epoch: list[dict[str, Any]] = []
    current: list[Mapping[str, Any]] = []
    for row in rows:
        row_epoch = _row_epoch(row)
        if row_epoch != epoch:
            rejected_epoch.append({**_ids(row), "row_epoch": row_epoch or None,
                                   "reason": "OTHER_EPOCH_HARD_REJECTED"})
        else:
            current.append(row)
    jobs, non_book = [], []

    def non(row, baseline_id, *reasons):
        non_book.append({**_ids(row), "baseline_id": baseline_id, "reasons": [str(r) for r in reasons]})

    for row in current:
        schedules = _schedules(row)
        if not schedules:
            non(row, None, "BASELINE_SCHEDULES_MISSING")
            continue
        declaration = (row.get("directional_capture") or {}).get("research_context_declaration") \
            if isinstance(row.get("directional_capture"), Mapping) else None
        for baseline_id, envelope in sorted(schedules.items()):
            intervals = _intervals((envelope or {}).get("schedule") if isinstance(envelope, Mapping) else None)
            delay = _num(row.get("latency_sec"))
            qty = _num(row.get("requested_remaining_qty") if baseline_id == "FINAL_MARKET_AFTER_EXPIRY"
                       else row.get("requested_qty"))
            reasons = []
            if declaration is not None:
                reasons.append("DECLARED_CONTEXT_NOT_MEASURED_DELAY")
            if not intervals:
                reasons.append("BASELINE_SCHEDULE_MISSING_OR_INVALID")
            if delay is None or delay < 0:
                reasons.append("MEASURED_DELAY_MISSING")
            if qty is None or qty <= 0:
                reasons.append("REQUESTED_QTY_MISSING")
            if str(row.get("direction") or "").upper() not in ("LONG", "SHORT"):
                reasons.append("DIRECTION_NOT_EXECUTABLE")
            if reasons:
                non(row, baseline_id, *reasons)
                continue
            jobs.append((row, baseline_id, intervals, delay, qty))
    results = []
    if jobs:
        if tape_loader is None:
            from research_v3_book_walk import load_book_walk as tape_loader
        windows = [(j[2][0]["start_ts"], j[2][-1]["end_ts"] + j[3]) for j in jobs]
        tape = tape_loader(data_dir, windows)
        for (row, baseline_id, intervals, delay, qty), (t0, t1) in zip(jobs, windows):
            cover = tape.coverage(t0, t1)
            if not cover.get("covered"):
                non(row, baseline_id, cover.get("reason") or "BOOK_NOT_COVERED")
                continue
            window = tape.window(t0, t1)
            receipt = book_limit_entry(
                str(row.get("direction")).upper(), qty, intervals, window.book_at,
                trades_between=window.trades_between, gap_clause_at=window.gap_clause_at,
                delay_s=delay, delay_source="measured_latency_sec",
                quantity_constraints=row.get("signed_quantity_constraints"),
                symbol=str(row.get("symbol") or "tBTCF0:USTF0"))
            outcome = str(receipt.get("final_classification") or receipt.get("outcome") or "").upper()
            if receipt.get("supported") is not True or outcome not in OUTCOMES:
                non(row, baseline_id, *(receipt.get("negative_reasons") or ["BOOK_LIMIT_ENTRY_UNSUPPORTED"]))
                continue
            results.append({**_ids(row), "baseline_id": baseline_id, "outcome_state": outcome,
                            "requested_qty": qty, "filled_qty": receipt.get("filled_qty"),
                            "fill_price": receipt.get("fill_price"), "fill_ts": receipt.get("fill_ts"),
                            "delay_s": receipt.get("delay_s"), "delay_source_kind": receipt.get("delay_source_kind"),
                            "price_age_ms": receipt.get("entry_book_age_ms"), "price_source": PRICE_SOURCE,
                            "evidence_status": receipt.get("evidence_status"),
                            "schedule_sha256": receipt.get("schedule_sha256"), "epoch_id": epoch,
                            "book_receipt": receipt})
    reason_counts: dict[str, int] = {}
    for item in non_book:
        for reason in item["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    summaries: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = summaries.setdefault(item["baseline_id"], {k: 0 for k in sorted(OUTCOMES)})
        bucket[item["outcome_state"]] += 1
    return {
        "schema": SCHEMA, "status": "OK" if results else "NO_BOOK_PRICED_RESULTS",
        "epoch_id": epoch, "price_source": PRICE_SOURCE, "helper": "book_limit_entry",
        "generation": dict(generation),
        "source_provenance": dict(provenance) if provenance is not None else source_provenance(data_dir, epoch),
        "opportunities_read": len(rows), "current_epoch_opportunities": len(current),
        "same_opportunity_count": len({(r["episode_id"], r["opportunity_id"]) for r in results}),
        "other_epoch_rejected": rejected_epoch, "other_epoch_rejected_count": len(rejected_epoch),
        "results": results, "summaries": summaries,
        "non_book_rows": non_book, "non_book_reason_counts": dict(sorted(reason_counts.items())),
    }
