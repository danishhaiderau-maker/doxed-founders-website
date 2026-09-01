"""Analyzer-facing Safe Policy Genome V3 status and ranking report."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import csv
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from policy_search_manifest import POLICY_SEARCH_MANIFEST
from research_v3_contract import SAFE_POLICY_GENOME_CONTRACT, normalize_lifecycle_outcome
from research_v3_candidates import evaluate_protection_screen, load_candidate_inputs
from research_v3_ranking import rank_safe_policies
from research_v3_search import build_search_plan, search_progress
from research_v3_store import V3EvidenceStore
from research_dynamic_entry_policy import DEFAULT_CAUSAL_FEATURES
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    ACTIVE_TILE_REGISTRY,
    active_tile_registry_signature,
)

REPORT_FILE = "safe_policy_genome_v3_report.json"
EXHAUSTIVE_POLICY_FILE = "safe_policy_genome_v3_exhaustive.jsonl.gz"
EXHAUSTIVE_POLICY_MANIFEST_FILE = "safe_policy_genome_v3_exhaustive_manifest.json"


_PRE_ENTRY_FEATURE_PATHS = {
    "atr_bucket": (("atr_bucket",), ("research_buckets", "atr_bucket")),
    "realized_volatility_bucket": (
        ("realized_volatility_bucket",),
        ("research_buckets", "realized_volatility_bucket"),
        ("market_context", "realized_volatility_bucket"),
    ),
    "spread_bucket": (
        ("spread_bucket",), ("directional_spread_bucket",),
        ("research_buckets", "spread_bucket"),
        ("research_buckets", "directional_spread_bucket"),
    ),
    "depth_bucket": (
        ("depth_bucket",), ("research_buckets", "depth_bucket"),
        ("market_context", "depth_bucket"),
    ),
    "liquidity_bucket": (
        ("liquidity_bucket",), ("research_buckets", "liquidity_bucket"),
        ("market_context", "liquidity_bucket"),
    ),
    "regime": (
        ("regime",), ("entry_regime",), ("market_regime",),
        ("market_context", "regime"), ("market_context", "regime_label"),
    ),
    "direction": (
        ("direction",), ("final_direction",), ("raw_direction",),
        ("executed_direction",),
    ),
    "trend_strength_bucket": (
        ("trend_strength_bucket",),
        ("research_buckets", "trend_strength_bucket"),
        ("market_context", "trend_strength_bucket"),
    ),
}


def _nested_feature(features: dict[str, Any], paths) -> Any:
    for path in paths:
        value: Any = features
        for key in path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if value not in (None, ""):
            return value
    return None


def normalize_pre_entry_feature_receipt(
    receipt: dict[str, Any], *, signal_ts: Any,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Project one immutable receipt into the dynamic-policy causal schema.

    Scalars are timestamped only at the receipt's explicit pre-decision capture
    boundary. Explicit observation timestamps are retained and rejected when
    they cross the signal boundary. Missing dimensions stay missing so the
    dynamic-policy evaluator reports UNKNOWN rather than inventing a bucket.
    """
    if str(receipt.get("availability_boundary") or "") != "PRE_DECISION_ONLY":
        return {}, ["PRE_ENTRY_AVAILABILITY_BOUNDARY_INVALID"]
    features = receipt.get("features")
    if not isinstance(features, dict):
        return {}, ["PRE_ENTRY_FEATURE_PAYLOAD_INVALID"]
    try:
        captured_at = float(receipt.get("captured_at_ts"))
        signal_at = float(signal_ts)
    except (TypeError, ValueError):
        return {}, ["PRE_ENTRY_CAPTURE_TIMESTAMP_INVALID"]
    if captured_at > signal_at:
        return {}, ["PRE_ENTRY_CAPTURE_AFTER_SIGNAL"]

    normalized: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for name in DEFAULT_CAUSAL_FEATURES:
        value = _nested_feature(features, _PRE_ENTRY_FEATURE_PATHS[name])
        if value in (None, ""):
            blockers.append(f"MISSING_PRE_ENTRY_FEATURE:{name}")
            continue
        if isinstance(value, dict) and "value" in value:
            observation = value.get("value")
            try:
                observed_at = float(value.get("observed_ts"))
            except (TypeError, ValueError):
                blockers.append(f"FEATURE_TIMESTAMP_MISSING:{name}")
                continue
        else:
            observation = value
            observed_at = captured_at
        if observation in (None, ""):
            blockers.append(f"MISSING_PRE_ENTRY_FEATURE:{name}")
            continue
        if observed_at > signal_at:
            blockers.append(f"POST_ENTRY_FEATURE_LEAKAGE:{name}")
            continue
        normalized[name] = {"value": observation, "observed_ts": observed_at}
    return normalized, blockers


