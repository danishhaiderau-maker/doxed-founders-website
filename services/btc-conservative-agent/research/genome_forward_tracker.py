"""Forward-test tracker: freeze mix-and-match candidates, then score them only on episodes signalled after the freeze.

Research only (SIMULATED_COUNTERFACTUAL, REALISTIC_V1 counterfactual replay on the 1 s tape). Each analyzer cycle:

1. ``freeze_batch`` appends one batch per UTC day to ``frozen_candidates.jsonl``: the top in-sample combos (labelled
   IN_SAMPLE_TOP), each nested-procedure's full-data pick, every per-regime winner and every regime-switching
   meta-policy, per cohort. Each line carries its exact rule, a rule hash, the freeze timestamp, the verdict rules and
   ``prev_sha`` (sha256 of the previous line), so the file is an append-only hash chain. Frozen lines are never edited.
2. ``score`` evaluates every frozen candidate on episodes whose signal is strictly after its freeze time, using the
   current genome outcome matrix, and applies the pre-declared FORWARD_RULES_V1 verdict.

FORWARD_CONFIRMED candidates are flagged as paper-tile proposals (draft registry PR only); nothing is deployed.

Registered paper tiles can also be frozen into the chain (``freeze_registry_tiles``, kind ``REGISTRY_TILE``) under an
explicit batch id. Their exact registry rule is hashed here, but they are scored by their own pre-registration in
``tile_paired_comparison_report.json``, never by the genome grid.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from research import genome_mix_match as mm

SCHEMA = "genome_forward_tracker_v1"
FROZEN_FILE = "frozen_candidates.jsonl"
SCORES_FILE = "forward_scores.json"
FORWARD_RULES = {
    "id": "FORWARD_RULES_V1",
    "min_fills": 30,
    "min_effective_trades": 30,
    "alpha": 0.05,
    "multiplicity": "Bonferroni within the candidate's freeze batch: one-sided alpha / batch_size",
    "FORWARD_FAILED": "fills >= min_fills and upper 95% cluster CI < 0; or adequately sampled and forward EV <= 0",
    "FORWARD_CONFIRMED": "fills >= min_fills, n_eff >= min_effective_trades, forward EV > 0 and the one-sided "
                         "cluster-bootstrap lower bound at alpha / batch_size > 0",
    "INSUFFICIENT": "otherwise (too few forward trades, or positive but not yet significant)",
    "scope": "episodes signalled strictly after frozen_at; counterfactual fills, no capacity limit",
}
RULES_SHA = hashlib.sha256(json.dumps(FORWARD_RULES, sort_keys=True).encode()).hexdigest()[:16]
MAX_PER_COHORT = 60
REGISTRY_TILE_KIND = "REGISTRY_TILE"
REGISTRY_TILE_COHORT = "REGISTRY_TILES"
REGISTRY_TILE_VERDICT = "SCORED_BY_TILE_PRE_REGISTRATION"
REGISTRY_TILE_VERDICT_SOURCE = "tile_paired_comparison_report.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rule_sha(rule: Mapping[str, Any]) -> str:
    return _sha(json.dumps(rule, sort_keys=True, default=str))[:16]


def load_frozen(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = Path(root) / FROZEN_FILE
    rows, prev, broken = [], "GENESIS", []
    if path.exists():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("prev_sha") != prev:
                broken.append(i)
            prev = _sha(line)
            rows.append(row)
    return rows, {"lines": len(rows), "chain_ok": not broken, "broken_at_lines": broken[:20], "head_sha": prev}


def _base_filters(rule: Mapping[str, Any]) -> list[list[str]]:
    base = (rule or {}).get("base")
    extra = [["ai_class", "COMMITTED"]] if base == "COMMITTED_ONLY" else \
        [["ai_class", "NO_TRADE_SCORE_LED"]] if base == "NO_TRADE_SCORE_LED_ONLY" else []
    return extra + [list(f) for f in (rule or {}).get("filters") or []]


def candidates_from(mix: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Candidate rules from one cohort's mix-and-match result (labels say how each was chosen)."""
    cohort = mix.get("cohort")
    out: list[dict[str, Any]] = []
    for c in mix.get("in_sample_top") or []:
        out.append({"label": "IN_SAMPLE_TOP", "kind": "POLICY_FILTER", "cohort": cohort,
                    "rule": {"policy_id": c["policy_id"], "filters": c["filters"]},
                    "at_freeze": {"in_sample": c.get("in_sample")}})
    for s in mix.get("structures") or []:
        fr = s.get("full_data_rule")
        if fr:
            out.append({"label": "NESTED_PROCEDURE_PICK", "kind": "POLICY_FILTER", "cohort": cohort, "procedure": s["id"],
                        "rule": {"policy_id": fr["policy_id"], "filters": _base_filters(fr)},
                        "at_freeze": {"in_sample": s.get("full_data_in_sample"), "nested_oos": s.get("nested_oos"),
                                      "nested_oos_fine": s.get("nested_oos_fine")}})
    for w in mix.get("regime_winners") or []:
        out.append({"label": "REGIME_WINNER", "kind": "POLICY_FILTER", "cohort": cohort,
                    "rule": {"policy_id": w["policy_id"], "filters": [], "regime": w["regime"]},
                    "at_freeze": {"insample_ev_bp": w.get("insample_ev_bp"), "insample_fills": w.get("insample_fills")}})
    for m in mix.get("meta_policies") or []:
        mapping = {k: (v or {}).get("policy_id") for k, v in (m.get("full_data_mapping") or {}).items()}
        if any(mapping.values()):
            out.append({"label": "META_POLICY", "kind": "META", "cohort": cohort, "procedure": m["id"],
                        "rule": {"regime": m["regime"], "mapping": mapping},
                        "at_freeze": {"in_sample": m.get("full_data_in_sample"), "nested_oos": m.get("nested_oos")}})
    seen, uniq = set(), []
    for c in out:
        h = rule_sha({"cohort": cohort, "kind": c["kind"], "rule": c["rule"]})
        if h not in seen:
            seen.add(h)
            uniq.append(c | {"rule_sha": h})
    return uniq[:MAX_PER_COHORT]


