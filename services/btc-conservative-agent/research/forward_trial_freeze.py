"""Forward-trial freeze protocol and 15-day tracker.

A tile qualifies only when, in the current generation, its after-cost EV has
n >= 30 strategy exits with a 95% CI entirely above zero (evidence-points EV
ranking), both chronological halves are positive (selector report), it has a
single observed policy signature, and the analyzer registry signature equals
the runtime one. With no qualifying tile the protocol refuses to freeze and
says why; it never invents a candidate.

Freezing writes one write-once manifest (candidate + control identities,
parameters, evidence snapshot, registry signature), content-hashed and, when
``FORWARD_TRIAL_SIGNING_KEY`` is set, HMAC-signed. It is a research record:
no runtime, toggle or relay code reads it, both tiles stay paper-only and
relay-ineligible, and the tracker fails the trial on identity drift.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.tile_evidence_points import (
    FORCED_EXIT_REASONS,
    MIN_RANK_SAMPLE,
    _epoch_seconds,
    _iso,
    _summary,
    _upper,
    classify_trade_rows,
    exact_net_pnl,
)
from research_v3_contract import canonical_hash, canonical_json

REPORT_FILE = "forward_trial_report.json"
REPORT_SCHEMA = "forward_trial_v1"
MANIFEST_SCHEMA = "forward_trial_freeze_manifest_v1"
TRIAL_DAYS = 15
DEFAULT_TRIAL_DIR = r"C:\DoxxedCrypto\forward-trial"
SIGNING_KEY_ENV = "FORWARD_TRIAL_SIGNING_KEY"
FROZEN_TILE_FIELDS = ("label", "raw_policy_id", "policy_signature", "policy_epoch", "admission_treatment",
                      "entry_policy", "exit_policy", "ladder", "requested_margin_usd", "risk_limits",
                      "entry_ttl_sec", "path_end_sec")


def trial_dir() -> Path:
    path = Path(os.environ.get("FORWARD_TRIAL_DIR") or DEFAULT_TRIAL_DIR)
    if "onedrive" in str(path).lower():
        raise ValueError(f"FORWARD_TRIAL_DIR_UNDER_ONEDRIVE:{path}")
    return path


def qualify_candidates(*, evidence: Mapping[str, Any] | None, selector: Mapping[str, Any] | None,
                       tile_order: Iterable[str], registry_signature: str | None,
                       runtime_registry_signature: str | None) -> list[dict[str, Any]]:
    """Per-tile freeze gates; a tile qualifies only when every gate passes."""
    ranking = {str(r.get("lane")): r for r in ((evidence or {}).get("ev_ranking") or {}).get("rows") or []}
    sel_tiles = (selector or {}).get("tiles") or {}
    same_epoch = bool(evidence and selector and evidence.get("epoch_id") == selector.get("epoch_id"))
    parity = bool(registry_signature and registry_signature == runtime_registry_signature)
    out = []
    for lane in tile_order:
        row, sel = ranking.get(lane) or {}, sel_tiles.get(lane) or {}
        n = int(row.get("n") or 0)
        ci = row.get("ci95_usd") or None
        halves = (sel.get("chronological_halves") or {}).get("status")
        gates = {
            "reports_current_same_epoch": same_epoch,
            "n_at_least_30": n >= MIN_RANK_SAMPLE,
            "ev_ci95_above_zero": bool(ci and ci[0] > 0),
            "oos_consistent_positive": sel.get("oos_consistent_positive") is True,
            "single_policy_signature": sel.get("identity_single_signature") is True,
            "registry_runtime_parity": parity,
        }
        out.append({
            "lane": lane, "n": n, "mean_usd": row.get("mean_usd"), "ci95_usd": ci,
            "chronological_halves": halves, "gates": gates,
            "failed_gates": [g for g, ok in gates.items() if not ok],
            "qualifies": all(gates.values()),
        })
    return out


def choose_control(candidate: str, evidence: Mapping[str, Any], tile_order: list[str]) -> tuple[str | None, str]:
    rows = [r for r in (evidence.get("ev_ranking") or {}).get("rows") or [] if r.get("lane") != candidate]
    ranked = sorted((r for r in rows if r.get("status") == "RANKED"), key=lambda r: r.get("mean_usd") or 0,
                    reverse=True)
    if ranked:
        return ranked[0]["lane"], "best-ranked other registered tile (n>=30)"
    others = sorted(rows, key=lambda r: (-(r.get("n") or 0), tile_order.index(r["lane"]) if r.get("lane") in tile_order else 99))
    if others:
        return others[0]["lane"], "other registered tile with the most strategy exits (none ranked)"
    return None, "no other registered tile"


def _tile_identity(registry: Mapping[str, Any], lane: str, observed: Mapping[str, Any]) -> dict[str, Any]:
    spec = registry.get(lane) or {}
    identity = {"lane": lane, **{k: spec.get(k) for k in FROZEN_TILE_FIELDS}}
    identity["ladder"] = [list(r) for r in (identity.get("ladder") or ())]
    identity["observed_policy_signatures"] = dict(observed.get("observed_policy_signatures") or {})
    identity["paper_only"] = spec.get("paper_only") is True
    identity["relay_eligible"] = bool(spec.get("platform_relay_eligible"))
    return identity


def _signature(body: Mapping[str, Any], key: bytes | None) -> dict[str, str]:
    if key:
        return {"kind": "HMAC_SHA256", "value": hmac.new(key, canonical_json(body).encode("utf-8"), hashlib.sha256).hexdigest()}
    return {"kind": "SHA256_CONTENT", "value": hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()}


def build_freeze_manifest(*, candidate: Mapping[str, Any], control_lane: str, control_reason: str,
                          registry: Mapping[str, Any], selector: Mapping[str, Any], evidence: Mapping[str, Any],
                          registry_signature: str, analyzer_revision: str | None, frozen_at: float,
                          signing_key: bytes | None = None) -> dict[str, Any]:
    lane = candidate["lane"]
    sel_tiles = selector.get("tiles") or {}
    body = {
        "schema": MANIFEST_SCHEMA,
        "frozen_at": _iso(frozen_at),
        "trial_ends_at": _iso(frozen_at + TRIAL_DAYS * 86400),
        "trial_days": TRIAL_DAYS,
        "epoch_id": evidence.get("epoch_id"),
        "registry_signature": registry_signature,
        "analyzer_revision": analyzer_revision,
        "candidate": {**_tile_identity(registry, lane, sel_tiles.get(lane) or {}),
                      "evidence": {k: candidate.get(k) for k in ("n", "mean_usd", "ci95_usd", "chronological_halves")}},
        "control": {**_tile_identity(registry, control_lane, sel_tiles.get(control_lane) or {}),
                    "selection_reason": control_reason},
        "gates_at_freeze": candidate.get("gates"),
        "runtime_effect": "NONE",
        "relay_eligibility_change": "NONE",
        "live_policy_change_allowed": False,
    }
    return {**body, "manifest_id": canonical_hash("forward-trial-freeze", body),
            "content_sha256": hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest(),
            "signature": _signature(body, signing_key)}


def verify_manifest(manifest: Mapping[str, Any], signing_key: bytes | None = None) -> list[str]:
    problems = []
    body = {k: v for k, v in manifest.items() if k not in {"manifest_id", "content_sha256", "signature"}}
    if manifest.get("schema") != MANIFEST_SCHEMA:
        problems.append("SCHEMA")
    if manifest.get("content_sha256") != hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest():
        problems.append("CONTENT_SHA256_MISMATCH")
    if manifest.get("manifest_id") != canonical_hash("forward-trial-freeze", body):
        problems.append("MANIFEST_ID_MISMATCH")
    sig = manifest.get("signature") or {}
    if sig.get("kind") == "HMAC_SHA256":
        if not signing_key:
            problems.append("HMAC_KEY_UNAVAILABLE")
        elif not hmac.compare_digest(str(sig.get("value")), _signature(body, signing_key)["value"]):
            problems.append("HMAC_MISMATCH")
    if manifest.get("runtime_effect") != "NONE" or manifest.get("live_policy_change_allowed") is not False:
        problems.append("RUNTIME_EFFECT_NOT_NONE")
    for role in ("candidate", "control"):
        tile = manifest.get(role) or {}
        if tile.get("relay_eligible") is not False or tile.get("paper_only") is not True:
            problems.append(f"{role.upper()}_NOT_PAPER_ONLY_RELAY_INELIGIBLE")
    return problems


def write_manifest(directory: Path, manifest: Mapping[str, Any]) -> Path:
    """Write-once; refuses while another trial is active."""
    if load_active_manifest(directory)[0] is not None:
        raise ValueError("FORWARD_TRIAL_ALREADY_ACTIVE")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"freeze-{manifest['manifest_id']}.json"
    payload = (canonical_json(dict(manifest)) + "\n").encode("utf-8")
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o444)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return target


def load_active_manifest(directory: Path, now: float | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    """Most recent manifest; a completed one stays readable for the report."""
    if not directory.is_dir():
        return None, None
    best = (None, None, -1.0)
    for path in directory.glob("freeze-*.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        frozen = _epoch_seconds(manifest.get("frozen_at")) or 0.0
        if frozen > best[2]:
            best = (manifest, path, frozen)
    return best[0], best[1]


def _post_freeze_closes(trades: Iterable[Mapping[str, Any]], *, lanes: set[str], epoch_id: str | None,
                        frozen_at: float) -> tuple[dict[str, list[dict[str, Any]]], dict[str, set[str]]]:
    current, _q = classify_trade_rows(trades, tiles=lanes, epoch_id=epoch_id, v2_start_ts=frozen_at)
    closes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sigs: dict[str, set[str]] = defaultdict(set)
    for row in current:
        lane = _upper(row.get("research_lane"))
        decision = _epoch_seconds(row.get("shared_ai_call_ts"))
        if decision is None or decision < frozen_at:
            continue
        sigs[lane].add(str(row.get("policy_signature") or "MISSING"))
        if _upper(row.get("exit_reason") or row.get("outcome_exit_reason")) in FORCED_EXIT_REASONS:
            continue
        pnl, _basis = exact_net_pnl(row)
        closed = _epoch_seconds(row.get("close_ts")) or _epoch_seconds(row.get("ts"))
        if pnl is not None and closed is not None:
            closes[lane].append({"pnl": pnl, "closed": closed})
    return closes, sigs


def track_trial(manifest: Mapping[str, Any], *, trades: Iterable[Mapping[str, Any]], registry: Mapping[str, Any],
                registry_signature: str | None, now: float, signing_key: bytes | None = None) -> dict[str, Any]:
    frozen = _epoch_seconds(manifest.get("frozen_at"))
    ends = _epoch_seconds(manifest.get("trial_ends_at"))
    cand, ctrl = manifest["candidate"]["lane"], manifest["control"]["lane"]
    closes, sigs = _post_freeze_closes(trades, lanes={cand, ctrl}, epoch_id=manifest.get("epoch_id"), frozen_at=frozen)
    drift = list(verify_manifest(manifest, signing_key))
    if registry_signature != manifest.get("registry_signature"):
        drift.append("REGISTRY_SIGNATURE_CHANGED")
    for role, lane in (("candidate", cand), ("control", ctrl)):
        frozen_tile = manifest[role]
        spec = registry.get(lane)
        if spec is None:
            drift.append(f"{role.upper()}_NOT_REGISTERED")
            continue
        if spec.get("policy_signature") != frozen_tile.get("policy_signature"):
            drift.append(f"{role.upper()}_POLICY_SIGNATURE_CHANGED")
        if spec.get("paper_only") is not True or spec.get("platform_relay_eligible"):
            drift.append(f"{role.upper()}_NO_LONGER_PAPER_ONLY_RELAY_INELIGIBLE")
        unexpected = sigs.get(lane, set()) - {frozen_tile.get("policy_signature")}
        if unexpected:
            drift.append(f"{role.upper()}_UNFROZEN_SIGNATURE_IN_TRADES")
    day_count = max(0, min(TRIAL_DAYS, int((min(now, ends) - frozen) // 86400) + (1 if now > frozen else 0)))
    daily = []
    for day in range(day_count):
        start, stop = frozen + day * 86400, frozen + (day + 1) * 86400
        c = [x["pnl"] for x in closes.get(cand, []) if start <= x["closed"] < stop]
        k = [x["pnl"] for x in closes.get(ctrl, []) if start <= x["closed"] < stop]
        cs, ks = _summary(c), _summary(k)
        daily.append({"day": day + 1, "from": _iso(start), "to": _iso(stop), "candidate": cs, "control": ks,
                      "ev_diff_usd": (round(cs["mean_usd"] - ks["mean_usd"], 6)
                                      if cs["mean_usd"] is not None and ks["mean_usd"] is not None else None)})
    cum_c = _summary([x["pnl"] for x in closes.get(cand, [])])
    cum_k = _summary([x["pnl"] for x in closes.get(ctrl, [])])
    complete = now >= ends
    if drift:
        status = "INVALIDATED_IDENTITY_DRIFT"
    elif not complete:
        status = "TRIAL_ACTIVE"
    elif cum_c["n"] < MIN_RANK_SAMPLE:
        status = "COMPLETE_NOT_ENOUGH_DATA"
    elif cum_c["ci95_usd"] and cum_c["ci95_usd"][0] > 0 and (cum_k["mean_usd"] is None or cum_c["mean_usd"] > cum_k["mean_usd"]):
        status = "COMPLETE_CANDIDATE_HELD"
    else:
        status = "COMPLETE_CANDIDATE_DID_NOT_HOLD"
    return {"status": status, "day": day_count, "trial_days": TRIAL_DAYS, "drift": sorted(set(drift)),
            "cumulative": {"candidate": cum_c, "control": cum_k}, "daily": daily}


def build_forward_trial_report(*, evidence: Mapping[str, Any] | None, selector: Mapping[str, Any] | None,
                               registry: Mapping[str, Any], tile_order: Iterable[str],
                               trades: Iterable[Mapping[str, Any]], directory: Path,
                               registry_signature: str | None, runtime_registry_signature: str | None,
                               analyzer_revision: str | None, now: float | None = None,
                               auto_freeze: bool = True, signing_key: bytes | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc).timestamp()
    order = [str(x).upper() for x in tile_order]
    trades = list(trades or ())
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA, "generated_at": _iso(now), "epoch_id": (evidence or {}).get("epoch_id"),
        "trial_dir": str(directory), "registry_signature": registry_signature,
        "runtime_registry_signature": runtime_registry_signature,
        "live_policy_change_allowed": False, "runtime_effect": "NONE",
        "protocol": {"min_sample": MIN_RANK_SAMPLE, "trial_days": TRIAL_DAYS,
                     "gates": ["reports_current_same_epoch", "n_at_least_30", "ev_ci95_above_zero",
                               "oos_consistent_positive", "single_policy_signature", "registry_runtime_parity"]},
    }
    manifest, path = load_active_manifest(directory)
    if manifest is None:
        candidates = qualify_candidates(evidence=evidence, selector=selector, tile_order=order,
                                        registry_signature=registry_signature,
                                        runtime_registry_signature=runtime_registry_signature)
        report["candidates"] = candidates
        qualified = sorted((c for c in candidates if c["qualifies"]),
                           key=lambda c: (c["ci95_usd"][0], c["mean_usd"]), reverse=True)
        if not qualified:
            report["status"] = "NO_QUALIFYING_CANDIDATE"
            report["status_text"] = "Freeze refused: no tile passes every gate (" + "; ".join(
                f"{c['lane']}: {', '.join(c['failed_gates'])}" for c in candidates) + ")"
            return report
        control, reason = choose_control(qualified[0]["lane"], evidence or {}, order)
        if control is None:
            report["status"] = "NO_CONTROL_AVAILABLE"
            report["status_text"] = "Freeze refused: " + reason
            return report
        manifest = build_freeze_manifest(
            candidate=qualified[0], control_lane=control, control_reason=reason, registry=registry,
            selector=selector or {}, evidence=evidence or {}, registry_signature=str(registry_signature),
            analyzer_revision=analyzer_revision, frozen_at=now, signing_key=signing_key)
        if not auto_freeze:
            report["status"] = "ELIGIBLE_TO_FREEZE"
            report["status_text"] = f"{qualified[0]['lane']} qualifies; control {control}"
            report["proposed_manifest"] = manifest
            return report
        path = write_manifest(directory, manifest)
    report["manifest"] = manifest
    report["manifest_path"] = str(path)
    report["tracker"] = track_trial(manifest, trades=trades, registry=registry,
                                    registry_signature=registry_signature, now=now, signing_key=signing_key)
    report["status"] = report["tracker"]["status"]
    report["status_text"] = (f"Frozen {manifest['candidate']['lane']} vs control {manifest['control']['lane']} "
                             f"at {manifest['frozen_at']}; day {report['tracker']['day']}/{TRIAL_DAYS}; "
                             f"{report['tracker']['status']}")
    return report


def runtime_registry_signature(snapshot_path: str | os.PathLike[str], now: float | None = None,
                               max_age_sec: float = 3600.0) -> str | None:
    """Registry signature the Fly runtime reported, from the laptop's read-only snapshot."""
    try:
        snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    observed = _epoch_seconds(snapshot.get("observedAt"))
    now = now or datetime.now(timezone.utc).timestamp()
    if snapshot.get("ok") is not True or observed is None or now - observed > max_age_sec:
        return None
    return snapshot.get("tile_registry_signature") or None


