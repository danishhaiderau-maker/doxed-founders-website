"""Equal-rights ranking for paper, shadow, and counterfactual worlds.

Every surface uses the same columns. The rank key is after-cost expectancy.
A SAFE badge is emitted only when every required safety gate is explicitly
true on closed evidence. Missing worlds stay visible as EMPTY / NO_SAFE.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from research_v3_ranking import REQUIRED_GATES


SCHEMA = "equal_rights_ranking_v1"
PRIMARY_METRIC = "after_cost_expectancy_usd"

SURFACES = (
    {"id": "paper", "label": "Paper", "world": "OBSERVED_PAPER"},
    {"id": "shadow", "label": "Shadow", "world": "IDEAL_TOUCH"},
    {"id": "counterfactual", "label": "Counterfactual", "world": "CONSERVATIVE_BBO"},
)
SURFACE_IDS = tuple(row["id"] for row in SURFACES)
WORLD_BY_SURFACE = {row["id"]: row["world"] for row in SURFACES}

_CLOSED_WITHOUT_NET = {
    "PAPER_REALIZED",
    "REALIZED_PROFIT",
    "REALIZED_LOSS",
    "REALIZED_ZERO_PNL",
}
_NOT_A_CLOSE = {
    "NO_FILL",
    "NO_TRADE",
    "REJECTED",
    "CENSORED",
    "UNSUPPORTED",
    "DATA_ERROR",
    "PENDING_FILL",
}
_PAPER_TOKENS = (
    "OBSERVED_PAPER",
    "PAPER_OBSERVED",
    "SHOWCASE_PAPER",
    "PAPER_POSITION",
    "PAPER_REALIZED",
    "LEGACY_PAPER",
)
_BLOCKED_TOKENS = ("REJECT", "BLOCK", "DISABLED", "NO_TRADE", "NO_ORDER")


def _text_blob(row: Mapping[str, Any]) -> str:
    parts: list[str] = []

    def add(value: Any) -> None:
        if value is None or value == "":
            return
        if isinstance(value, Mapping):
            return
        parts.append(str(value).upper())

    add(row.get("execution_world"))
    add(row.get("effective_execution_mode"))
    add(row.get("fill_model"))
    add(row.get("observation_status"))
    add(row.get("outcome_state"))
    fill = row.get("fill")
    if isinstance(fill, Mapping):
        add(fill.get("execution_world"))
        add(fill.get("fill_model"))
    spec = row.get("policy_spec")
    if isinstance(spec, Mapping):
        spec_fill = spec.get("fill")
        if isinstance(spec_fill, Mapping):
            add(spec_fill.get("execution_world"))
            add(spec_fill.get("source_fill_model"))
    return " ".join(parts)


def classify_world(row: Mapping[str, Any]) -> str | None:
    """Map a ledger or candidate row onto one collector world."""
    blob = _text_blob(row)
    if "CONSERVATIVE_BBO" in blob:
        return "CONSERVATIVE_BBO"
    if "IDEAL_TOUCH" in blob:
        return "IDEAL_TOUCH"
    if any(token in blob for token in _PAPER_TOKENS):
        return "OBSERVED_PAPER"
    return None


def _world_surface(world: str | None) -> str | None:
    for surface in SURFACES:
        if surface["world"] == world:
            return surface["id"]
    return None


def _policy_id(row: Mapping[str, Any]) -> str:
    for key in ("policy_id", "policy_signature", "research_lane"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return "UNATTRIBUTED"


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def gates_all_pass(gates: Any) -> bool:
    if not isinstance(gates, Mapping):
        return False
    return all(gates.get(name) is True for name in REQUIRED_GATES)


def _merge_gates(current: Any, incoming: Any) -> Any:
    """Missing or conflicting gates fail closed. They never become SAFE."""
    if not gates_all_pass(incoming):
        return False
    if current is False:
        return False
    if current is None:
        return {name: True for name in REQUIRED_GATES}
    if not gates_all_pass(current):
        return False
    return {name: True for name in REQUIRED_GATES}


class _Accumulator:
    def __init__(self) -> None:
        self.closed_n = 0
        self.fills = 0
        self.net = 0.0
        self.net_known = True
        self.max_drawdown_usd: float | None = None
        self.gates: Any = None

    def add(self, closed_n: int, net: float | None, gates: Any,
            fills: int = 0, max_drawdown_usd: float | None = None) -> None:
        if closed_n <= 0:
            return
        self.closed_n += closed_n
        self.fills += max(0, fills)
        if net is None:
            self.net_known = False
        else:
            self.net += net
        if max_drawdown_usd is not None:
            if self.max_drawdown_usd is None:
                self.max_drawdown_usd = max_drawdown_usd
            else:
                self.max_drawdown_usd = min(self.max_drawdown_usd, max_drawdown_usd)
        self.gates = _merge_gates(self.gates, gates)

    @property
    def expectancy(self) -> float | None:
        if self.closed_n <= 0 or not self.net_known:
            return None
        return round(self.net / self.closed_n, 6)

    @property
    def net_value(self) -> float | None:
        if self.closed_n <= 0 or not self.net_known:
            return None
        return round(self.net, 6)


def _observation_from_lifecycle(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if row.get("terminal") is not True:
        return None
    surface = _world_surface(classify_world(row))
    if surface is None:
        return None
    outcome = str(row.get("outcome_state") or "").upper()
    if outcome in _NOT_A_CLOSE:
        return None
    net = _float_or_none(row.get("net_pnl_usd"))
    if net is None and outcome not in _CLOSED_WITHOUT_NET:
        return None
    fills = 1 if row.get("fill_confirmed") or row.get("filled") else 0
    return {
        "surface": surface,
        "policy_id": _policy_id(row),
        "closed_n": 1,
        "net": net,
        "fills": fills,
        "max_drawdown_usd": _float_or_none(row.get("max_drawdown_usd")),
        "gates": row.get("gates") if isinstance(row.get("gates"), Mapping) else None,
    }


def _observation_from_candidate(row: Mapping[str, Any]) -> dict[str, Any] | None:
    world = classify_world(row)
    # The V3 protection screen is ideal-touch diagnostic. Rows that do not
    # name a world still belong on the shadow surface, never on paper.
    if world is None and (
        row.get("sealed_oos_net_usd") is not None or row.get("oos_episodes") is not None
    ):
        world = "IDEAL_TOUCH"
    surface = _world_surface(world)
    if surface is None:
        return None
    closed_n = _int_or_zero(row.get("oos_episodes") or row.get("closed_n"))
    if closed_n <= 0:
        return None
    net = _float_or_none(row.get("sealed_oos_net_usd"))
    if net is None:
        net = _float_or_none(row.get("after_cost_net_usd"))
    fills = _int_or_zero(row.get("oos_fills") or row.get("fills"))
    return {
        "surface": surface,
        "policy_id": _policy_id(row),
        "closed_n": closed_n,
        "net": net,
        "fills": fills,
        "max_drawdown_usd": _float_or_none(row.get("max_drawdown_usd")),
        "gates": row.get("gates") if isinstance(row.get("gates"), Mapping) else None,
    }


def _paper_count_from_outcomes(report: Mapping[str, Any]) -> int:
    states = (report.get("collection") or {}).get("outcome_states") or {}
    if not isinstance(states, Mapping):
        return 0
    return sum(_int_or_zero(states.get(name)) for name in _CLOSED_WITHOUT_NET)


def _evidence_strips(report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    collection = (report or {}).get("collection") or {}
    if not isinstance(collection, Mapping):
        collection = {}
    dispositions = collection.get("decision_dispositions") or {}
    outcomes = collection.get("decision_outcomes") or {}
    blocked = 0
    for source in (dispositions, outcomes):
        if not isinstance(source, Mapping):
            continue
        for key, count in source.items():
            token = str(key or "").upper()
            if any(marker in token for marker in _BLOCKED_TOKENS):
                blocked += _int_or_zero(count)
    strips = (
        ("microstructure", "Microstructure", _int_or_zero(collection.get("market_segments"))),
        ("funnel", "Funnel", _int_or_zero(collection.get("decision_branches"))),
        ("blocked", "Blocked", blocked),
    )
    rendered = []
    for strip_id, label, count in strips:
        rendered.append({
            "id": strip_id,
            "label": label,
            "count": count,
            "role": "EVIDENCE_ONLY",
            "qualification": "NOT_A_STRATEGY_RANK",
            "safe_badge": None,
            "state": "EMPTY" if count <= 0 else "EVIDENCE",
        })
    return rendered


def _qualify(closed_n: int, safe: bool) -> str:
    if safe and closed_n > 0:
        return "QUALIFIED"
    if closed_n <= 0:
        return "EMPTY"
    return "NO_SAFE_QUALIFIED_POLICY"


def _ranked_policy_rows(grouped: Mapping[str, _Accumulator]) -> list[dict[str, Any]]:
    rows = []
    for policy_id, acc in grouped.items():
        safe = bool(
            acc.closed_n > 0
            and acc.net_known
            and gates_all_pass(acc.gates)
        )
        rows.append({
            "policy_id": policy_id,
            "closed_n": acc.closed_n,
            "fills": acc.fills,
            "after_cost_net_usd": acc.net_value,
            "after_cost_expectancy_usd": acc.expectancy,
            "max_drawdown_usd": acc.max_drawdown_usd,
            "qualification": _qualify(acc.closed_n, safe),
            "safe_badge": "SAFE" if safe else None,
            "gates": {name: True for name in REQUIRED_GATES} if safe else None,
            "rank": None,
        })
    scored = [row for row in rows if row["after_cost_expectancy_usd"] is not None]
    unscored = [row for row in rows if row["after_cost_expectancy_usd"] is None]
    scored.sort(key=lambda row: (
        -float(row["after_cost_expectancy_usd"]),
        -int(row["closed_n"]),
        str(row["policy_id"]),
    ))
    unscored.sort(key=lambda row: (-int(row["closed_n"]), str(row["policy_id"])))
    for index, row in enumerate(scored, start=1):
        row["rank"] = index
    return scored + unscored


def _surface_payload(surface: Mapping[str, str], grouped: Mapping[str, _Accumulator]) -> dict[str, Any]:
    rows = _ranked_policy_rows(grouped)
    closed_n = sum(row["closed_n"] for row in rows)
    total_fills = sum(row.get("fills") or 0 for row in rows)
    dds = [row["max_drawdown_usd"] for row in rows if row.get("max_drawdown_usd") is not None]
    worst_dd = min(dds) if dds else None
    nets = [row["after_cost_net_usd"] for row in rows]
    pooled = None
    if closed_n > 0 and rows and all(net is not None for net in nets):
        pooled = round(sum(float(net) for net in nets) / closed_n, 6)
    safe = any(row["safe_badge"] == "SAFE" for row in rows)
    return {
        "id": surface["id"],
        "label": surface["label"],
        "world": surface["world"],
        "closed_n": closed_n,
        "fills": total_fills,
        "after_cost_net_usd": None if pooled is None else round(pooled * closed_n, 6),
        "after_cost_expectancy_usd": pooled,
        "max_drawdown_usd": worst_dd,
        "qualification": _qualify(closed_n, safe),
        "safe_badge": "SAFE" if safe else None,
        "rows": rows,
        "columns": [
            "rank",
            "policy_id",
            "closed_n",
            "fills",
            "after_cost_expectancy_usd",
            "after_cost_net_usd",
            "max_drawdown_usd",
            "qualification",
            "safe_badge",
        ],
    }


def _comparison_rows(by_surface: Mapping[str, Mapping[str, _Accumulator]]) -> list[dict[str, Any]]:
    policy_ids = sorted({
        policy_id
        for grouped in by_surface.values()
        for policy_id in grouped
    })
    rendered = []
    for policy_id in policy_ids:
        worlds = {}
        expectancies = []
        closed_n = 0
        safe = False
        gates = None
        for surface_id in SURFACE_IDS:
            acc = by_surface[surface_id].get(policy_id) or _Accumulator()
            row_safe = bool(acc.closed_n > 0 and acc.net_known and gates_all_pass(acc.gates))
            worlds[surface_id] = {
                "world": WORLD_BY_SURFACE[surface_id],
                "closed_n": acc.closed_n,
                "fills": acc.fills,
                "after_cost_net_usd": acc.net_value,
                "after_cost_expectancy_usd": acc.expectancy,
                "max_drawdown_usd": acc.max_drawdown_usd,
                "qualification": _qualify(acc.closed_n, row_safe),
                "safe_badge": "SAFE" if row_safe else None,
            }
            closed_n += acc.closed_n
            if acc.expectancy is not None:
                expectancies.append(acc.expectancy)
            if row_safe:
                safe = True
                gates = {name: True for name in REQUIRED_GATES}
        rendered.append({
            "policy_id": policy_id,
            "rank": None,
            "closed_n": closed_n,
            "min_after_cost_expectancy_usd": min(expectancies) if expectancies else None,
            "qualification": _qualify(closed_n, safe),
            "safe_badge": "SAFE" if safe else None,
            "gates": gates,
            "worlds": worlds,
        })
    scored = [row for row in rendered if row["min_after_cost_expectancy_usd"] is not None]
    unscored = [row for row in rendered if row["min_after_cost_expectancy_usd"] is None]
    scored.sort(key=lambda row: (
        -float(row["min_after_cost_expectancy_usd"]),
        -int(row["closed_n"]),
        str(row["policy_id"]),
    ))
    unscored.sort(key=lambda row: (-int(row["closed_n"]), str(row["policy_id"])))
    for index, row in enumerate(scored, start=1):
        row["rank"] = index
    return scored + unscored


def _empty_groups() -> dict[str, dict[str, _Accumulator]]:
    return {surface_id: {} for surface_id in SURFACE_IDS}


def _add_observation(groups: dict[str, dict[str, _Accumulator]], observation: Mapping[str, Any]) -> None:
    surface = str(observation.get("surface") or "")
    if surface not in groups:
        return
    policy_id = str(observation.get("policy_id") or "UNATTRIBUTED")
    acc = groups[surface].setdefault(policy_id, _Accumulator())
    acc.add(
        _int_or_zero(observation.get("closed_n")),
        _float_or_none(observation.get("net")),
        observation.get("gates"),
        fills=_int_or_zero(observation.get("fills")),
        max_drawdown_usd=_float_or_none(observation.get("max_drawdown_usd")),
    )


def build_equal_rights_ranking(
    *,
    report: Mapping[str, Any] | None = None,
    lifecycles: Iterable[Mapping[str, Any]] | None = None,
    candidates: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the three-world ranking. Empty worlds stay in the payload."""
    groups = _empty_groups()
    for row in lifecycles or []:
        if isinstance(row, Mapping):
            observation = _observation_from_lifecycle(row)
            if observation:
                _add_observation(groups, observation)
    for row in candidates or []:
        if isinstance(row, Mapping):
            observation = _observation_from_candidate(row)
            if observation:
                _add_observation(groups, observation)
    paper_rows = sum(acc.closed_n for acc in groups["paper"].values())
    if paper_rows <= 0:
        outcome_n = _paper_count_from_outcomes(report or {})
        if outcome_n > 0:
            _add_observation(groups, {
                "surface": "paper",
                "policy_id": "UNATTRIBUTED",
                "closed_n": outcome_n,
                "net": None,
                "gates": None,
            })
    return _payload_from_groups(groups, report)