def registry_tile_candidates(registry: Mapping[str, Mapping[str, Any]], lanes: Iterable[str]) -> list[dict[str, Any]]:
    """Exact registry rules of registered paper tiles, as forward-tracker candidates."""
    out = []
    for lane in lanes:
        spec = registry[lane]
        rule = {"lane": lane, "raw_policy_id": spec["raw_policy_id"], "policy_signature": spec["policy_signature"],
                "policy_epoch": spec.get("policy_epoch"), "entry_policy": spec.get("entry_policy"),
                "exit_policy": spec.get("exit_policy")}
        out.append({"label": "PRE_REGISTERED_TILE", "kind": REGISTRY_TILE_KIND, "cohort": REGISTRY_TILE_COHORT,
                    "procedure": (spec.get("pre_registration") or {}).get("hypothesis_id"), "rule": rule,
                    "rule_sha": rule_sha(rule),
                    "at_freeze": {"honest_label": (spec.get("pre_registration") or {}).get("honest_label")}})
    return out


def freeze_registry_tiles(root: Path, registry: Mapping[str, Mapping[str, Any]], lanes: Iterable[str], *,
                          batch_id: str, now: float | None = None, code_revision: str | None = None) -> dict[str, Any]:
    """Append one named batch of registered tiles to the hash chain (once per batch id)."""
    return freeze_batch(root, registry_tile_candidates(registry, lanes), now=now, batch_id=batch_id,
                        code_revision=code_revision)