def signing_key_from_env() -> bytes | None:
    value = os.environ.get(SIGNING_KEY_ENV)
    return value.encode("utf-8") if value else None


def main(argv: list[str] | None = None) -> int:
    """Evaluate the freeze gates from a published generation; --freeze writes the manifest if one qualifies."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--report-dir", required=True, help="analyzer report directory of the current generation")
    parser.add_argument("--data-dir", required=True, help="canonical research data directory (trades CSV)")
    parser.add_argument("--runtime-snapshot", default=r"C:\DoxxedCrypto\laptop-chain\fly_runtime_snapshot_v1.json")
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args(argv)
    from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY, active_tile_registry_signature
    from research.fixed_vs_dynamic_selector import REPORT_FILE as SELECTOR_FILE
    from research.tile_evidence_points import REPORT_FILE as EVIDENCE_FILE, _read_csv

    def load(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    report = build_forward_trial_report(
        evidence=load(Path(args.report_dir) / EVIDENCE_FILE), selector=load(Path(args.report_dir) / SELECTOR_FILE),
        registry=ACTIVE_TILE_REGISTRY, tile_order=ACTIVE_TILE_ORDER,
        trades=_read_csv(str(Path(args.data_dir) / "trades_3factor.csv")), directory=trial_dir(),
        registry_signature=active_tile_registry_signature(),
        runtime_registry_signature=runtime_registry_signature(args.runtime_snapshot), analyzer_revision=None,
        auto_freeze=args.freeze, signing_key=signing_key_from_env())
    print(json.dumps({k: report.get(k) for k in ("status", "status_text", "manifest_path")}, indent=2))
    return 0 if report["status"] not in {"NO_QUALIFYING_CANDIDATE", "NO_CONTROL_AVAILABLE"} else 2


if __name__ == "__main__":
    sys.exit(main())
