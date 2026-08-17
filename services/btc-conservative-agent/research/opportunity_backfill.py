"""Read-only backfill of canonical trade-idea research records.

Never modifies raw evidence files. Never changes live strategy parameters.
Missing evidence stays UNKNOWN. Unfilled ideas are not zero-PnL trades.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research.shadow_outcome_reconstruction import (
    EXECUTABLE_AFTER_EXPIRY,
    EXECUTABLE_AT_ORIGINAL_LIMIT,
    EXECUTABLE_BUT_BLOCKED,
    EXECUTABLE_COUNTERFACTUAL,
    EXECUTABLE_ONLY_AFTER_CHASE,
    ESTIMATED_FILL_PROBABILITY,
    NEVER_EXECUTABLE,
    PARTIALLY_EXECUTABLE,
    UNKNOWN,
    attach_market_evidence,
    reconstruct_row,
    wilson,
)
from research.source_market_evidence import load_market_evidence_index

EVIDENCE_NAMES = (
    "counterfactual.jsonl",
    "signal_snapshot.jsonl",
    "execution_funnel.jsonl",
    "source_order_market_evidence.jsonl",
    "signal_replay.jsonl",
    "post_exit_replay.jsonl",
    "duplicate_intent_audit.jsonl",
    "trades_3factor.csv",
    "relay_lifecycle_evidence_v1.json",
)


def _jsonl_paths(root: Path, name: str, include_quarantine=None):
    paths = []
    active = root / name
    if active.is_file():
        paths.append(active)
    try:
        for candidate in root.glob(name + ".*"):
            suffix = candidate.name[len(name) + 1:]
            if candidate.is_file() and suffix.isdigit():
                paths.append(candidate)
    except OSError:
        pass
    for extra in include_quarantine or []:
        if extra.is_file() and extra.name.startswith(name):
            paths.append(extra)
        elif extra.is_dir():
            nested = extra / name
            if nested.is_file():
                paths.append(nested)
            try:
                paths.extend(p for p in extra.glob(name + ".*") if p.is_file())
            except OSError:
                pass
    seen = set()
    unique = []
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _iter_jsonl(paths):
    for path in paths:
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        yield path, json.loads(line)
                    except (TypeError, ValueError):
                        continue
        except OSError:
            continue


def _quarantine_roots(quarantine: Path | None):
    if quarantine is None or not quarantine.exists():
        return []
    try:
        return [child for child in quarantine.iterdir() if child.is_dir()]
    except OSError:
        return []


def inventory_evidence(mirror: Path, quarantine: Path | None = None):
    roots = [mirror] + _quarantine_roots(quarantine)
    files = []
    for root in roots:
        kind = "mirror" if root == mirror else "quarantine"
        for name in EVIDENCE_NAMES:
            for path in _jsonl_paths(root, name):
                try:
                    size = path.stat().st_size
                except OSError:
                    size = None
                files.append({
                    "path": str(path),
                    "name": path.name,
                    "kind": kind,
                    "size_bytes": size,
                    "exists": True,
                    "modified": True,
                })
        if (root / "relay_lifecycle_evidence_v1.json").is_file() and not any(
            item["name"] == "relay_lifecycle_evidence_v1.json" and item["kind"] == kind for item in files
        ):
            path = root / "relay_lifecycle_evidence_v1.json"
            files.append({
                "path": str(path),
                "name": path.name,
                "kind": kind,
                "size_bytes": path.stat().st_size,
                "exists": True,
            })
    return {
        "schema": "data_recovery_inventory_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mirror": str(mirror),
        "quarantine": str(quarantine) if quarantine else None,
        "files": files,
        "note": "Read-only inventory. Raw files were not modified.",
    }


def _latest_by_id(paths, id_keys=("trade_id", "canonical_trade_id")):
    latest = {}
    for _path, row in _iter_jsonl(paths):
        if not isinstance(row, dict):
            continue
        trade_id = next((str(row.get(key) or "") for key in id_keys if row.get(key)), "")
        if trade_id:
            latest[trade_id] = row
            latest[trade_id]["trade_id"] = trade_id
    return latest


def _funnel_stages(paths):
    stages = defaultdict(set)
    for _path, row in _iter_jsonl(paths):
        trade_id = str((row or {}).get("trade_id") or "")
        stage = str((row or {}).get("stage") or "")
        if trade_id and stage:
            stages[trade_id].add(stage)
    return stages


def _duplicate_latest(paths):
    latest = {}
    for _path, row in _iter_jsonl(paths):
        trade_id = str((row or {}).get("trade_id") or "")
        if trade_id:
            latest[trade_id] = row
    return latest


def _paper_trades(path: Path):
    if not path.is_file():
        return {}
    rows = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            trade_id = str(row.get("trade_id") or "")
            if trade_id:
                rows[trade_id] = row
    return rows


def _relay_ids(path: Path):
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return set()
    ids = set()
    for record in payload.get("records") or []:
        trade_id = str((record or {}).get("canonicalTradeId") or (record or {}).get("canonical_trade_id") or "")
        if trade_id:
            ids.add(trade_id)
    return ids


def build_records(mirror: Path, quarantine: Path | None = None):
    extra = _quarantine_roots(quarantine)
    snapshots = _latest_by_id(_jsonl_paths(mirror, "signal_snapshot.jsonl", extra))
    counterfactual = _latest_by_id(_jsonl_paths(mirror, "counterfactual.jsonl", extra))
    funnel = _funnel_stages(_jsonl_paths(mirror, "execution_funnel.jsonl", extra))
    duplicates = _duplicate_latest(_jsonl_paths(mirror, "duplicate_intent_audit.jsonl", extra))
    paper = _paper_trades(mirror / "trades_3factor.csv")
    relay_ids = _relay_ids(mirror / "relay_lifecycle_evidence_v1.json")
    all_ids = set(snapshots) | set(counterfactual) | set(funnel) | set(duplicates) | set(paper) | relay_ids
    evidence_index = load_market_evidence_index(
        mirror / "source_order_market_evidence.jsonl",
        target_trade_ids=all_ids,
    )
    for root in extra:
        extra_index = load_market_evidence_index(
            root / "source_order_market_evidence.jsonl",
            target_trade_ids=all_ids,
        )
        for trade_id, grouped in extra_index.items():
            current = evidence_index.setdefault(trade_id, grouped)
            if current is grouped:
                continue
            current_obs = list(current.get("observations") or [])
            extra_obs = list(grouped.get("observations") or [])
            current["observations"] = current_obs + extra_obs
    all_ids |= set(evidence_index)
    records = []
    for trade_id in sorted(all_ids):
        row = dict(counterfactual.get(trade_id) or snapshots.get(trade_id) or {"trade_id": trade_id, "executed": False})
        row["trade_id"] = trade_id
        attached = attach_market_evidence(row, evidence_index)
        record = reconstruct_row(
            attached,
            funnel_stages=funnel.get(trade_id),
            paper_trade=paper.get(trade_id),
            duplicate_audit=duplicates.get(trade_id),
        )
        record["funnel_stages"] = sorted(funnel.get(trade_id) or [])
        record["has_market_evidence"] = trade_id in evidence_index
        record["has_counterfactual"] = trade_id in counterfactual
        record["has_snapshot"] = trade_id in snapshots
        record["has_relay"] = trade_id in relay_ids
        record["paper_closed"] = trade_id in paper
        records.append(record)
    return records


def _prob(k, n):
    estimate = wilson(k, n)
    if n is None or int(n or 0) <= 0:
        estimate["status"] = UNKNOWN
    return estimate


def five_question_report(records):
    with_market = [row for row in records if row.get("has_market_evidence")]
    origins = [row.get("fill_origin") or {} for row in with_market]
    n = len(with_market)
    certain = [row for row in with_market if (row.get("fill_origin") or {}).get("label") == EXECUTABLE_COUNTERFACTUAL]
    estimated = [row for row in with_market if (row.get("fill_origin") or {}).get("label") == ESTIMATED_FILL_PROBABILITY]
    never = [row for row in with_market if (row.get("fill_origin") or {}).get("classification") == NEVER_EXECUTABLE]
    original = [row for row in certain if (row.get("fill_origin") or {}).get("classification") == EXECUTABLE_AT_ORIGINAL_LIMIT]
    chased = [row for row in certain if (row.get("fill_origin") or {}).get("classification") == EXECUTABLE_ONLY_AFTER_CHASE]
    after_expiry = [row for row in certain if (row.get("fill_origin") or {}).get("classification") == EXECUTABLE_AFTER_EXPIRY]
    blocked_certain = [row for row in certain if (row.get("fill_origin") or {}).get("classification") == EXECUTABLE_BUT_BLOCKED]
    blocked = [row for row in records if "CLUSTER" in str((row.get("fill_origin") or {}).get("classification") or "")
               or (row.get("copy_disposition") or {}).get("disposition") == "DUPLICATE_CLUSTER_BLOCKED"
               or (row.get("fill_origin") or {}).get("cluster_blocked")]
    paper_closed = [row for row in records if row.get("paper_closed")]
    hyp_pnl = [row.get("net_pnl_usd") for row in certain if row.get("net_pnl_usd") is not None]
    by_direction = {}
    for direction in ("LONG", "SHORT"):
        subset = [row for row in with_market if (row.get("fill_origin") or {}).get("direction") == direction]
        by_direction[direction] = {
            "n": len(subset),
            "certain_executable": _prob(sum(1 for row in subset if (row.get("fill_origin") or {}).get("label") == EXECUTABLE_COUNTERFACTUAL), len(subset)),
            "never_executable": _prob(sum(1 for row in subset if (row.get("fill_origin") or {}).get("classification") == NEVER_EXECUTABLE), len(subset)),
            "estimated_only": _prob(sum(1 for row in subset if (row.get("fill_origin") or {}).get("label") == ESTIMATED_FILL_PROBABILITY), len(subset)),
        }
    return {
        "schema": "five_question_preliminary_report_v1",
        "classification": "PRELIMINARY",
        "live_modification_authorized": False,
        "independent_chronological_holdout": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Empirical estimates from currently recoverable evidence. "
            "Insufficient evidence remains UNKNOWN. "
            "These numbers do not authorize live parameter changes."
        ),
        "sample": {
            "total_ideas": len(records),
            "with_market_evidence": n,
            "without_market_evidence": len(records) - n,
        },
        "by_direction": by_direction,
        "questions": {
            "duplicate_cluster_0_09pct": {
                "question": "Is 0.09% too wide, too narrow, or reasonable?",
                "blocked_or_cluster_rows": len(blocked),
                "blocked_certain_executable": len(blocked_certain),
                "winner_skipped": sum(1 for row in blocked if (row.get("cluster_portfolio") or {}).get("winner_skipped")),
                "loss_avoided": sum(1 for row in blocked if (row.get("cluster_portfolio") or {}).get("loss_avoided")),
                "answer": "UNKNOWN_TOO_SMALL" if len(blocked) < 30 else "PRELIMINARY_DESCRIPTIVE",
                "live_recommendation": "not qualified",
            },
            "thesis_fast_cut": {
                "question": "Did thesis-fast-cut improve or worsen outcomes versus holding?",
                "closed_paper_n": len(paper_closed),
                "answer": "PRELIMINARY_DESCRIPTIVE_ONLY",
                "live_recommendation": "not qualified",
            },
            "hard_stop_13pct": {
                "question": "Did the current 13% hard stop bind, and what would alternatives have done?",
                "certain_replay_n": sum(1 for row in certain if (row.get("exit_replay") or {}).get("hard_stop_hit")),
                "answer": "UNKNOWN_OR_PRELIMINARY",
                "live_recommendation": "not qualified",
            },
            "scenario_c": {
                "question": "Did current Scenario C rungs improve outcomes versus alternatives?",
                "certain_replay_n": sum(1 for row in certain if (row.get("exit_replay") or {}).get("scenario_c_hit")),
                "answer": "UNKNOWN_OR_PRELIMINARY",
                "live_recommendation": "not qualified",
            },
            "chase_timing_and_limits": {
                "question": "Did chasing raise fill probability but worsen expected P&L?",
                "original_limit_executable": _prob(len(original), n),
                "executable_only_after_chase": _prob(len(chased), n),
                "any_certain_executable": _prob(len(certain), n),
                "estimated_book_executable_only": _prob(len(estimated), n),
                "never_executable": _prob(len(never), n),
                "hypothetical_net_pnl_usd_sum": None if not hyp_pnl else round(sum(hyp_pnl), 4),
                "hypothetical_net_pnl_n": len(hyp_pnl),
                "answer": "PRELIMINARY_EMPIRICAL",
                "live_recommendation": "not qualified",
            },
        },
        "fill_probabilities": {
            "any_certain_executable_counterfactual": _prob(len(certain), n),
            "full_executable": _prob(sum(1 for row in certain if (row.get("fill_origin") or {}).get("full")), n),
            "partial_executable": _prob(sum(1 for row in certain if (row.get("fill_origin") or {}).get("partial")), n),
            "estimated_fill_probability_only": _prob(len(estimated), n),
            "never_executable": _prob(len(never), n),
            "executable_at_original_limit": _prob(len(original), n),
            "executable_only_after_chase": _prob(len(chased), n),
            "executable_after_expiry": _prob(len(after_expiry), n),
            "executable_but_blocked": _prob(len(blocked_certain), n),
            "note": "These are Wilson 95% intervals on completed market-evidence samples, not guaranteed future probabilities.",
        },
        "min_additional_sample_for_holdout": max(0, 30 - n),
        "train_holdout": {
            "status": "NOT_SPLIT",
            "reason": "Independent chronological holdout is required before any live parameter change. This backfill is descriptive of recovered evidence only.",
        },
    }


def population_fidelity_report(records):
    source = Counter((row.get("source_disposition") or {}).get("disposition") or UNKNOWN for row in records)
    copy = Counter((row.get("copy_disposition") or {}).get("disposition") or UNKNOWN for row in records)
    origin = Counter((row.get("fill_origin") or {}).get("classification") or UNKNOWN for row in records)
    accounting = {
        "total_ideas": len(records),
        "source_partition": dict(source),
        "source_partition_sum": sum(source.values()),
        "rejected_before_approval": source.get("NEVER_APPROVED", 0),
        "approved_but_not_submitted": source.get("APPROVED_BUT_NEVER_SUBMITTED", 0),
        "paused_research_only": copy.get("PAUSED_OR_RESEARCH_ONLY", 0),
        "transport_rejected": copy.get("TRANSPORT_SCHEMA_REJECTED", 0),
        "cluster_blocked": copy.get("DUPLICATE_CLUSTER_BLOCKED", 0),
        "never_executable": origin.get(NEVER_EXECUTABLE, 0),
        "executable_counterfactual": sum(
            1 for row in records
            if (row.get("fill_origin") or {}).get("label") == EXECUTABLE_COUNTERFACTUAL
        ),
        "real_unfilled": source.get("EXPIRED", 0) + source.get("LIMIT_SUBMITTED", 0),
        "real_partial_fills": source.get("PARTIALLY_FILLED", 0),
        "real_full_fills": source.get("FULLY_FILLED", 0),
        "unknown_missing_evidence": source.get(UNKNOWN, 0),
        "closed_paper": source.get("CLOSED", 0),
        "note": (
            "Source dispositions are the mutually exclusive idea census. "
            "Copy/origin counts overlap that census and must not be added together as if they were extra trades."
        ),
    }
    return {
        "schema": "population_fidelity_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": "PRELIMINARY",
        "live_modification_authorized": False,
        "source_dispositions": dict(source),
        "copy_dispositions": dict(copy),
        "fill_origin_classes": dict(origin),
        "accounting_identity": accounting,
        "showcase_only": sum(1 for row in records if row.get("paper_closed") and (row.get("copy_disposition") or {}).get("disposition") == "NEVER_RECEIVED"),
        "bitfinex_only": sum(1 for row in records if (row.get("copy_disposition") or {}).get("disposition") in {"PARTIALLY_FILLED", "FULLY_FILLED"} and (row.get("source_disposition") or {}).get("disposition") != "FULLY_FILLED"),
        "both": sum(1 for row in records if row.get("paper_closed") and (row.get("copy_disposition") or {}).get("disposition") in {"PARTIALLY_FILLED", "FULLY_FILLED"}),
        "never_executable": origin.get(NEVER_EXECUTABLE, 0),
        "later_executable": origin.get(EXECUTABLE_AFTER_EXPIRY, 0),
        "paused_research_only": copy.get("PAUSED_OR_RESEARCH_ONLY", 0),
        "transport_rejected": copy.get("TRANSPORT_SCHEMA_REJECTED", 0),
        "cluster_blocked": copy.get("DUPLICATE_CLUSTER_BLOCKED", 0),
        "note": "No row is classified as zero profit because it did not trade. Unfilled ideas keep net_pnl_usd=null.",
    }


def shadow_summary(records):
    with_market = [row for row in records if row.get("has_market_evidence")]
    certain = [row for row in with_market if (row.get("fill_origin") or {}).get("label") == EXECUTABLE_COUNTERFACTUAL]
    hyp = [row.get("net_pnl_usd") for row in certain if row.get("net_pnl_usd") is not None]
    return {
        "schema": "shadow_opportunity_summary_v1",
        "opportunities_with_market_evidence": len(with_market),
        "never_executable": sum(1 for row in with_market if (row.get("fill_origin") or {}).get("classification") == NEVER_EXECUTABLE),
        "executable_blocked": sum(1 for row in with_market if (row.get("fill_origin") or {}).get("classification") == EXECUTABLE_BUT_BLOCKED),
        "later_executable": sum(1 for row in with_market if (row.get("fill_origin") or {}).get("classification") == EXECUTABLE_AFTER_EXPIRY),
        "hypothetical_fills": len(certain),
        "hypothetical_winners": sum(1 for value in hyp if value > 0),
        "hypothetical_losers": sum(1 for value in hyp if value < 0),
        "total_hyp_net_pnl_usd": None if not hyp else round(sum(hyp), 4),
        "avg_hyp_net_pnl_usd": None if not hyp else round(sum(hyp) / len(hyp), 4),
        "losses_avoided": sum(1 for row in records if (row.get("cluster_portfolio") or {}).get("loss_avoided")),
        "winners_skipped": sum(1 for row in records if (row.get("cluster_portfolio") or {}).get("winner_skipped")),
        "replay_complete_count": sum(1 for row in records if row.get("replay_complete") is True),
        "note": "Hypothetical PnL is absent unless ticks were supplied. This backfill classifies fill origins without fabricating zeros.",
    }


def write_reports(mirror: Path, out_dir: Path, quarantine: Path | None = None):
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory = inventory_evidence(mirror, quarantine)
    records = build_records(mirror, quarantine)
    five = five_question_report(records)
    population = population_fidelity_report(records)
    summary = shadow_summary(records)
    recovery = {
        **inventory,
        "unique_ideas": len(records),
        "ideas_with_market_evidence": sum(1 for row in records if row.get("has_market_evidence")),
        "ideas_without_market_evidence": sum(1 for row in records if not row.get("has_market_evidence")),
        "recovered_fill_origins": sum(1 for row in records if (row.get("fill_origin") or {}).get("classification") not in {None, UNKNOWN}),
        "irrecoverable_without_market_evidence": [
            row["trade_id"] for row in records if not row.get("has_market_evidence")
        ][:200],
        "missing_fields": [
            "Queue position is unknown for never-submitted orders.",
            "Depth at synthetic bps-offset prices is UNKNOWN.",
            "Exit-replay PnL requires tick paths and is omitted unless loaded.",
        ],
        "raw_files_modified": False,
    }
    payloads = {
        "DATA_RECOVERY_REPORT.json": recovery,
        "FIVE_QUESTION_PRELIMINARY_REPORT.json": five,
        "POPULATION_FIDELITY_REPORT.json": population,
        "SHADOW_OPPORTUNITY_SUMMARY.json": summary,
    }
    for name, payload in payloads.items():
        (out_dir / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    compact = []
    for row in records:
        compact.append({
            "trade_id": row.get("trade_id"),
            "source_disposition": (row.get("source_disposition") or {}).get("disposition"),
            "copy_disposition": (row.get("copy_disposition") or {}).get("disposition"),
            "classification": (row.get("fill_origin") or {}).get("classification"),
            "label": (row.get("fill_origin") or {}).get("label"),
            "not_a_trade": row.get("not_a_trade"),
            "net_pnl_usd": row.get("net_pnl_usd"),
            "replay_complete": row.get("replay_complete"),
            "evidence_quality": row.get("evidence_quality"),
            "has_market_evidence": row.get("has_market_evidence"),
        })
    (out_dir / "complete_research_records_compact.json").write_text(json.dumps(compact, indent=2), encoding="utf-8")
    return recovery, five, population, summary, records
