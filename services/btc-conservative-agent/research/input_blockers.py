"""Analyzer input-fidelity blockers: which inputs carry no usable data, and why.

Each item says whether an analyzer input is usable (OK), usable with a
measured gap (DEGRADED), or unusable (BLOCKED), with a stable reason code, a
human reason, eligible/total counts and the evidence behind the verdict.
``side`` names who must act: ANALYZER (fixable in this repo's analyzer) or
COLLECTION (needs a collector/runtime change on Fly).  Nothing here relaxes a
cohort gate: retired-tile rows stay quarantined as NON_REGISTRY_LANE.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from analyzer_epoch_guard import epoch_csv_rows, epoch_lines, guarded_open

SCHEMA = "analyzer_input_blockers_v1"
REPORT_FILE = "analyzer_input_blockers.json"

EXIT_LADDER_REPORT = "exit_ladder_simulator_report.json"
CROSS_WORLD_REPORT = "cross_world_evidence_report.json"
DATA_HEALTH_REPORT = "data_health_report.json"
PROTECTION_REPLAY_WINDOW_FILE = "protection_replay_window.json"
TRADES_CSV = "trades_3factor.csv"
COUNTERFACTUAL_FILE = "counterfactual.jsonl"
OPPORTUNITY_LEDGER = Path("v3") / "ledgers" / "opportunity.jsonl"
MIGRATION_RECEIPT = Path("migration") / "migration_receipt.json"
COUNTERFACTUAL_JOIN_KEYS = ("signal_ts", "shared_ai_call_id", "epoch_id", "opportunity_id")


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in epoch_lines(path, "r", encoding="utf-8-sig"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        pass
    return rows


def _read_trades(data_dir: Path) -> list[dict[str, Any]]:
    try:
        with guarded_open(data_dir / TRADES_CSV, "r", encoding="utf-8-sig", newline="") as handle:
            return epoch_csv_rows(csv.DictReader(handle), TRADES_CSV)
    except OSError:
        return []


def _item(input_name, status, reason_code, reason, *, eligible=None, total=None, side=None, evidence=None):
    return {
        "input": input_name,
        "status": status,
        "reason_code": reason_code,
        "reason": reason,
        "eligible": eligible,
        "total": total,
        "side": side,
        "evidence": evidence or {},
    }


def current_epoch(data_dir: Path) -> tuple[str | None, str]:
    from research.research_trade_accumulator import current_collection_epoch

    session = _load_json(data_dir / "research_session.json") or {}
    epoch_id, _, source = current_collection_epoch(session)
    if epoch_id:
        return epoch_id, source
    manifest = _load_json(data_dir / "canonical_dataset_current.json") or {}
    if manifest.get("dataset_epoch"):
        return str(manifest["dataset_epoch"]), "CANONICAL_DATASET_MANIFEST"
    return None, "UNKNOWN"


# --------------------------------------------------------------------------- exit ladder
def exit_ladder_item(report_dir: Path, data_dir: Path, epoch_id: str | None, *, replay_index=None) -> dict:
    from research.exit_ladder_paper_cohort import (
        SCHEMA as PAPER_SCHEMA,
        build_paper_exit_ladder_cohort,
        load_replay_index,
    )

    report = _load_json(report_dir / EXIT_LADDER_REPORT) or {}
    cohort = report.get("cohort_receipt") if isinstance(report.get("cohort_receipt"), Mapping) else None
    if cohort is None or cohort.get("schema") != PAPER_SCHEMA:
        index = replay_index if replay_index is not None else load_replay_index(data_dir)
        cohort = build_paper_exit_ladder_cohort(_read_trades(data_dir), index, epoch_id=epoch_id)
        report_uses_paper_cohort = False
    else:
        report_uses_paper_cohort = True
    registry_trades = int(cohort.get("current_registry_trades") or 0)
    eligible = int(cohort.get("eligible_count") or 0)
    matched = int(report.get("replays_matched_executed") or 0)
    evidence = {
        "report_data_status": report.get("data_status"),
        "report_raw_replays_available": report.get("raw_replays_available"),
        "report_eligible_replays": report.get("eligible_replays_available"),
        "report_replays_matched_executed": matched,
        "report_uses_paper_cohort": report_uses_paper_cohort,
        "paper_cohort": {k: v for k, v in cohort.items() if k != "eligible_trade_ids"},
        "relay_state_assumed": "DISARMED_PAPER_ONLY",
    }
    if not report_uses_paper_cohort:
        if eligible:
            return _item(
                "exit_ladder_simulator", "BLOCKED", "REAL_COPY_COHORT_GATE_WHILE_RELAY_DISARMED",
                "The simulator is gated on the Bitfinex real-copy cohort, which no paper trade can "
                f"satisfy with the relay disarmed. {eligible} current-registry paper trade(s) are "
                "eligible under the paper cohort; the analyzer hook switching the simulator to it "
                "is pending.",
                eligible=0, total=registry_trades, side="ANALYZER", evidence=evidence,
            )
        return _item(
            "exit_ladder_simulator", "BLOCKED", "NO_COMPLETE_REPLAY_FOR_CURRENT_REGISTRY_TRADES",
            "No current-registry executed paper trade has a complete replay path yet.",
            eligible=0, total=registry_trades, side="COLLECTION", evidence=evidence,
        )
    if not eligible or not matched:
        return _item(
            "exit_ladder_simulator", "BLOCKED", "NO_COMPLETE_REPLAY_FOR_CURRENT_REGISTRY_TRADES",
            "No current-registry executed paper trade has a complete replay path yet.",
            eligible=eligible, total=registry_trades, side="COLLECTION", evidence=evidence,
        )
    if eligible < registry_trades:
        return _item(
            "exit_ladder_simulator", "DEGRADED", "PARTIAL_REPLAY_COVERAGE",
            f"{eligible}/{registry_trades} current-registry paper trades have a complete replay; "
            "the rest are missing or still inside their post-exit horizon.",
            eligible=eligible, total=registry_trades, side="COLLECTION", evidence=evidence,
        )
    return _item("exit_ladder_simulator", "OK", None, "All current-registry paper trades replayable.",
                 eligible=eligible, total=registry_trades, evidence=evidence)


# --------------------------------------------------------------------------- cross world
_WORLD_CAUSES = {
    "IDEAL_TOUCH_DIAGNOSTIC": "rows are aggregate policy rows without per-episode epoch/causal identity",
    "CONSERVATIVE_BBO_DEPTH": (
        "conservative receipts carry no tape_id (the V3 order_intent rows have none) and a fill_id "
        "only for conservative fills"
    ),
    "SHADOW_COUNTERFACTUAL": "shadow/counterfactual rows carry no epoch_id/policy_signature/schedule/tape/fill identity",
    "OBSERVED_PAPER": "paper rows missing an explicit identity",
    "BITFINEX_COPY": "relay is disarmed, so no authenticated Bitfinex copy rows exist",
}


def cross_world_item(report_dir: Path) -> dict:
    report = _load_json(report_dir / CROSS_WORLD_REPORT)
    if report is None:
        return _item("cross_world_evidence", "BLOCKED", "REPORT_MISSING",
                     "cross_world_evidence_report.json was not produced.", side="ANALYZER")
    summary = report.get("join_summary") or {}
    worlds = report.get("worlds") or {}
    computable_worlds = sorted(name for name, row in worlds.items() if (row or {}).get("status") == "COMPUTABLE")
    comparisons = int(summary.get("pairwise_computable_comparisons") or 0)
    evidence = {
        "join_status": summary.get("status"),
        "pairwise_computable_comparisons": comparisons,
        "computable_worlds": computable_worlds,
        "worlds": {
            name: {
                "status": (row or {}).get("status"),
                "rows_observed": (row or {}).get("rows_observed"),
                "source_rows_total": (row or {}).get("source_rows_total"),
                "rows_excluded_missing_epoch": (row or {}).get("rows_excluded_missing_epoch"),
                "unique_joinable_rows": (row or {}).get("unique_joinable_rows"),
                "missing_identity_counts": (row or {}).get("missing_identity_counts"),
            }
            for name, row in worlds.items()
        },
        "required_identities": (report.get("join_contract") or {}).get("required_explicit_identities"),
    }
    if comparisons:
        return _item("cross_world_evidence", "OK", None, "Cross-world comparisons computable.",
                     eligible=comparisons, total=comparisons, evidence=evidence)
    causes = [
        f"{name}: {_WORLD_CAUSES.get(name, 'identity incomplete')}"
        for name, row in worlds.items()
        if (row or {}).get("status") != "COMPUTABLE"
    ]
    return _item(
        "cross_world_evidence", "BLOCKED",
        "FEWER_THAN_TWO_COMPUTABLE_WORLDS" if len(computable_worlds) < 2 else "NO_SHARED_EXPLICIT_IDENTITY",
        f"Only {len(computable_worlds)} world(s) ({', '.join(computable_worlds) or 'none'}) have complete "
        "explicit causal identities; the fail-closed join forbids fuzzy matching. "
        + "; ".join(causes) + ".",
        eligible=0, total=len(worlds), side="COLLECTION", evidence=evidence,
    )


# --------------------------------------------------------------------------- accumulator
def trade_accumulator_item(data_dir: Path, epoch_id: str | None, *, status_path: Path | None = None) -> dict:
    from research import research_trade_accumulator as acc
    from research.exit_ladder_paper_cohort import active_registry_lanes

    path = status_path or acc._status_path()
    status = _load_json(path)
    lanes = set(active_registry_lanes().values())
    expected = sum(
        1 for row in {r.get("trade_id"): r for r in _read_trades(data_dir)}.values()
        if str(row.get("research_lane") or "").upper() in lanes
        and (row.get("epoch_id") or row.get("research_collection_id")) == epoch_id
    )
    evidence = {
        "status_path": str(path),
        "accumulator_epoch_id": (status or {}).get("epoch_id"),
        "accumulator_epoch_start": (status or {}).get("epoch_start_iso"),
        "accumulator_total_trades": (status or {}).get("total_trades"),
        "current_epoch_id": epoch_id,
        "expected_current_registry_trades_in_csv": expected,
    }
    if status is None:
        return _item("trade_accumulator", "BLOCKED", "STATUS_MISSING",
                     "Accumulator status file not found; the accumulator has not run with this code.",
                     eligible=0, total=expected, side="ANALYZER", evidence=evidence)
    total = int(status.get("total_trades") or 0)
    if status.get("epoch_id") != epoch_id:
        return _item(
            "trade_accumulator", "BLOCKED", "ACCUMULATOR_EPOCH_STALE",
            f"Accumulator pinned to {status.get('epoch_start_iso')} (epoch {status.get('epoch_id')}), "
            f"not the current epoch {epoch_id}; it re-anchors on its next sync.",
            eligible=total, total=expected, side="ANALYZER", evidence=evidence,
        )
    if total < expected:
        return _item("trade_accumulator", "DEGRADED", "ACCUMULATOR_BEHIND_CSV",
                     f"{total}/{expected} current-epoch registry trades accumulated.",
                     eligible=total, total=expected, side="ANALYZER", evidence=evidence)
    return _item("trade_accumulator", "OK", None, "Accumulator follows the current epoch.",
                 eligible=total, total=expected, evidence=evidence)


# --------------------------------------------------------------------------- signal replay
def signal_replay_item(report_dir: Path, *, cohort: Mapping[str, Any] | None = None) -> dict:
    health = _load_json(report_dir / DATA_HEALTH_REPORT) or {}
    replay = health.get("signal_replay") if isinstance(health.get("signal_replay"), Mapping) else None
    if replay is None:
        return _item("signal_replay", "BLOCKED", "DATA_HEALTH_SIGNAL_REPLAY_MISSING",
                     "data_health_report.json has no signal_replay section.", side="ANALYZER")
    distinct = int(replay.get("distinct_trades") or 0)
    complete = int(replay.get("distinct_complete") or 0)
    reasons = dict(replay.get("row_reasons") or {})
    evidence = {
        "distinct_trades": distinct,
        "distinct_complete": complete,
        "distinct_complete_pct": replay.get("distinct_complete_pct"),
        "distinct_censored_shutdown": replay.get("distinct_censored_shutdown"),
        "row_reasons": reasons,
        "by_lane": replay.get("by_lane"),
    }
    if cohort is not None:
        evidence["current_registry_executed"] = {
            "trades": cohort.get("current_registry_trades"),
            "with_replay": cohort.get("current_registry_trades_with_replay"),
            "complete_and_eligible": cohort.get("eligible_count"),
        }
    if not distinct:
        return _item("signal_replay", "BLOCKED", "NO_SIGNAL_REPLAY_ROWS", "No signal replay rows.",
                     eligible=0, total=0, side="COLLECTION", evidence=evidence)
    ratio = complete / distinct
    if ratio >= 0.9:
        return _item("signal_replay", "OK", None, "Replay completeness healthy.",
                     eligible=complete, total=distinct, evidence=evidence)
    return _item(
        "signal_replay", "DEGRADED", "REPLAY_CENSORED_BY_PROCESS_RESTARTS",
        f"Only {complete}/{distinct} distinct replays ({100 * ratio:.1f}%) are complete; "
        f"{reasons.get('CENSORED_PROCESS_SHUTDOWN', 0)} rows were censored by process shutdown and "
        "buffers are re-dumped incomplete across Fly restarts. Collection-side; the analyzer "
        "already uses only explicitly complete paths.",
        eligible=complete, total=distinct, side="COLLECTION", evidence=evidence,
    )


# --------------------------------------------------------------------------- counterfactual
def resolve_counterfactual_join_keys(
    counterfactual_rows: Iterable[Mapping[str, Any]],
    opportunity_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recover join keys for ``scan-*`` counterfactual rows by exact AI-call id.

    A counterfactual row's ``trade_id`` is the shared AI call id that the V3
    opportunity ledger records verbatim.  Keys are copied only when that id
    names exactly one epoch/opportunity; an ambiguous id stays unresolved.
    Existing non-empty keys are never overwritten.
    """
    by_call: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in opportunity_rows:
        call_id = str(row.get("shared_ai_call_id") or "").strip()
        if call_id:
            by_call[call_id].append(row)
    resolved_rows: list[dict[str, Any]] = []
    outcome: Counter[str] = Counter()
    for row in counterfactual_rows:
        item = dict(row)
        call_id = str(item.get("shared_ai_call_id") or item.get("trade_id") or "").strip()
        candidates = by_call.get(call_id) or []
        identities = {
            (str(c.get("epoch_id") or ""), str(c.get("opportunity_id") or c.get("record_id") or ""))
            for c in candidates
        }
        if not call_id.startswith("scan-") or not candidates:
            outcome["UNRESOLVED_NO_MATCHING_AI_CALL"] += 1
        elif len(identities) != 1:
            outcome["UNRESOLVED_AMBIGUOUS_AI_CALL"] += 1
        else:
            source = min(candidates, key=lambda c: str(c.get("record_id") or ""))
            epoch, opportunity = next(iter(identities))
            recovered = {
                "shared_ai_call_id": call_id,
                "epoch_id": epoch or None,
                "opportunity_id": opportunity or None,
                "signal_ts": source.get("signal_ts"),
            }
            for key, value in recovered.items():
                if item.get(key) in (None, "") and value not in (None, ""):
                    item[key] = value
            item["join_key_provenance"] = "OPPORTUNITY_LEDGER_EXACT_SHARED_AI_CALL_ID"
            outcome["RESOLVED_EXACT_AI_CALL_ID"] += 1
        resolved_rows.append(item)
    total = len(resolved_rows)
    coverage = {
        key: sum(1 for row in resolved_rows if row.get(key) not in (None, "")) for key in COUNTERFACTUAL_JOIN_KEYS
    }
    return resolved_rows, {
        "rows": total,
        "resolution": dict(sorted(outcome.items())),
        "join_key_coverage": coverage,
        "join_key_coverage_pct": {
            key: round(100.0 * value / total, 2) if total else 0.0 for key, value in coverage.items()
        },
    }