def freeze_batch(root: Path, candidates: list[Mapping[str, Any]], *, now: float | None = None,
                 generation: str | None = None, code_revision: str | None = None,
                 batch_id: str | None = None) -> dict[str, Any]:
    """Append one batch (once per UTC day, or once per explicit ``batch_id``). Never rewrites existing lines."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    now = float(now if now is not None else time.time())
    batch_id = batch_id or time.strftime("%Y-%m-%d", time.gmtime(now))
    rows, chain = load_frozen(root)
    if not chain["chain_ok"]:
        return {"status": "REFUSED_CHAIN_BROKEN", "batch_id": batch_id, "chain": chain}
    if any(r.get("batch_id") == batch_id for r in rows):
        return {"status": "ALREADY_FROZEN_TODAY", "batch_id": batch_id, "frozen": 0}
    if not candidates:
        return {"status": "NO_CANDIDATES", "batch_id": batch_id, "frozen": 0}
    prev = chain["head_sha"]
    lines = []
    for c in candidates:
        rec = {"schema": SCHEMA, "batch_id": batch_id, "batch_size": len(candidates),
               "candidate_id": f"{batch_id}:{c['cohort']}:{c['rule_sha']}", "frozen_at": now,
               "frozen_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
               "label": c["label"], "kind": c["kind"], "cohort": c["cohort"], "procedure": c.get("procedure"),
               "rule": c["rule"], "rule_sha": c["rule_sha"], "at_freeze": c.get("at_freeze"),
               "forward_rules": FORWARD_RULES["id"], "forward_rules_sha": RULES_SHA,
               "filter_defs_sha": mm.FILTER_DEFS_SHA, "generation": generation, "code_revision": code_revision,
               "prev_sha": prev}
        line = json.dumps(rec, sort_keys=True, default=str, separators=(",", ":"))
        prev = _sha(line)
        lines.append(line)
    with open(root / FROZEN_FILE, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return {"status": "FROZEN", "batch_id": batch_id, "frozen": len(lines)}


def verdict(sm: Mapping[str, Any], lower_bonf_bp: float | None) -> str:
    n, neff, ev, ci = sm.get("fills") or 0, sm.get("n_eff") or 0, sm.get("ev_bp"), sm.get("ci_bp") or [None, None]
    if n >= FORWARD_RULES["min_fills"] and ci[1] is not None and ci[1] < 0:
        return "FORWARD_FAILED"
    if n < FORWARD_RULES["min_fills"] or neff < FORWARD_RULES["min_effective_trades"]:
        return "INSUFFICIENT"
    if ev is not None and ev > 0 and lower_bonf_bp is not None and lower_bonf_bp > 0:
        return "FORWARD_CONFIRMED"
    if ev is None or ev <= 0:
        return "FORWARD_FAILED"
    return "INSUFFICIENT"


def _candidate_pnl(g: "mm.Grid", rec: Mapping[str, Any], fwd: np.ndarray) -> tuple[np.ndarray, np.ndarray] | str:
    rule = rec["rule"]
    if rec["kind"] == "META":
        fn = mm.REGIMES.get(rule.get("regime"))
        if fn is None:
            return "UNSCORABLE_REGIME_UNKNOWN"
        labels = np.array([fn(c) or "" for c in g.ctx], dtype=object)
        pnl, ts = [], []
        for cell, pid in (rule.get("mapping") or {}).items():
            if not pid:
                continue
            if pid not in g.index:
                return "UNSCORABLE_POLICY_NOT_IN_GRID"
            p, t = g.apply(fwd & (labels == cell), g.index[pid])
            pnl.extend(p.tolist())
            ts.extend(t.tolist())
        return np.asarray(pnl), np.asarray(ts)
    k = g.index.get(rule.get("policy_id"))
    if k is None:
        return "UNSCORABLE_POLICY_NOT_IN_GRID"
    m = mm.rule_mask(g.ctx, rule.get("filters") or [], rule.get("regime"))
    if m is None:
        return "UNSCORABLE_FILTER_UNKNOWN"
    return g.apply(fwd & m, k)


def score(root: Path, grids: Mapping[str, "mm.Grid"], *, now: float | None = None) -> dict[str, Any]:
    rows, chain = load_frozen(root)
    now = float(now if now is not None else time.time())
    out = []
    for rec in rows:
        g = grids.get(rec.get("cohort"))
        base = {k: rec.get(k) for k in ("candidate_id", "batch_id", "batch_size", "frozen_at_utc", "label", "kind",
                                        "cohort", "procedure", "rule", "rule_sha", "at_freeze")}
        if rec.get("kind") == REGISTRY_TILE_KIND:
            out.append(base | {"status": REGISTRY_TILE_VERDICT, "verdict": REGISTRY_TILE_VERDICT,
                               "verdict_source": REGISTRY_TILE_VERDICT_SOURCE})
            continue
        if g is None:
            out.append(base | {"status": "UNSCORABLE_COHORT_ABSENT", "verdict": "INSUFFICIENT"})
            continue
        fwd = g.ts > float(rec["frozen_at"])
        res = _candidate_pnl(g, rec, fwd)
        if isinstance(res, str):
            out.append(base | {"status": res, "verdict": "INSUFFICIENT", "forward": mm.summarize([], [])})
            continue
        p, t = res
        span = max(0.0, (float(g.ts.max()) - float(rec["frozen_at"])) / mm.DAY_SEC) if len(g.ts) else 0.0
        sm = mm.summarize(p, t, span or None, g.cluster_sec)
        alpha = FORWARD_RULES["alpha"] / max(1, int(rec.get("batch_size") or 1))
        low = mm.cluster_ci(p, t, alpha=alpha, cluster_sec=g.cluster_sec).get("lower_one_sided_bp") if len(p) > 1 else None
        v = verdict(sm, low)
        out.append(base | {"status": "SCORED", "forward": sm, "forward_episodes_seen": int(fwd.sum()),
                           "bonferroni_alpha": round(alpha, 6), "lower_bound_bonferroni_bp": low, "verdict": v,
                           "tile_proposal": v == "FORWARD_CONFIRMED"})
    out.sort(key=lambda r: (-((r.get("forward") or {}).get("net_pnl_usd") or 0.0), r["candidate_id"]))
    counts: dict[str, int] = {}
    for r in out:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    doc = {"schema": SCHEMA, "scored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "rules": FORWARD_RULES,
           "rules_sha": RULES_SHA, "chain": chain, "candidates": len(out), "verdicts": counts,
           "batches": sorted({r["batch_id"] for r in out}),
           "proposals": [r["candidate_id"] for r in out if r.get("tile_proposal")],
           "proposal_policy": "FORWARD_CONFIRMED -> flag for a draft post-freeze paper-only, relay-ineligible registry PR; "
                              "never auto-deployed", "rows": out}
    tmp = Path(root) / (SCORES_FILE + ".tmp")
    tmp.write_text(json.dumps(doc, default=str), encoding="utf-8")
    os.replace(tmp, Path(root) / SCORES_FILE)
    return doc