_WORLD_TAGS = (
    "OBSERVED_PAPER",
    "IDEAL_TOUCH",
    "CONSERVATIVE_BBO",
    "SHADOW_BLOCKED",
    "CF",
    "MISSED",
)

MIN_EPISODES_FOR_ADEQUATE_SAMPLE = 30


def _freshness_age_sec(report: Mapping[str, Any] | None) -> float | None:
    gen = (report or {}).get("generated_at")
    if not gen:
        return None
    try:
        dt = datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except (TypeError, ValueError):
        return None


def _freshness_label(age_sec: float | None) -> str:
    if age_sec is None:
        return "UNKNOWN"
    if age_sec < 3600:
        return "FRESH"
    if age_sec < 86400:
        return "STALE"
    return "FROZEN"


def _secondary_world_columns(report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """SHADOW_BLOCKED, CF, MISSED as evidence-only columns (not strategy ranks)."""
    collection = (report or {}).get("collection") or {}
    outcomes = collection.get("decision_outcomes") or {}
    dispositions = collection.get("decision_dispositions") or {}
    if not isinstance(outcomes, Mapping):
        outcomes = {}
    if not isinstance(dispositions, Mapping):
        dispositions = {}
    shadow_blocked = 0
    cf_count = 0
    missed_count = 0
    for source in (outcomes, dispositions):
        for key, count in source.items():
            token = str(key or "").upper()
            if "SHADOW" in token and ("BLOCK" in token or "REJECT" in token):
                shadow_blocked += _int_or_zero(count)
            if "COUNTERFACTUAL" in token or "CF_" in token:
                cf_count += _int_or_zero(count)
            if "MISS" in token or "TIMEOUT" in token or "EXPIRED" in token:
                missed_count += _int_or_zero(count)
    return [
        {"id": "shadow_blocked", "world": "SHADOW_BLOCKED", "label": "Shadow blocked",
         "closed_n": shadow_blocked, "after_cost_expectancy_usd": None, "fills": 0,
         "max_drawdown_usd": None, "qualification": "NOT_A_STRATEGY_RANK", "safe_badge": None,
         "role": "EVIDENCE_ONLY"},
        {"id": "cf_evidence", "world": "CF", "label": "Counterfactual evidence",
         "closed_n": cf_count, "after_cost_expectancy_usd": None, "fills": 0,
         "max_drawdown_usd": None, "qualification": "NOT_A_STRATEGY_RANK", "safe_badge": None,
         "role": "EVIDENCE_ONLY"},
        {"id": "missed", "world": "MISSED", "label": "Missed / expired",
         "closed_n": missed_count, "after_cost_expectancy_usd": None, "fills": 0,
         "max_drawdown_usd": None, "qualification": "NOT_A_STRATEGY_RANK", "safe_badge": None,
         "role": "EVIDENCE_ONLY"},
    ]


def _exit_leakage_tile(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Summarise exit leakage from the report collection or a co-located file."""
    collection = (report or {}).get("collection") or {}
    outcomes = collection.get("decision_outcomes") or {}
    leakage_count = 0
    for key, count in (outcomes if isinstance(outcomes, Mapping) else {}).items():
        token = str(key or "").upper()
        if "LEAK" in token or "EARLY_EXIT" in token or "PREMATURE" in token:
            leakage_count += _int_or_zero(count)
    return {
        "id": "exit_leakage",
        "label": "Exit leakage",
        "count": leakage_count,
        "role": "EVIDENCE_ONLY",
        "qualification": "NOT_A_STRATEGY_RANK",
        "safe_badge": None,
        "state": "EMPTY" if leakage_count <= 0 else "EVIDENCE",
    }


def _regime_progress(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read regime coverage from the V3 ranking or candidate screen."""
    ranking = (report or {}).get("safe_policy_ranking") or {}
    gates = ranking.get("gates") or {}
    regime_pass = gates.get("regime_coverage_pass")
    candidates = ((report or {}).get("candidate_screen") or {}).get("candidates") or []
    regime_sets: set[str] = set()
    for cand in (candidates if isinstance(candidates, list) else []):
        if isinstance(cand, Mapping):
            for regime in (cand.get("regimes") or []):
                if regime:
                    regime_sets.add(str(regime))
    min_required = 3
    return {
        "regimes_observed": sorted(regime_sets),
        "regime_count": len(regime_sets),
        "min_required": min_required,
        "regime_coverage_pass": bool(regime_pass) if regime_pass is not None else None,
        "progress_pct": round(min(100.0, 100.0 * len(regime_sets) / max(1, min_required)), 1),
    }


def _banners(qualification: str, total_closed: int, report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    banners: list[dict[str, Any]] = []
    if qualification != "QUALIFIED":
        banners.append({
            "id": "NO_SAFE",
            "severity": "warning",
            "text": "NO_SAFE — no strategy passed every safety gate.",
        })
    if 0 < total_closed < MIN_EPISODES_FOR_ADEQUATE_SAMPLE:
        banners.append({
            "id": "SAMPLE_POOR",
            "severity": "warning",
            "text": f"SAMPLE_POOR — only {total_closed} closed episodes; minimum {MIN_EPISODES_FOR_ADEQUATE_SAMPLE} needed.",
        })
    live_allowed = (report or {}).get("live_policy_change_allowed")
    if live_allowed is False:
        banners.append({
            "id": "LIVE_LOCKED",
            "severity": "info",
            "text": "live_policy_change_allowed = false. No live arm.",
        })
    return banners


def _post_fresh_diff(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Epoch, gen hash, and sync timestamp for the post-FRESH diff panel."""
    r = report or {}
    return {
        "epoch_id": r.get("epoch_id"),
        "data_scope": r.get("data_scope"),
        "schema": r.get("schema"),
        "generated_at": r.get("generated_at"),
        "status": r.get("status"),
        "qualification": r.get("qualification"),
    }


def _genome_surface(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Genome 0/N gate progress for the summary."""
    ranking = (report or {}).get("safe_policy_ranking") or {}
    gates = ranking.get("gates") or {}
    required = list(REQUIRED_GATES)
    passed = sum(1 for name in required if gates.get(name) is True) if isinstance(gates, Mapping) else 0
    return {
        "gates_passed": passed,
        "gates_total": len(required),
        "label": f"{passed}/{len(required)}",
        "all_pass": passed == len(required) and len(required) > 0,
        "gates": {name: gates.get(name) if isinstance(gates, Mapping) else None for name in required},
    }


def _frozen_digest(
    report: Mapping[str, Any] | None,
    qualification: str,
    total_closed: int,
) -> dict[str, Any]:
    age_sec = _freshness_age_sec(report)
    return {
        "generated_at": (report or {}).get("generated_at"),
        "freshness": _freshness_label(age_sec),
        "freshness_age_sec": age_sec,
        "world_tags": list(_WORLD_TAGS),
        "banners": _banners(qualification, total_closed, report),
        "secondary_worlds": _secondary_world_columns(report),
        "exit_leakage": _exit_leakage_tile(report),
        "regime_progress": _regime_progress(report),
        "post_fresh_diff": _post_fresh_diff(report),
        "genome": _genome_surface(report),
        "fixed_watch": "ATR_TRAIL + CHANDELIER_3",
        "regime_dynamic": False,
        "live_arm": False,
        "copy": "Fixed watch ATR_TRAIL + CHANDELIER_3. No regime-dynamic. No live arm.",
    }


def _payload_from_groups(
    groups: Mapping[str, Mapping[str, _Accumulator]],
    report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    surfaces = [_surface_payload(surface, groups[surface["id"]]) for surface in SURFACES]
    comparison = _comparison_rows(groups)
    safe_rows = [row for row in comparison if row["safe_badge"] == "SAFE"]
    number_one = safe_rows[0] if safe_rows else None
    any_closed = any(surface["closed_n"] > 0 for surface in surfaces)
    total_closed = sum(s["closed_n"] for s in surfaces)
    if number_one:
        qualification = "QUALIFIED"
        headline = "QUALIFIED — one policy passed every safety gate. Rank remains after-cost expectancy."
        safe_badge = "SAFE"
    elif any_closed:
        qualification = "NO_SAFE_QUALIFIED_POLICY"
        headline = "NO_SAFE — no strategy is crowned. Ranks are after-cost expectancy only."
        safe_badge = None
    else:
        qualification = "NO_SAFE_QUALIFIED_POLICY"
        headline = "NO_SAFE — no closed after-cost evidence in paper, shadow, or counterfactual."
        safe_badge = None
    return {
        "schema": SCHEMA,
        "primary_metric": PRIMARY_METRIC,
        "win_rate_is_rank": False,
        "rank_basis": "MIN_AFTER_COST_EXPECTANCY_ACROSS_WORLDS_WITH_EVIDENCE",
        "qualification": qualification,
        "safe_badge": safe_badge,
        "number_one": number_one,
        "headline": headline,
        "required_gates": list(REQUIRED_GATES),
        "surfaces": surfaces,
        "comparison_rows": comparison,
        "evidence": _evidence_strips(report),
        "digest": _frozen_digest(report, qualification, total_closed),
        "mirror_ready": True,
    }


def _groups_from_payload(payload: Mapping[str, Any]) -> dict[str, dict[str, _Accumulator]]:
    groups = _empty_groups()
    surfaces = payload.get("surfaces") or []
    if not isinstance(surfaces, list):
        return groups
    for surface in surfaces:
        if not isinstance(surface, Mapping):
            continue
        surface_id = str(surface.get("id") or "")
        if surface_id not in groups:
            continue
        for row in surface.get("rows") or []:
            if not isinstance(row, Mapping):
                continue
            _add_observation(groups, {
                "surface": surface_id,
                "policy_id": row.get("policy_id") or "UNATTRIBUTED",
                "closed_n": row.get("closed_n"),
                "net": row.get("after_cost_net_usd"),
                "fills": row.get("fills"),
                "max_drawdown_usd": row.get("max_drawdown_usd"),
                "gates": row.get("gates"),
            })
    return groups


def sanitize_equal_rights(payload: Mapping[str, Any], report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Recompute SAFE from gates so a stale file cannot mint a badge."""
    if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA:
        return build_equal_rights_ranking(report=report)
    return _payload_from_groups(_groups_from_payload(payload), report if report is not None else payload)


def _candidates_from_report(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    screen = report.get("candidate_screen") or {}
    if not isinstance(screen, Mapping):
        return []
    candidates = screen.get("candidates")
    if isinstance(candidates, list) and candidates:
        return [row for row in candidates if isinstance(row, Mapping)]
    descriptive = screen.get("descriptive_top_100")
    if isinstance(descriptive, list):
        return [row for row in descriptive if isinstance(row, Mapping)]
    return []


def equal_rights_from_report(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Prefer a stored view, then derive one. Both paths fail closed on SAFE."""
    report = report if isinstance(report, Mapping) else {}
    embedded = report.get("equal_rights")
    if isinstance(embedded, Mapping) and embedded.get("schema") == SCHEMA:
        return sanitize_equal_rights(embedded, report)
    return build_equal_rights_ranking(
        report=report,
        candidates=_candidates_from_report(report),
    )


EQUAL_RIGHTS_CLIENT_JS = r"""
function renderEqualRights(d) {
  const emptySurfaces = [
    {id:'paper', label:'Paper', world:'OBSERVED_PAPER', qualification:'EMPTY', closed_n:0, after_cost_expectancy_usd:null, safe_badge:null},
    {id:'shadow', label:'Shadow', world:'IDEAL_TOUCH', qualification:'EMPTY', closed_n:0, after_cost_expectancy_usd:null, safe_badge:null},
    {id:'counterfactual', label:'Counterfactual', world:'CONSERVATIVE_BBO', qualification:'EMPTY', closed_n:0, after_cost_expectancy_usd:null, safe_badge:null}
  ];
  if (!d || !Array.isArray(d.surfaces) || d.surfaces.length < 3) {
    d = {
      qualification: 'NO_SAFE_QUALIFIED_POLICY',
      headline: 'NO_SAFE — no closed after-cost evidence in paper, shadow, or counterfactual.',
      surfaces: emptySurfaces,
      comparison_rows: [],
      evidence: [],
      digest: null
    };
  }
  const noSafe = d.qualification !== 'QUALIFIED';
  const money = (v) => (v == null || v === '' || Number.isNaN(Number(v))) ? 'UNAVAILABLE' : ((Number(v) >= 0 ? '+' : '') + Number(v).toFixed(4));
  const intOrUnavail = (v) => (v == null || v === '') ? 'UNAVAILABLE' : String(v);
  const ddFmt = (v) => (v == null || v === '' || Number.isNaN(Number(v))) ? 'UNAVAILABLE' : ('$' + Number(v).toFixed(2));
  const badge = (row) => {
    if (row && row.safe_badge === 'SAFE' && !noSafe)
      return '<span class="er-safe">SAFE</span>';
    return '<span class="er-nosafe">NO_SAFE</span>';
  };
  const dig = d.digest || {};
  const banners = (dig.banners || []).map(b =>
    '<div class="er-banner er-banner-' + (b.severity || 'warning') + '">' + (b.text || '') + '</div>'
  ).join('');
  const freshness = dig.freshness || 'UNKNOWN';
  const freshCls = freshness === 'FRESH' ? 'er-chip-fresh' : (freshness === 'FROZEN' ? 'er-chip-frozen' : 'er-chip-stale');
  const freshChip = '<span class="er-chip ' + freshCls + '">' + freshness + '</span>';
  const worldTags = (dig.world_tags || ['OBSERVED_PAPER','IDEAL_TOUCH','CONSERVATIVE_BBO','SHADOW_BLOCKED','CF','MISSED'])
    .map(t => '<span class="er-tag">' + t + '</span>').join(' ');
  const fixedCopy = dig.copy || 'Fixed watch ATR_TRAIL + CHANDELIER_3. No regime-dynamic. No live arm.';
  const tiles = d.surfaces.map(s => {
    const profitableOnly = (s.after_cost_expectancy_usd != null && Number(s.after_cost_expectancy_usd) > 0);
    const evClass = profitableOnly ? 'er-profitable-hypothesis' : '';
    return '<article class="er-tile">'
    + '<div class="er-kicker">' + (s.label || '') + ' · ' + (s.world || '') + '</div>'
    + '<div class="er-metric"><span>After-cost EV</span><strong class="' + evClass + '">' + money(s.after_cost_expectancy_usd) + '</strong></div>'
    + '<div class="er-sub">n ' + intOrUnavail(s.closed_n) + ' · fills ' + intOrUnavail(s.fills) + ' · DD ' + ddFmt(s.max_drawdown_usd) + '</div>'
    + '<div class="er-sub">' + (s.qualification || 'EMPTY') + ' · ' + badge(s) + '</div>'
    + ((s.closed_n || 0) > 0 ? '' : '<div class="er-empty">EMPTY --- no closed after-cost evidence</div>')
    + '</article>';
  }).join('');
  const cell = (world) => '<td>' + intOrUnavail(world.closed_n) + '</td><td>' + intOrUnavail(world.fills) + '</td><td>' + money(world.after_cost_expectancy_usd) + '</td><td>' + ddFmt(world.max_drawdown_usd) + '</td>';
  const body = (d.comparison_rows || []).map(r => {
    const worlds = r.worlds || {};
    return '<tr><td>' + (r.rank == null ? '---' : r.rank) + '</td><td>' + (r.policy_id || '---') + '</td>'
      + cell(worlds.paper || {}) + cell(worlds.shadow || {}) + cell(worlds.counterfactual || {})
      + '<td>' + badge(r) + ' ' + (r.qualification || 'NO_SAFE_QUALIFIED_POLICY') + '</td></tr>';
  }).join('') || '<tr><td colspan="15">EMPTY --- no closed after-cost evidence in paper, shadow, or counterfactual.</td></tr>';
  const evidenceItems = (d.evidence || []).concat(dig.exit_leakage ? [dig.exit_leakage] : []);
  const evidence = evidenceItems.map(e => (
    '<article class="er-tile"><div class="er-kicker">' + (e.label || '') + '</div>'
    + '<div class="er-sub">n ' + (e.count || 0) + ' · ' + (e.qualification || 'NOT_A_STRATEGY_RANK') + '</div>'
    + '<div class="er-empty">Evidence only. Not a strategy rank and not SAFE.</div></article>'
  )).join('');
  const secondaryWorlds = (dig.secondary_worlds || []).map(sw => (
    '<article class="er-tile"><div class="er-kicker">' + (sw.label || '') + ' · ' + (sw.world || '') + '</div>'
    + '<div class="er-sub">n ' + intOrUnavail(sw.closed_n) + ' · fills ' + intOrUnavail(sw.fills) + ' · DD ' + ddFmt(sw.max_drawdown_usd) + '</div>'
    + '<div class="er-sub">' + (sw.qualification || 'NOT_A_STRATEGY_RANK') + '</div>'
    + '<div class="er-empty">Evidence only. Not a strategy rank.</div></article>'
  )).join('');
  const genome = dig.genome || {};
  const genomeChip = '<span class="er-chip ' + (genome.all_pass ? 'er-chip-fresh' : 'er-chip-frozen') + '">'
    + 'Genome ' + (genome.label || '0/N') + '</span>';
  const postFresh = dig.post_fresh_diff || {};
  const postFreshLine = postFresh.epoch_id
    ? '<span style="font-size:12px;color:#8b949e;margin-left:12px">Epoch ' + postFresh.epoch_id + ' · ' + (postFresh.data_scope || '') + ' · ' + (postFresh.status || '') + '</span>'
    : '';
  const rp = dig.regime_progress || {};
  const regimeBar = '<div class="er-regime">'
    + '<span class="er-regime-label">Regime cells: ' + (rp.regime_count || 0) + ' / ' + (rp.min_required || 3)
    + (rp.regime_coverage_pass === true ? ' <span class="er-safe">PASS</span>' : ' <span class="er-nosafe">PENDING</span>') + '</span>'
    + '<div class="er-regime-bar"><div class="er-regime-fill" style="width:' + Math.min(100, rp.progress_pct || 0) + '%"></div></div>'
    + '</div>';
  const wrSecondary = noSafe ? '<p class="er-sub">Win rate is secondary. After-cost expectancy is the rank.</p>' : '';
  const style = '<style>'
    + '.er-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:12px 0}'
    + '.er-tile{border:1px solid #30363d;background:#161b22;border-radius:8px;padding:12px}'
    + '.er-kicker{color:#8b949e;font-size:12px;letter-spacing:.04em;text-transform:uppercase}'
    + '.er-metric{display:flex;flex-direction:column;gap:4px;margin-top:8px}'
    + '.er-metric span{color:#8b949e;font-size:12px}.er-metric strong{font-size:1.35rem}'
    + '.er-sub,.er-empty,.er-note{color:#8b949e;font-size:13px}'
    + '.er-safe{color:#3fb950;font-weight:700}.er-nosafe{color:#d29922;font-weight:700}'
    + '.er-scroll{overflow-x:auto}'
    + '.er-table{width:100%;border-collapse:collapse}'
    + '.er-table th,.er-table td{border:1px solid #30363d;padding:8px;text-align:right}'
    + '.er-table th:first-child,.er-table td:first-child,.er-table th:nth-child(2),.er-table td:nth-child(2){text-align:left}'
    + '.er-banner{padding:8px 12px;border-radius:6px;margin:6px 0;font-size:13px}'
    + '.er-banner-warning{background:#3d2a1f;border:1px solid #d29922;color:#f8e3a1}'
    + '.er-banner-info{background:#1f2d3d;border:1px solid #58a6ff;color:#c9d1d9}'
    + '.er-chip{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:700;letter-spacing:.04em}'
    + '.er-chip-fresh{background:#1a3a2a;color:#3fb950}.er-chip-stale{background:#3d2a1f;color:#d29922}.er-chip-frozen{background:#3d1f1f;color:#f85149}'
    + '.er-tag{display:inline-block;padding:1px 6px;border:1px solid #30363d;border-radius:4px;font-size:11px;color:#8b949e;margin:2px}'
    + '.er-digest{margin:10px 0;padding:10px 14px;border:1px solid #30363d;border-radius:8px;background:#0d1117}'
    + '.er-profitable-hypothesis{color:#58a6ff}'
    + '.er-regime{margin:8px 0}'
    + '.er-regime-label{font-size:12px;color:#8b949e}'
    + '.er-regime-bar{height:6px;background:#21262d;border-radius:3px;margin-top:4px}'
    + '.er-regime-fill{height:100%;background:#58a6ff;border-radius:3px;transition:width .3s}'
    + '@media(max-width:800px){.er-grid{grid-template-columns:1fr}}'
    + '</style>';
  return style + banners
    + '<div class="er-digest">'
    + '<span style="font-size:12px;color:#8b949e">Freshness </span>' + freshChip
    + ' ' + genomeChip
    + ' <span style="font-size:12px;color:#8b949e;margin-left:12px">Worlds </span>' + worldTags
    + postFreshLine
    + '<div style="margin-top:6px;font-size:12px;color:#8b949e">' + fixedCopy + '</div>'
    + regimeBar
    + '</div>'
    + '<p class="er-note">' + (d.headline || 'NO_SAFE --- no strategy is crowned.') + '</p>'
    + wrSecondary
    + '<div class="er-grid">' + tiles + '</div>'
    + '<div class="er-scroll"><table class="er-table"><thead><tr>'
    + '<th>Rank</th><th>Policy</th>'
    + '<th>Paper n</th><th>Paper fills</th><th>Paper after-cost EV</th><th>Paper DD</th>'
    + '<th>Shadow n</th><th>Shadow fills</th><th>Shadow after-cost EV</th><th>Shadow DD</th>'
    + '<th>CF n</th><th>CF fills</th><th>CF after-cost EV</th><th>CF DD</th>'
    + '<th>Qualification</th></tr></thead><tbody>' + body + '</tbody></table></div>'
    + '<h3>Secondary worlds (evidence only)</h3><div class="er-grid">' + secondaryWorlds + '</div>'
    + '<h3>Evidence (not strategy ranks)</h3><div class="er-grid">' + evidence + '</div>';
}
async function loadEqualRights() {
  const roots = ['equal-rights-root', 'equalRightsRoot']
    .map(id => document.getElementById(id))
    .filter(Boolean);
  if (!roots.length) return;
  let payload = null;
  try {
    const response = await fetch('/api/equal-rights-ranking', {cache: 'no-store'});
    if (response.ok) payload = await response.json();
  } catch (err) {
    payload = null;
  }
  const html = renderEqualRights(payload);
  roots.forEach(node => { node.innerHTML = html; });
}
"""