def counterfactual_item(report_dir: Path, data_dir: Path, epoch_id: str | None) -> dict:
    health = _load_json(report_dir / DATA_HEALTH_REPORT) or {}
    stream = ((health.get("repaired_streams") or {}).get("counterfactual")) or {}
    raw = _read_jsonl(data_dir / COUNTERFACTUAL_FILE)
    native = {
        key: sum(1 for row in raw if row.get(key) not in (None, "")) for key in COUNTERFACTUAL_JOIN_KEYS
    }
    _, receipt = resolve_counterfactual_join_keys(raw, _read_jsonl(data_dir / OPPORTUNITY_LEDGER))
    resolved = receipt["resolution"].get("RESOLVED_EXACT_AI_CALL_ID", 0)
    evidence = {
        "data_health_stream": stream,
        "rows": len(raw),
        "native_key_counts": native,
        "native_ts_present": sum(1 for row in raw if row.get("ts") not in (None, "")),
        "analyzer_resolution": receipt,
        "current_epoch_id": epoch_id,
    }
    total = len(raw)
    if not total:
        return _item("counterfactual_stream", "BLOCKED", "NO_COUNTERFACTUAL_ROWS", "No counterfactual rows.",
                     eligible=0, total=0, side="COLLECTION", evidence=evidence)
    if all(native[key] == total for key in COUNTERFACTUAL_JOIN_KEYS):
        return _item("counterfactual_stream", "OK", None, "Counterfactual rows carry their join keys.",
                     eligible=total, total=total, evidence=evidence)
    if resolved:
        return _item(
            "counterfactual_stream", "DEGRADED", "JOIN_KEYS_RECOVERED_FROM_SHARED_AI_CALL_ID",
            f"Rows are written without epoch/opportunity/signal_ts/shared_ai_call_id, but {resolved}/{total} "
            "trade_ids are exact shared AI call ids that resolve to one opportunity in the V3 ledger. "
            "Analyzer-side recovery via resolve_counterfactual_join_keys; the collector should still "
            "write the keys natively (post-freeze Fly fix).",
            eligible=resolved, total=total, side="ANALYZER", evidence=evidence,
        )
    return _item(
        "counterfactual_stream", "BLOCKED", "COLLECTION_JOIN_KEYS_MISSING",
        "Counterfactual rows carry no join keys under any name and their ids do not resolve to an "
        "opportunity; collection-side, post-freeze Fly fix.",
        eligible=0, total=total, side="COLLECTION", evidence=evidence,
    )