def join_pre_entry_feature_receipts(
    opportunities: list[dict[str, Any]], receipts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join exactly one causal receipt per opportunity; ambiguity is UNKNOWN."""
    by_episode: dict[str, list[dict[str, Any]]] = {}
    for receipt in receipts:
        by_episode.setdefault(str(receipt.get("episode_id") or ""), []).append(receipt)
    joined, blocker_counts = [], Counter()
    receipt_joined = schema_complete = 0
    for opportunity in opportunities:
        row = dict(opportunity)
        episode_id = str(row.get("episode_id") or "")
        matches = by_episode.get(episode_id, [])
        blockers: list[str]
        normalized: dict[str, dict[str, Any]] = {}
        if len(matches) == 0:
            blockers = ["PRE_ENTRY_FEATURE_RECEIPT_MISSING"]
        elif len(matches) != 1:
            blockers = ["PRE_ENTRY_FEATURE_RECEIPT_AMBIGUOUS"]
        else:
            receipt = matches[0]
            if str(receipt.get("opportunity_id") or "") != str(
                row.get("opportunity_id") or row.get("record_id") or ""
            ):
                blockers = ["PRE_ENTRY_OPPORTUNITY_ID_MISMATCH"]
            else:
                normalized, blockers = normalize_pre_entry_feature_receipt(
                    receipt, signal_ts=row.get("signal_ts"),
                )
                receipt_joined += 1
                if not blockers:
                    schema_complete += 1
        row["pre_entry_features"] = normalized
        row["pre_entry_feature_status"] = (
            "COMPLETE" if not blockers else "UNKNOWN"
        )
        row["pre_entry_feature_blockers"] = blockers
        blocker_counts.update(blockers)
        joined.append(row)
    return joined, {
        "opportunities": len(opportunities),
        "receipt_rows": len(receipts),
        "receipt_joined_opportunities": receipt_joined,
        "dynamic_schema_complete_opportunities": schema_complete,
        "unknown_opportunities": len(opportunities) - schema_complete,
        "blocker_counts": dict(sorted(blocker_counts.items())),
    }


def _deployed_policy_collection() -> dict[str, Any]:
    """Describe deployed paper policies independently of observed evidence."""
    policies = [
        {
            "lane": lane,
            "policy_id": ACTIVE_TILE_REGISTRY[lane]["raw_policy_id"],
            "policy_signature": ACTIVE_TILE_REGISTRY[lane]["policy_signature"],
            "collection_status": "COLLECTING_NO_CURRENT_EPOCH_EVIDENCE",
            "qualification_status": "NOT_QUALIFIED",
        }
        for lane in ACTIVE_TILE_ORDER
    ]
    epochs = {ACTIVE_TILE_REGISTRY[lane]["policy_epoch"] for lane in ACTIVE_TILE_ORDER}
    return {
        "policy_epoch": next(iter(epochs)) if len(epochs) == 1 else None,
        "policies": policies,
        "policy_count": len(policies),
        "qualification_allowed": False,
    }


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def _recover_expired_order_resolutions(
    data_dir: str | Path,
    expected_decisions: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Index exact expired-order receipts without inventing execution outcomes.

    Older collector builds could persist a paper order and its later expiry in
    ``expired_orders_3factor.csv`` while missing the normalized V3 order-intent
    append.  A unique expiry receipt proves that an order was submitted, so it
    resolves the decision-to-order integrity edge.  It does *not* prove a fill
    or no-fill: those classifications still require execution-grade BBO/depth
    and trade evidence.
    """
    path = Path(data_dir) / "expired_orders_3factor.csv"
    if not path.is_file():
        return {}
    decision_by_call_lane: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for decision in expected_decisions:
        shared_call = str(decision.get("shared_ai_call_id") or "").strip()
        lane = str(decision.get("research_lane") or "").strip().upper()
        if shared_call and lane:
            decision_by_call_lane.setdefault((shared_call, lane), []).append(decision)
    candidates: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                shared_call = str(row.get("shared_ai_call_id") or "").strip()
                lane = str(row.get("research_lane") or "").strip().upper()
                trade_id = str(row.get("trade_id") or "").strip()
                reason = str(row.get("reason") or "").strip().upper()
                decisions = decision_by_call_lane.get((shared_call, lane), [])
                if len(decisions) != 1 or not trade_id or not reason.endswith("TTL_EXPIRED"):
                    continue
                decision = decisions[0]
                key = (
                    str(decision.get("episode_id") or ""),
                    str(decision.get("policy_signature") or ""),
                    lane,
                )
                candidates.setdefault(key, []).append({
                    "trade_id": trade_id,
                    "shared_ai_call_id": shared_call,
                    "research_lane": lane,
                    "expired_at": row.get("time") or None,
                    "expired_ts": row.get("expired_ts") or None,
                    "reason": reason,
                    "touched_limit_diagnostic": str(row.get("touched_limit") or "").upper() == "TRUE",
                })
    except (OSError, csv.Error):
        return {}
    recovered: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key, rows in candidates.items():
        unique = {row["trade_id"]: row for row in rows}
        if len(unique) != 1:
            continue
        receipt = next(iter(unique.values()))
        recovered[key] = {
            **receipt,
            "resolution": "ORDER_SUBMITTED_THEN_EXPIRED",
            "execution_classification": "UNKNOWN",
            "unknown_reason_codes": [
                "UNKNOWN_EXECUTION_LEDGER_MISSING",
                "UNKNOWN_EXECUTION_GRADE_MARKET_EVIDENCE_MISSING",
            ],
            "source_path": "expired_orders_3factor.csv",
        }
    return recovered


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _source_revision(data_dir: str | Path) -> str:
    """Resolve the mirrored source identity without invoking Git or the runtime."""
    runtime_revision = str(
        os.getenv("SOURCE_GIT_REV")
        or os.getenv("RAILWAY_GIT_COMMIT_SHA")
        or os.getenv("GIT_REVISION")
        or ""
    ).strip()
    for name in ("canonical_dataset_current.json", ".fly-sync-state.json"):
        try:
            payload = json.loads((Path(data_dir) / name).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        revision = str(
            payload.get("source_revision")
            or payload.get("source_git_rev")
            or payload.get("revision")
            or ""
        ).strip()
        if revision:
            # The canonical manifest may intentionally store Fly's 12-character
            # display revision while the analyzer process is pinned to the full
            # commit. Preserve the full identity only when it proves the same
            # revision; otherwise fail closed to the canonical mirror value.
            if runtime_revision and runtime_revision.startswith(revision):
                return runtime_revision
            return revision
    return runtime_revision or "UNKNOWN"


def _exhaustive_policy_row(
    candidate: dict[str, Any],
    *,
    epoch_id: str,
    source_revision: str,
    analyzer_generation_revision: str,
    tile_config_signature: str,
) -> dict[str, Any]:
    """Project one evaluated policy into a stable, compact audit contract."""
    validation = candidate.get("validation") or {}
    risk = validation.get("risk") or {}
    bootstrap = validation.get("bootstrap") or {}
    ideal = candidate.get("ideal_touch_diagnostic") or {}
    full = int(candidate.get("full_fills") or 0)
    partial = int(candidate.get("partial_fills") or 0)
    no_fill = int(candidate.get("no_fills") or 0)
    unknown = int(candidate.get("unsupported_episodes") or 0)
    supported = int(candidate.get("supported_conservative_episodes") or 0)
    independent = int(candidate.get("oos_episodes") or 0)
    unavailable_reasons = []
    if supported == 0:
        unavailable_reasons.append("NO_SUPPORTED_CONSERVATIVE_EPISODES")
    if full + partial == 0:
        unavailable_reasons.append("NO_CONSERVATIVE_FILLS")
    if unknown:
        unavailable_reasons.append("UNSUPPORTED_OR_MISSING_EXECUTION_EVIDENCE")
    if candidate.get("comparison_cohort_key") is None:
        unavailable_reasons.append("COMPARISON_COHORT_IDENTITY_INCOMPLETE")
    policy_identity_verified = not str(candidate.get("policy_signature") or "").startswith("UNVERIFIED-")
    if not policy_identity_verified:
        unavailable_reasons.append("POLICY_SIGNATURE_MISSING")
    metrics_available = full + partial > 0 and risk.get("net_pnl_usd") is not None
    if metrics_available and (
        bootstrap.get("mean_lcb95") is None or bootstrap.get("mean_ucb95") is None
    ):
        unavailable_reasons.append("CONFIDENCE_INTERVAL_UNAVAILABLE")
    if metrics_available and all(
        candidate.get(field) is None
        for field in ("funding_cost_usd", "slippage_usd", "fee_cost_usd")
    ):
        unavailable_reasons.append("FUNDING_SLIPPAGE_FEE_AGGREGATES_UNAVAILABLE")
    return {
        "schema": "safe_policy_exhaustive_row_v1",
        "source_revision": source_revision,
        "analyzer_generation_revision": analyzer_generation_revision,
        "epoch_id": epoch_id,
        "tile_config_signature": tile_config_signature,
        "policy_id": candidate.get("policy_id"),
        "policy_signature": candidate.get("policy_signature"),
        "policy_identity_verified": policy_identity_verified,
        "policy_family": candidate.get("policy_family") or "UNKNOWN",
        "policy_spec": candidate.get("policy_spec") or {},
        "evidence_world": candidate.get("evidence_world") or "CONSERVATIVE_BBO_DEPTH_V1",
        "qualification": candidate.get("qualification") or "DESCRIPTIVE_ONLY",
        "qualification_gates": candidate.get("gates") or validation.get("gates") or {},
        "independent_episode_count": independent,
        "episodes_total": int(candidate.get("episodes_total") or 0),
        "supported_episode_count": supported,
        "full_fill_count": full,
        "partial_fill_count": partial,
        "no_fill_count": no_fill,
        "unknown_count": unknown,
        "fill_count": full + partial,
        "fill_rate": candidate.get("conservative_fill_rate"),
        "wins": risk.get("wins") if metrics_available else None,
        "losses": risk.get("losses") if metrics_available else None,
        "net_pnl_usd": risk.get("net_pnl_usd") if metrics_available else None,
        "ev_per_independent_episode_usd": (
            round(float(risk["net_pnl_usd"]) / independent, 8)
            if metrics_available and independent else None
        ),
        "max_drawdown_usd": risk.get("max_drawdown_usd") if metrics_available else None,
        "max_drawdown_pct": risk.get("max_drawdown_pct") if metrics_available else None,
        "cvar95_usd": risk.get("cvar95_usd") if metrics_available else None,
        "expected_shortfall_usd": risk.get("cvar95_usd") if metrics_available else None,
        "longest_losing_sequence": risk.get("longest_loss_streak") if metrics_available else None,
        "expectancy_lcb95_usd": bootstrap.get("mean_lcb95") if metrics_available else None,
        "expectancy_ucb95_usd": bootstrap.get("mean_ucb95") if metrics_available else None,
        "confidence_interval_available": bool(
            metrics_available
            and bootstrap.get("mean_lcb95") is not None
            and bootstrap.get("mean_ucb95") is not None
        ),
        # The current protection evaluator does not aggregate these costs at
        # candidate level. Retain explicit nulls rather than silently implying
        # zero cost; unavailable_reasons below explains the evidence gap.
        "funding_cost_usd": candidate.get("funding_cost_usd") if metrics_available else None,
        "slippage_usd": candidate.get("slippage_usd") if metrics_available else None,
        "fee_cost_usd": candidate.get("fee_cost_usd") if metrics_available else None,
        "ideal_touch_diagnostic": {
            "evidence_world": ideal.get("evidence_world") or "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
            "touches": int(ideal.get("touches") or 0),
            "no_touches": int(ideal.get("no_touches") or 0),
            "wins": ideal.get("wins"),
            "losses": ideal.get("losses"),
            "net_pnl_usd": ideal.get("oos_net_usd"),
            "max_drawdown_usd": ideal.get("max_drawdown_usd"),
            "qualification_eligible": False,
        },
        "comparison_cohort": candidate.get("comparison_cohort") or {},
        "receipt_identity": candidate.get("receipt_identity") or {},
        "regime_breakdown": candidate.get("regime_breakdown") or {},
        "risk_metrics_available": metrics_available,
        "unavailable_reasons": sorted(set(unavailable_reasons)),
    }


def _persist_exhaustive_policies(
    report_dir: str | Path,
    candidates: list[dict[str, Any]],
    *,
    epoch_id: str,
    source_revision: str,
    analyzer_generation_revision: str,
    tile_config_signature: str,
) -> dict[str, Any]:
    """Atomically persist every unique evaluated policy as compressed JSONL."""
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        policy_id = str(candidate.get("policy_id") or "").strip()
        signature = str(candidate.get("policy_signature") or "").strip()
        if not policy_id:
            raise ValueError("exhaustive policy row missing policy_id")
        if not signature:
            # Externally supplied compatibility candidates in focused tests or
            # migrations can predate signed policy identities. Retain them
            # under an explicit non-qualified deterministic identity rather
            # than silently dropping the evaluated row.
            signature = "UNVERIFIED-" + hashlib.sha256(policy_id.encode("utf-8")).hexdigest()[:24]
            candidate = {**candidate, "policy_signature": signature}
        key = (policy_id, signature)
        projected = _exhaustive_policy_row(
            candidate,
            epoch_id=epoch_id,
            source_revision=source_revision,
            analyzer_generation_revision=analyzer_generation_revision,
            tile_config_signature=tile_config_signature,
        )
        previous = unique.get(key)
        if previous is not None and previous != projected:
            raise ValueError(f"conflicting duplicate exhaustive policy row: {policy_id}")
        unique[key] = projected

    destination = Path(report_dir) / EXHAUSTIVE_POLICY_FILE
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            for key in sorted(unique):
                compressed.write(
                    (json.dumps(unique[key], sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                )
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(temporary, destination)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest = {
        "schema": "safe_policy_exhaustive_manifest_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": source_revision,
        "analyzer_generation_revision": analyzer_generation_revision,
        "epoch_id": epoch_id,
        "tile_config_signature": tile_config_signature,
        "artifact": EXHAUSTIVE_POLICY_FILE,
        "compression": "gzip",
        "row_schema": "safe_policy_exhaustive_row_v1",
        "row_count": len(unique),
        "sha256": checksum,
        "size_bytes": destination.stat().st_size,
        "deduplication_key": ["policy_id", "policy_signature"],
        "independence_basis": "ONE_SHARED_OPPORTUNITY_EPISODE_NOT_SIBLING_LANE_COUNT",
    }
    _atomic_json(Path(report_dir) / EXHAUSTIVE_POLICY_MANIFEST_FILE, manifest)
    return manifest


def _fresh_cutoff(data_dir: str | Path) -> float | None:
    try:
        session = json.loads((Path(data_dir) / "research_session.json").read_text(encoding="utf-8"))
        return float(session.get("fresh_collection_start_time") or session.get("bot_start_time"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _select_current_epoch(opportunities: list[dict[str, Any]], cutoff: float | None) -> str | None:
    eligible = []
    for row in opportunities:
        epoch_id = str(row.get("epoch_id") or "")
        try:
            signal_ts = float(row.get("signal_ts"))
        except (TypeError, ValueError):
            signal_ts = 0.0
            if cutoff is not None:
                continue
        if epoch_id and (cutoff is None or signal_ts >= cutoff):
            eligible.append((signal_ts, epoch_id))
    return max(eligible)[1] if eligible else None


def _exclude_identity_aliases(opportunities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prefer the causal shared-ID row when an enrichment retry minted a fallback alias."""
    parents = list(range(len(opportunities)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parents[right] = left

    first_by_shared: dict[str, int] = {}
    first_by_fingerprint: dict[tuple[float, str, str], int] = {}
    for index, row in enumerate(opportunities):
        shared = str(row.get("shared_ai_call_id") or "").strip()
        if shared:
            if shared in first_by_shared:
                union(index, first_by_shared[shared])
            else:
                first_by_shared[shared] = index
        try:
            signal_ts = float(row.get("signal_ts"))
        except (TypeError, ValueError):
            signal_ts = -1.0
        fingerprint = (
            signal_ts,
            str(row.get("symbol") or "").upper(),
            str(row.get("raw_direction") or "").upper(),
        )
        if fingerprint in first_by_fingerprint:
            union(index, first_by_fingerprint[fingerprint])
        else:
            first_by_fingerprint[fingerprint] = index
    grouped: dict[int, list[dict[str, Any]]] = {}
    for index, row in enumerate(opportunities):
        grouped.setdefault(find(index), []).append(row)
    kept, excluded = [], []
    for rows in grouped.values():
        rows = sorted(
            rows,
            key=lambda row: (
                0 if str(row.get("grouping_basis") or "") == "SHARED_AI_CALL" else 1,
                str(row.get("episode_id") or ""),
            ),
        )
        kept.append(rows[0])
        excluded.extend(rows[1:])
    return kept, excluded


def _shared_call_independence_clusters(
    opportunities: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Describe the causal clusters used for independent-opportunity counts.

    A shared AI call may fan out into several policy/lane decisions.  Those
    children remain useful paired evidence, but they are not independent
    observations.  Keep that distinction explicit in the report contract.
    """
    decisions_by_episode: dict[str, list[dict[str, Any]]] = {}
    for row in decisions:
        episode_id = str(row.get("episode_id") or "").strip()
        if episode_id:
            decisions_by_episode.setdefault(episode_id, []).append(row)

    clusters = []
    for row in opportunities:
        episode_id = str(row.get("episode_id") or "").strip()
        shared_call_id = str(row.get("shared_ai_call_id") or "").strip()
        grouping_basis = "SHARED_AI_CALL" if shared_call_id else str(
            row.get("grouping_basis") or "EPISODE_ID_FALLBACK"
        )
        children = decisions_by_episode.get(episode_id, [])
        child_identities = {
            (
                str(child.get("research_lane") or "UNKNOWN"),
                str(child.get("policy_signature") or child.get("policy_id") or "UNKNOWN"),
            )
            for child in children
        }
        clusters.append({
            "cluster_id": shared_call_id or episode_id,
            "grouping_basis": grouping_basis,
            "episode_id": episode_id,
            "shared_ai_call_id": shared_call_id or None,
            "child_decision_count": len(children),
            "child_lane_count": len(child_identities),
        })
    return clusters


def build_safe_policy_genome_v3_report(data_dir=".", report_dir=".", *, candidates=None) -> dict[str, Any]:
    v3_root = Path(data_dir) / "v3"
    all_opportunities = _read_ledger(v3_root / "ledgers" / "opportunity.jsonl")
    cutoff = _fresh_cutoff(data_dir)
    selected_epoch = _select_current_epoch(all_opportunities, cutoff)
    epoch_id = selected_epoch or "V3_NOT_STARTED"
    store = V3EvidenceStore(data_dir, epoch_id=epoch_id)
    verification = store.verify()
    def scoped(rows):
        return [row for row in rows if selected_epoch is not None and str(row.get("epoch_id") or "") == selected_epoch]

    opportunities = scoped(all_opportunities)
    if cutoff is not None:
        opportunities = [row for row in opportunities if float(row.get("signal_ts") or 0) >= cutoff]
    opportunities, identity_aliases = _exclude_identity_aliases(opportunities)
    allowed_episodes = {str(row.get("episode_id") or "") for row in opportunities}
    pre_entry_feature_receipts = [
        row for row in scoped(_read_ledger(store.ledger_path("pre_entry_features")))
        if str(row.get("episode_id") or "") in allowed_episodes
    ]
    opportunities, pre_entry_feature_coverage = join_pre_entry_feature_receipts(
        opportunities, pre_entry_feature_receipts,
    )
    decisions = [row for row in scoped(_read_ledger(store.ledger_path("decision"))) if str(row.get("episode_id") or "") in allowed_episodes]
    independence_clusters = _shared_call_independence_clusters(opportunities, decisions)
    order_intents = [row for row in scoped(_read_ledger(store.ledger_path("order_intent"))) if str(row.get("episode_id") or "") in allowed_episodes]
    lifecycles = [row for row in scoped(_read_ledger(store.ledger_path("lifecycle"))) if str(row.get("episode_id") or "") in allowed_episodes]
    terminal_lifecycles = [row for row in lifecycles if row.get("terminal") is True]
    executions = [row for row in scoped(_read_ledger(store.ledger_path("execution"))) if str(row.get("episode_id") or "") in allowed_episodes]
    market_segment_rows = [
        row for row in scoped(_read_ledger(store.ledger_path("market_segment")))
        if str(row.get("episode_id") or "") in allowed_episodes
    ]
    pre_signal_context_segments = [
        row for row in market_segment_rows
        if str(row.get("context_role") or "").upper() == "PRE_SIGNAL_ONLY"
        or (row.get("coverage") or {}).get("future_exit_path_included") is False
    ]
    terminal_path_segments = [
        row for row in market_segment_rows if row not in pre_signal_context_segments
    ]
    observed_epochs = sorted({str(row.get("epoch_id")) for row in all_opportunities if row.get("epoch_id")})
    excluded_opportunities = len(all_opportunities) - len(opportunities)
    policy_ids_by_signature: dict[str, set[str]] = {}
    signatures_by_episode_policy: dict[tuple[str, str], set[str]] = {}
    missing_policy_identity_rows = 0
    pending_policy_identity_rows = 0
    immediate_lane_decisions = [
        row for row in decisions
        if str(row.get("decision_stage") or "") == "LANE_POLICY_VERDICT"
    ]
    now_ts = datetime.now(timezone.utc).timestamp()
    reconciliation_deadlines_by_episode: dict[str, list[float]] = {}
    for decision in immediate_lane_decisions:
        if decision.get("order_intent_expected") is True:
            reconciliation_deadlines_by_episode.setdefault(
                str(decision.get("episode_id") or ""), []
            ).append(float(decision.get("resolution_deadline_ts") or 0))

    def identity_is_still_reconciling(row: dict[str, Any]) -> bool:
        deadlines = reconciliation_deadlines_by_episode.get(
            str(row.get("episode_id") or ""), []
        )
        return bool(deadlines and max(deadlines) > now_ts)
    policy_attributable_lifecycles = [
        row for row in lifecycles
        if str(row.get("observation_status") or "") in {
            "PAPER_POSITION_OPEN", "PAPER_POSITION_CLOSED",
        }
    ]
    for row in immediate_lane_decisions:
        signature = str(row.get("policy_signature") or "").strip()
        policy_id = str(row.get("policy_id") or "").strip()
        policy_epoch_id = str(row.get("policy_epoch_id") or "").strip()
        if not signature or not policy_id or not policy_epoch_id:
            missing_policy_identity_rows += 1
            continue
        policy_ids_by_signature.setdefault(signature, set()).add(policy_id)
        signatures_by_episode_policy.setdefault(
            (str(row.get("episode_id") or ""), policy_id), set()
        ).add(signature)
    for row in order_intents:
        signature = str(row.get("policy_signature") or "").strip()
        policy_id = str(row.get("policy_id") or "").strip()
        policy_epoch_id = str(row.get("policy_epoch_id") or "").strip()
        if not signature or not policy_id or not policy_epoch_id:
            if identity_is_still_reconciling(row):
                pending_policy_identity_rows += 1
            else:
                missing_policy_identity_rows += 1
            continue
        policy_ids_by_signature.setdefault(signature, set()).add(policy_id)
        signatures_by_episode_policy.setdefault(
            (str(row.get("episode_id") or ""), policy_id), set()
        ).add(signature)
    for row in [*executions, *policy_attributable_lifecycles]:
        signature = str(row.get("policy_signature") or "").strip()
        policy_id = str(row.get("policy_id") or "").strip()
        policy_epoch_id = str(row.get("policy_epoch_id") or "").strip()
        research_lane = str(row.get("research_lane") or "").strip()
        shared_ai_call_id = str(row.get("shared_ai_call_id") or "").strip()
        if not all((signature, policy_id, policy_epoch_id, research_lane, shared_ai_call_id)):
            if identity_is_still_reconciling(row):
                pending_policy_identity_rows += 1
            else:
                missing_policy_identity_rows += 1
            continue
        policy_ids_by_signature.setdefault(signature, set()).add(policy_id)
        signatures_by_episode_policy.setdefault(
            (str(row.get("episode_id") or ""), policy_id), set()
        ).add(signature)
    policy_signature_collisions = {
        signature: sorted(policy_ids)
        for signature, policy_ids in policy_ids_by_signature.items()
        if len(policy_ids) > 1
    }
    policy_signature_divergence = {
        f"{episode_id}:{policy_id}": sorted(signatures)
        for (episode_id, policy_id), signatures in signatures_by_episode_policy.items()
        if len(signatures) > 1
    }
    paper_world_contradiction_rows = []
    policy_provenance_rows = [
        *immediate_lane_decisions,
        *order_intents,
        *executions,
        *policy_attributable_lifecycles,
    ]
    for row in policy_provenance_rows:
        if str(row.get("policy_execution_scope") or "") != "PAPER_RESEARCH_ONLY":
            continue
        spec = row.get("paper_policy_spec")
        spec_paper_only = spec.get("paper_only") if isinstance(spec, dict) else None
        if row.get("paper_only") is not False and spec_paper_only is not False:
            continue
        paper_world_contradiction_rows.append({
            "record_id": str(row.get("record_id") or ""),
            "episode_id": str(row.get("episode_id") or ""),
            "policy_id": str(row.get("policy_id") or ""),
            "top_level_paper_only": row.get("paper_only"),
            "spec_paper_only": spec_paper_only,
        })
    policy_identity_contamination = bool(
        policy_signature_collisions or policy_signature_divergence
        or missing_policy_identity_rows or paper_world_contradiction_rows
    )
    contamination = bool(
        excluded_opportunities or identity_aliases or len(observed_epochs) > 1
        or policy_identity_contamination
    )
    outcome_counts = Counter(normalize_lifecycle_outcome(
        row.get("outcome_state"), net_pnl_usd=row.get("net_pnl_usd")
    ) for row in terminal_lifecycles)
    decision_outcomes = Counter(str(
        row.get("primary_outcome")
        or row.get("outcome_state")
        or row.get("policy_decision")
        or "UNKNOWN"
    ) for row in decisions)
    decision_dispositions = Counter(str(
        row.get("execution_disposition") or "LEGACY_TERMINAL_DECISION"
    ) for row in decisions)
    lane_decision_outcomes: dict[str, Counter] = {}
    for row in immediate_lane_decisions:
        lane = str(row.get("research_lane") or "UNKNOWN")
        outcome = str(row.get("outcome_state") or row.get("policy_decision") or "UNKNOWN")
        lane_decision_outcomes.setdefault(lane, Counter())[outcome] += 1
    def resolution_key(row):
        return (
            str(row.get("episode_id") or ""),
            str(row.get("policy_signature") or ""),
            str(row.get("research_lane") or "").upper(),
        )
    expected_order_decisions = [
        row for row in immediate_lane_decisions if row.get("order_intent_expected") is True
    ]
    intent_keys = {resolution_key(row) for row in order_intents}
    recovered_expired_orders = _recover_expired_order_resolutions(
        data_dir, expected_order_decisions,
    )
    entry_resolutions: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in lifecycles:
        if row.get("resolution_scope") == "LANE_ENTRY":
            entry_resolutions.setdefault(resolution_key(row), []).append(row)
    entry_resolution_counts = Counter()
    orphan_expected_orders = []
    applied_expired_order_recoveries: dict[tuple[str, str, str], dict[str, Any]] = {}
    for decision in expected_order_decisions:
        key = resolution_key(decision)
        rows = entry_resolutions.get(key, [])
        states = {str(row.get("entry_resolution") or "") for row in rows}
        if key in intent_keys or "ORDER_SUBMITTED" in states:
            entry_resolution_counts["submitted"] += 1
        elif "NO_ORDER" in states:
            entry_resolution_counts["terminal_no_order"] += 1
        elif key in recovered_expired_orders:
            # The expiry receipt proves the missing order-intent edge, while
            # deliberately leaving the execution result UNKNOWN. Existing V3
            # intent/lifecycle resolutions always remain authoritative.
            entry_resolution_counts["submitted"] += 1
            entry_resolution_counts["recovered_expired_order"] += 1
            applied_expired_order_recoveries[key] = recovered_expired_orders[key]
        else:
            deadline = float(decision.get("resolution_deadline_ts") or 0)
            awaiting_deadlines = [float(row.get("resolution_deadline_ts") or 0) for row in rows if row.get("entry_resolution") == "AWAITING"]
            deadline = max([deadline, *awaiting_deadlines])
            if deadline > now_ts:
                entry_resolution_counts["awaiting_within_deadline"] += 1
            else:
                entry_resolution_counts["overdue_orphan"] += 1
                orphan_expected_orders.append({
                    "episode_id": key[0], "policy_signature": key[1],
                    "research_lane": key[2], "resolution_deadline_ts": deadline or None,
                })
    entry_resolution_integrity = {
        "expected": len(expected_order_decisions),
        "submitted": entry_resolution_counts["submitted"],
        "recovered_expired_order": entry_resolution_counts["recovered_expired_order"],
        "terminal_no_order": entry_resolution_counts["terminal_no_order"],
        "awaiting_within_deadline": entry_resolution_counts["awaiting_within_deadline"],
        "overdue_orphan": entry_resolution_counts["overdue_orphan"],
        "orphan_expected_orders": orphan_expected_orders,
        "recovered_expired_orders": [
            {
                "episode_id": key[0],
                "policy_signature": key[1],
                **receipt,
            }
            for key, receipt in sorted(applied_expired_order_recoveries.items())
        ],
        "recovery_semantics": (
            "A unique expired-order receipt proves ORDER_SUBMITTED only; "
            "execution remains UNKNOWN without execution-grade market evidence."
        ),
        "passed": entry_resolution_counts["overdue_orphan"] == 0,
    }
    effective_paper_execution_identities = []
    seen_effective_identities = set()
    for row in [*order_intents, *executions, *policy_attributable_lifecycles]:
        signature = str(row.get("policy_signature") or "").strip()
        if not signature or signature in seen_effective_identities:
            continue
        seen_effective_identities.add(signature)
        spec = row.get("paper_policy_spec") or {}
        relay_capable = bool(spec.get("relay_eligible", row.get("relay_eligible", False)))
        effective_paper_execution_identities.append({
            "policy_signature": signature,
            "policy_epoch_id": row.get("policy_epoch_id"),
            "policy_id": row.get("policy_id"),
            "research_lane": row.get("research_lane"),
            "effective_execution_mode": "PAPER_OBSERVED",
            "live_relay_capable": relay_capable,
            "relay_capability_note": (
                "Capability metadata only; paper evidence does not authorize live relay."
            ),
        })
    search = build_search_plan({
        "entry_offset_pct": list((POLICY_SEARCH_MANIFEST.get("dimensions") or {}).get("entry_offset_pct") or []),
        "entry_ttl_min": list((POLICY_SEARCH_MANIFEST.get("dimensions") or {}).get("entry_ttl_min") or []),
        "chase_policy_id": list((POLICY_SEARCH_MANIFEST.get("dimensions") or {}).get("chase_policy_id") or []),
    })
    candidate_screen = None
    if candidates is None:
        def emit_candidate_progress(receipt):
            print(
                "  V3.1 protection replay: "
                f"{receipt.get('input_events_completed', 0)}/"
                f"{receipt.get('input_events_total', 0)} events; "
                f"{receipt.get('protection_variants', 0)} protection variants; "
                f"{receipt.get('policies_materialized', 0)} policies",
                flush=True,
            )

        candidate_screen = evaluate_protection_screen(
            load_candidate_inputs(
                data_dir,
                epoch_id=selected_epoch,
                minimum_signal_ts=cutoff,
            ),
            progress_callback=emit_candidate_progress,
        )
        candidates = candidate_screen["candidates"]
    ranking = rank_safe_policies(candidates or [])
    if not entry_resolution_integrity["passed"]:
        # Preserve descriptive rows, but never surface a qualified winner from
        # a cohort whose expected entry outcomes are still missing.
        ranking = dict(ranking)
        ranking["number_one"] = None
        ranking["qualification"] = "BLOCKED_ORDER_RESOLUTION_INTEGRITY"
    exhaustive_manifest = _persist_exhaustive_policies(
        report_dir,
        list(candidates or []),
        epoch_id=epoch_id,
        source_revision=_source_revision(data_dir),
        analyzer_generation_revision=str(
            os.getenv("SOURCE_GIT_REV")
            or os.getenv("RAILWAY_GIT_COMMIT_SHA")
            or os.getenv("GIT_REVISION")
            or "UNKNOWN"
        ),
        tile_config_signature=active_tile_registry_signature(),
    )
    # The replay engine may assess tens of thousands of complete policies.  The
    # full candidate rows are working memory, not a report contract: persisting
    # them under both candidate_screen.candidates and ranking.blocked made a
    # small V3.1 cohort produce a ~200 MB artifact and held the scheduled
    # analyzer in JSON serialization for minutes.  Keep the auditable counts,
    # blocker distribution, bounded leaderboards and at most the public top 100
    # qualified policies.  This is also the dashboard's documented exposure.
    persisted_candidate_screen = dict(candidate_screen or {
        "schema": "externally_supplied_safe_policy_candidates_v3",
        "unique_policies_evaluated": len(candidates or []),
        "descriptive_top_100": [],
        "profit_capture_leaders": {},
        "drawdown_control_leaders": [],
        "dynamic_regime_leaders": {},
    })
    persisted_candidate_screen.pop("candidates", None)
    blocked_rows = list(ranking.get("blocked") or [])
    blocker_counts = Counter(
        blocker
        for row in blocked_rows
        for blocker in (row.get("ranking_blockers") or [])
    )
    persisted_ranking = dict(ranking)
    persisted_ranking.pop("blocked", None)
    persisted_ranking["ranked"] = list(ranking.get("ranked") or [])[:100]
    persisted_ranking["blocked_policy_count"] = len(blocked_rows)
    persisted_ranking["blocked_gate_counts"] = dict(sorted(blocker_counts.items()))
    progress_receipts = []
    if candidate_screen is not None:
        progress_receipts.append({
            "unique_policies_evaluated": candidate_screen.get("unique_policies_evaluated", 0),
            "independent_episodes": len({row.get("episode_id") for row in opportunities if row.get("episode_id")}),
        })
    report = {
        "schema": "safe_policy_genome_v3_1_report_v1",
        "extension": "ADAPTIVE_EXIT_AND_DRAWDOWN_LAB_V3_1",
        "data_scope": "FRESH-COLLECTION" if selected_epoch is not None else "SESSION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "V3_INTEGRITY_FAILED" if not verification["passed"] else "V3_ORDER_RESOLUTION_INTEGRITY_FAILED" if not entry_resolution_integrity["passed"] else "V3_EPOCH_CONTAMINATION_BLOCKED" if contamination else "V3_COLLECTING" if opportunities else "V3_READY_FOR_FRESH_EPOCH",
        "live_policy_change_allowed": False,
        "real_bitfinex_trading_allowed": False,
        "epoch_id": epoch_id,
        "deployed_policy_collection": _deployed_policy_collection(),
        "contract": SAFE_POLICY_GENOME_CONTRACT,
        "integrity": verification,
        "epoch_scope": {
            "selected_epoch_id": selected_epoch,
            "fresh_cutoff_ts": cutoff,
            "observed_opportunity_epochs": observed_epochs,
            "active_opportunity_rows": len(all_opportunities),
            "included_fresh_rows": len(opportunities),
            "excluded_stale_or_foreign_rows": excluded_opportunities,
            "excluded_identity_alias_rows": len(identity_aliases),
            "identity_alias_episode_ids": sorted(str(row.get("episode_id") or "") for row in identity_aliases),
            "missing_policy_identity_rows": missing_policy_identity_rows,
            "pending_policy_identity_rows": pending_policy_identity_rows,
            "policy_signature_collisions": policy_signature_collisions,
            "policy_signature_divergence": policy_signature_divergence,
            "paper_world_contradiction_count": len(paper_world_contradiction_rows),
            "paper_world_contradiction_rows": paper_world_contradiction_rows,
            "contamination_detected": contamination,
        },
        "collection": {
            "independent_opportunities": len({row.get("episode_id") for row in opportunities if row.get("episode_id")}),
            "independence_grouping_basis": "SHARED_AI_CALL_WITH_EPISODE_ID_FALLBACK",
            "independence_clusters": independence_clusters,
            "independent_cluster_count": len(independence_clusters),
            "correlated_child_decision_count": sum(
                cluster["child_decision_count"] for cluster in independence_clusters
            ),
            "decision_branches": len(decisions),
            "execution_rows": len(executions),
            "terminal_lifecycles": len(terminal_lifecycles),
            "provisional_lifecycles": len(lifecycles) - len(terminal_lifecycles),
            "decision_outcomes": dict(sorted(decision_outcomes.items())),
            "decision_dispositions": dict(sorted(decision_dispositions.items())),
            "lane_decision_outcomes": {
                lane: dict(sorted(counts.items()))
                for lane, counts in sorted(lane_decision_outcomes.items())
            },
            "outcome_states": dict(sorted(outcome_counts.items())),
            "ledger_counts": verification["ledger_counts"],
            "pre_entry_feature_evidence": pre_entry_feature_coverage,
            # Qualification requires a signal-to-terminal market path.  A
            # frozen pre-signal context segment makes rejected/NO_TRADE regime
            # analysis auditable, but must never satisfy the execution-path
            # maturity gate merely because it is stored in the same
            # content-addressed segment library.
            "market_segments": len(terminal_path_segments),
            "terminal_path_market_segments": len(terminal_path_segments),
            "pre_signal_context_segments": len(pre_signal_context_segments),
            "market_segment_ledger_rows": len(market_segment_rows),
            "market_segment_objects_verified": verification["market_segment_count"],
            "entry_resolution_integrity": entry_resolution_integrity,
            "effective_paper_execution_identities": effective_paper_execution_identities,
        },
        "search": search,
        "search_progress": search_progress(search, progress_receipts),
        "candidate_screen": persisted_candidate_screen,
        "exhaustive_policy_results": exhaustive_manifest,
        "safe_policy_ranking": persisted_ranking,
        "number_one_strategy": ranking["number_one"],
        "qualification": ranking["qualification"],
        "blockers": (["V3_DATA_INTEGRITY_FAILED"] if not verification["passed"] else []) + (["ORPHAN_EXPECTED_ORDER"] if not entry_resolution_integrity["passed"] else []) + (["PRE_ENTRY_FEATURE_EVIDENCE_INCOMPLETE"] if pre_entry_feature_coverage["unknown_opportunities"] else []) + (["MIXED_OR_PRE_CUTOFF_V3_EVIDENCE_EXCLUDED"] if excluded_opportunities or len(observed_epochs) > 1 else []) + (["CAUSAL_IDENTITY_ALIAS_EXCLUDED"] if identity_aliases else []) + (["POLICY_IDENTITY_CONTAMINATION"] if policy_identity_contamination else []) + (["NO_SAFE_QUALIFIED_POLICY"] if not ranking["number_one"] else []),
        "note": "Number one is selected only among policies passing every integrity, conservative-execution, sealed-OOS, drawdown, CVaR, liquidation, stability, multiple-testing and regime gate.",
    }
    _atomic_json(Path(report_dir) / REPORT_FILE, report)
    return report