# --------------------------------------------------------------------------- protection replay
def protection_replay_item(report_dir: Path) -> dict:
    summary = _load_json(report_dir / PROTECTION_REPLAY_WINDOW_FILE)
    if summary is None:
        from research_v3_candidates import protection_replay_window_summary

        report = _load_json(report_dir / "safe_policy_genome_v3_report.json") or {}
        summary = report.get("protection_replay_window")
        window = (report.get("candidate_screen") or {}).get("input_window")
        if not isinstance(summary, Mapping) and isinstance(window, Mapping):
            summary = protection_replay_window_summary(window)
    if not isinstance(summary, Mapping):
        return _item("protection_replay_window", "BLOCKED", "WINDOW_RECEIPT_MISSING",
                     "No protection replay window receipt was persisted.", side="ANALYZER",
                     evidence={"alert_level": "AMBER"})
    alert = summary.get("alert_level")
    status = {"GREEN": "OK"}.get(str(alert), "DEGRADED")
    return _item(
        "protection_replay_window", status, summary.get("reason"),
        (
            f"Protection replay covered {summary.get('events_selected')}/{summary.get('events_eligible')} "
            f"eligible events (max_events={summary.get('max_events')})."
        ),
        eligible=summary.get("events_selected"), total=summary.get("events_eligible"),
        side="ANALYZER" if status != "OK" else None, evidence=dict(summary),
    )


def segment_promotion_item(data_dir: Path) -> dict[str, Any] | None:
    """DEGRADED when this dataset was promoted with disclosed Fly-side warnings."""
    receipt = _load_json(data_dir / MIGRATION_RECEIPT) or {}
    warnings = [str(item) for item in receipt.get("promotion_warnings") or []]
    if not warnings:
        return None
    stale = [item.split(":", 1)[1] for item in warnings if item.startswith("FLY_OVERSIZED_SQLITE_SNAPSHOT:")]
    reason = (f"Promoted on the last shipped copy of {', '.join(stale)} (over the Fly SQLite snapshot cap)."
              if stale else "Promoted with disclosed Fly shipper warnings.")
    return _item("fly_segment_promotion", "DEGRADED", warnings[0].split(":", 1)[0], reason,
                 side="COLLECTION", evidence={"promotion_warnings": warnings,
                                              "promotion_level": receipt.get("promotion_level"),
                                              "migrated_at": receipt.get("completed_at")})


def collect_input_blockers(report_dir, data_dir, *, write: bool = False) -> dict[str, Any]:
    """Return the analyzer_input_blockers_v1 document for one analyzer cycle."""
    from research.exit_ladder_paper_cohort import load_replay_index

    report_dir, data_dir = Path(report_dir), Path(data_dir)
    epoch_id, epoch_source = current_epoch(data_dir)
    exit_ladder = exit_ladder_item(
        report_dir, data_dir, epoch_id,
        replay_index=None if (_load_json(report_dir / EXIT_LADDER_REPORT) or {}).get("cohort_receipt")
        else load_replay_index(data_dir),
    )
    items = [
        protection_replay_item(report_dir),
        exit_ladder,
        cross_world_item(report_dir),
        trade_accumulator_item(data_dir, epoch_id),
        signal_replay_item(report_dir, cohort=exit_ladder["evidence"].get("paper_cohort")),
        counterfactual_item(report_dir, data_dir, epoch_id),
    ]
    promotion = segment_promotion_item(data_dir)
    if promotion is not None:
        items.append(promotion)
    statuses = {item["status"] for item in items}
    replay_red = any(
        item["input"] == "protection_replay_window" and item["evidence"].get("alert_level") == "RED"
        for item in items
    )
    level = "RED" if "BLOCKED" in statuses or replay_red else "AMBER" if "DEGRADED" in statuses else "GREEN"
    document = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "epoch_id": epoch_id,
        "epoch_source": epoch_source,
        "level": level,
        "counts": dict(sorted(Counter(item["status"] for item in items).items())),
        "items": items,
    }
    if write:
        target = report_dir / REPORT_FILE
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(json.dumps(document, indent=2, default=str), encoding="utf-8")
        temporary.replace(target)
    return document


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report_dir")
    parser.add_argument("data_dir")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(collect_input_blockers(args.report_dir, args.data_dir, write=args.write), indent=2, default=str))
