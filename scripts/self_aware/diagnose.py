"""Self-diagnosis: cross-stage invariants, progress checks and probable-cause attribution.

Each check returns a finding with severity GREEN/AMBER/RED/SKIP, what was
observed against what was expected, ranked probable causes with the evidence
behind each, a runbook anchor and a drill-down SQL over the store. Positive
progress is required; a live process or an HTTP 200 is not health.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import analyzer_sections, data_compat, fly_platform
from .config import RUNBOOK, RUNBOOK_BASE, THRESHOLDS, Paths
from .facts import iso, parse_ts, snapshot_age, watcher_check

GREEN, AMBER, RED, SKIP = "GREEN", "AMBER", "RED", "SKIP"
_RANK = {RED: 3, AMBER: 2, SKIP: 1, GREEN: 0}


@dataclass
class Finding:
    id: str
    title: str
    category: str
    severity: str
    observed: str
    expected: str
    causes: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    drill_sql: str | None = None
    emit_alarm: bool = True

    @property
    def runbook(self) -> str:
        return f"{RUNBOOK}#{self.id.replace('.', '-').replace('_', '-')}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["runbook"] = self.runbook
        d["runbook_url"] = RUNBOOK_BASE + self.runbook
        return d


def fmt_age(sec: float | None) -> str:
    if sec is None:
        return "unknown"
    sec = max(0.0, float(sec))
    if sec < 90:
        return f"{sec:.0f}s"
    if sec < 5400:
        return f"{sec / 60:.0f}m"
    if sec < 172800:
        return f"{sec / 3600:.1f}h"
    return f"{sec / 86400:.1f}d"


def _hhmm(ts: float | None) -> str:
    return (iso(ts) or "?")[11:16] + "Z" if ts else "never"


# ------------------------------------------------------------- signals

def signals(f: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Facts that explain failures. Each signal: {on: bool, detail: str}."""
    now = f["now"]
    rt = f.get("runtime") or {}
    sp = rt.get("strategy_progress") or {}
    s: dict[str, dict[str, Any]] = {}

    runs = [r.get("value", r) if isinstance(r, dict) else r for r in (f.get("deploys") or {}).get("runs") or []]
    runs = [r for r in runs if isinstance(r, dict)]
    runs.sort(key=lambda r: parse_ts(r.get("createdAt")) or 0, reverse=True)
    latest = runs[0] if runs else {}
    in_progress = str(latest.get("status") or "").lower() in ("in_progress", "queued", "waiting", "pending")
    deploy_pause = bool(rt.get("execution_paused")) and str(rt.get("pause_owner") or "").upper() == "DEPLOY_MAINTENANCE"
    s["deploy_maintenance"] = {"on": deploy_pause or in_progress,
                               "detail": (f"pause_owner={rt.get('pause_owner')} paused={rt.get('execution_paused')}; "
                                          f"latest deploy run {latest.get('databaseId')} {latest.get('status')}/"
                                          f"{latest.get('conclusion')} head {str(latest.get('headSha') or '')[:9]}")}
    s["latest_deploy"] = {"on": bool(latest), "detail": latest}

    eval_age = sp.get("evaluation_age_sec")
    ws_age = sp.get("ws_age_sec", rt.get("ws_age"))
    fly_proc = watcher_check(f, "fly.process") or {}
    timeouts = "timed out" in str(fly_proc.get("observed") or "").lower() or "timeout" in str(fly_proc.get("observed") or "").lower()
    cpu = (isinstance(eval_age, (int, float)) and eval_age > THRESHOLDS["evaluation_age_cpu_sec"]) or \
          (isinstance(ws_age, (int, float)) and ws_age > 30 and not rt.get("execution_paused")) or timeouts
    s["cpu_saturation"] = {"on": bool(cpu), "detail": f"evaluation_age={eval_age} ws_age={ws_age} fly.process='{fly_proc.get('observed')}'"}

    cvh = rt.get("cross_venue_health") or {}
    stale_venues = list(cvh.get("stale_venues") or [])
    bfx_stale = isinstance(ws_age, (int, float)) and ws_age > THRESHOLDS["venue_stale_sec"]
    s["stale_venue_feed"] = {"on": bool(stale_venues) or bfx_stale or (cvh and cvh.get("status") not in (None, "OK")),
                             "detail": f"cross_venue={cvh.get('status')} stale={stale_venues} bitfinex_ws_age={ws_age}"}

    ar = f.get("analyzer_run") or {}
    cyc = f.get("cycle") or {}
    run_age = now - (parse_ts(ar.get("startedAt")) or now) if ar.get("state") == "RUNNING" else 0
    cyc_stale = now - (parse_ts(cyc.get("updatedAt")) or now) if not cyc.get("finishedAt") else 0
    s["lock_holder"] = {"on": run_age > 40 * 60 or cyc_stale > 20 * 60,
                        "detail": f"analyzer run {ar.get('state')} pid {ar.get('pid')} for {fmt_age(run_age)}; "
                                  f"cycle phase {cyc.get('phase')} pid {cyc.get('pid')} last update {fmt_age(cyc_stale)} ago"}

    bal = watcher_check(f, "deepseek.balance") or {}
    s["deepseek_credit"] = {"on": bal.get("status") in (AMBER, RED), "detail": f"deepseek.balance {bal.get('status')}: {bal.get('observed')}"}

    errs = json.dumps((f.get("watcher") or {}).get("source_errors") or {}, default=str)
    rl = "429" in errs or "429" in str(fly_proc.get("observed") or "") or "too many" in errs.lower()
    s["rate_limited"] = {"on": rl, "detail": f"watcher source errors mention 429={rl}"}

    shipper = watcher_check(f, "shipper.progress") or {}
    s["shipper_stalled"] = {"on": shipper.get("status") in (AMBER, RED), "detail": f"shipper.progress {shipper.get('status')}: {shipper.get('observed')}"}

    paused = bool(rt.get("execution_paused"))
    s["paper_paused"] = {"on": paused, "detail": f"execution_paused={paused} owner={rt.get('pause_owner')}"}
    cpu_pct = f.get("laptop_cpu_pct")
    s["laptop_cpu"] = {"on": isinstance(cpu_pct, (int, float)) and cpu_pct >= 85,
                       "detail": f"laptop CPU {cpu_pct}% busy over 2 s; memory {f.get('laptop_mem_pct')}% used"}
    rt_age = snapshot_age(rt, now)
    s["runtime_snapshot_stale"] = {"on": rt_age is None or rt_age > THRESHOLDS["snapshot_max_age_sec"],
                                   "detail": f"fly runtime snapshot age {fmt_age(rt_age)}"}
    return s


CAUSES: dict[str, str] = {
    "deploy_maintenance": "A guarded deploy is in progress (maintenance pause, normal up to ~45 min).",
    "cpu_saturation": "Fly CPU saturation (1 vCPU): evaluation/WS loops are late or Fly requests time out.",
    "stale_venue_feed": "A venue feed is stale (Bitfinex WS or a cross-venue collector).",
    "lock_holder": "A laptop lock holder is stuck (analyzer run or cycle has not advanced).",
    "deepseek_credit": "DeepSeek credit is low or exhausted.",
    "rate_limited": "Fly public /api rate limit (60/min/IP) answered 429.",
    "shipper_stalled": "The Fly segment shipper is stalled or erroring.",
    "paper_paused": "Paper trading is paused.",
    "runtime_snapshot_stale": "The laptop's Fly runtime snapshot is stale (supervisor not polling or Fly unreachable).",
    "shipper_batching": "The segment shipper batches this file on a slower cadence than other streams.",
    "laptop_cpu": "The laptop CPU is saturated (analyzer, watcher or other load); laptop jobs run slow.",
}


def attribute(candidates: list[str], sig: dict[str, dict[str, Any]], extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = [{"cause": c, "text": CAUSES.get(c, c), "confidence": "likely", "evidence": sig[c]["detail"]}
           for c in candidates if c in sig and sig[c]["on"]]
    out.extend(extra or [])
    if not out:
        out.append({"cause": "unknown", "text": "No known signal explains this yet; follow the runbook and preserved evidence.",
                    "confidence": "unknown", "evidence": None})
    return out


# ------------------------------------------------------------- checks

def _q(store, sql: str) -> list[dict[str, Any]] | None:
    try:
        return store.read(sql)
    except Exception:  # noqa: BLE001 - a missing view is SKIP, never a crash
        return None


def check_fill_close(f, sig, store) -> Finding:
    rows = _q(store, f"""
        WITH x AS (
          SELECT fill_id, any_value(research_lane) AS lane,
                 max(try_cast(fill_ts AS DOUBLE)) AS fill_num, max(fill_ts) AS fill_txt,
                 count(close_ts) AS closes, max(activation_ts) AS act
          FROM raw_execution WHERE fill_id IS NOT NULL GROUP BY fill_id)
        SELECT count(*) AS fills, count(*) FILTER (WHERE closes = 0) AS unclosed,
               count(*) FILTER (WHERE closes = 0 AND (fill_num IS NULL OR fill_num < {f['now'] - THRESHOLDS['fill_close_max_open_sec']}))
                   AS overdue,
               min(fill_num) FILTER (WHERE closes = 0) AS oldest_open,
               list(fill_id) FILTER (WHERE closes = 0) AS unclosed_ids,
               list(lane) FILTER (WHERE closes = 0) AS unclosed_lanes
        FROM x""")
    if rows is None:
        return Finding("inv.fill_close", "Every fill reaches a close", "invariant", SKIP, "execution ledger unavailable",
                       "every fill_id has a terminal close row")
    r = rows[0]
    unclosed = int(r.get("unclosed") or 0)
    ids = (r.get("unclosed_ids") or [])[:10]
    overdue = int(r.get("overdue") or 0)
    oldest = r.get("oldest_open")
    sev = GREEN if overdue == 0 else AMBER
    held = f" (open positions, oldest {fmt_age(f['now'] - oldest)})" if unclosed and oldest else ""
    return Finding("inv.fill_close", "Every fill reaches a close", "invariant", sev,
                   f"{r.get('fills')} fills, {unclosed} without a close row{held}, {overdue} overdue"
                   + (f": {ids}" if overdue and ids else ""),
                   f"every fill_id has a close row; open longer than {fmt_age(THRESHOLDS['fill_close_max_open_sec'])} is overdue",
                   causes=[] if sev == GREEN else attribute(["shipper_stalled", "deploy_maintenance"], sig, [
                       {"cause": "open_position", "text": "Position may still be open (normal while held).",
                        "confidence": "possible", "evidence": f"lanes {r.get('unclosed_lanes')}"}]),
                   evidence={"unclosed_ids": ids},
                   drill_sql="SELECT fill_id, research_lane, fill_ts, close_ts, exit_reason, net_pnl_usd FROM raw_execution "
                             "WHERE fill_id IN (SELECT fill_id FROM raw_execution GROUP BY 1 HAVING count(close_ts)=0)")


def check_expired_filled(f, sig, store) -> Finding:
    rows = _q(store, """
        SELECT count(*) AS n, list(coalesce(record_id, event_id))[1:10] AS ids
        FROM raw_lifecycle
        WHERE fill_id IS NOT NULL AND (terminal_ttl_expired OR terminal_no_fill OR entry_outcome = 'NO_FILL')""")
    # The V3 lifecycle has no trade_id, so the legacy CSV ledgers are joined on trade_id as well (same windows as the watcher).
    legacy = _q(store, """
        WITH e AS (SELECT trade_id, max(try_cast(expired_ts AS DOUBLE)) AS ets FROM raw_expired_csv
                   WHERE coalesce(trade_id, '') <> '' GROUP BY 1),
             t AS (SELECT trade_id, max(ts) AS tts, any_value(research_lane) AS lane FROM raw_trades_csv
                   WHERE coalesce(trade_id, '') <> '' GROUP BY 1)
        SELECT t.trade_id, t.lane, e.ets, t.tts FROM e JOIN t USING (trade_id)""")
    if rows is None and legacy is None:
        return Finding("inv.expired_filled", "No order is both expired and filled", "invariant", SKIP,
                       "lifecycle and legacy ledgers unavailable", "0 rows expired/no-fill with a fill_id")
    n = int((rows or [{}])[0].get("n") or 0)
    now = f["now"]
    both = []
    for r in legacy or []:
        last = max(r.get("ets") or 0, parse_ts(r.get("tts")) or 0)
        both.append((r["trade_id"], r.get("lane"), now - last if last else None))
    red_2h = [b for b in both if b[2] is not None and b[2] <= 2 * 3600]
    amber_24h = [b for b in both if b[2] is not None and b[2] <= 24 * 3600]
    sev = RED if (n or red_2h) else AMBER if amber_24h else GREEN
    ids = (rows or [{}])[0].get("ids") if n else None
    recent = [f"{tid} ({lane}, {fmt_age(age)} ago)" for tid, lane, age in sorted(amber_24h, key=lambda b: b[2])[:6]]
    return Finding("inv.expired_filled", "No order is both expired and filled", "invariant", sev,
                   f"V3 lifecycle: {n} rows TTL-expired/no-fill with a fill_id" + (f" {ids}" if n else "")
                   + f"; legacy ledgers: {len(both)} trade_ids both expired and traded, {len(amber_24h)} in 24h, "
                     f"{len(red_2h)} in 2h" + (f": {recent}" if recent else ""),
                   "0 in the V3 lifecycle; legacy trade_ids both expired and traded: none in 2h (RED) or 24h (AMBER)",
                   causes=[] if sev == GREEN else [{"cause": "fill_expiry_race", "confidence": "likely",
                                                    "text": "Race between the fill and the order-expiry timer (fill-guard, see #253/#255).",
                                                    "evidence": ids or recent}],
                   evidence={"v3_ids": ids, "legacy_recent": recent},
                   drill_sql="SELECT t.trade_id, t.research_lane, t.ts, t.exit_reason, e.expired_ts, e.reason FROM "
                             "raw_trades_csv t JOIN raw_expired_csv e USING (trade_id) ORDER BY t.ts DESC")


def check_ai_response(f, sig, store) -> Finding:
    grace = THRESHOLDS["ai_call_unanswered_grace_sec"]
    rows = _q(store, """
        WITH i AS (SELECT trade_id, max(ts) AS ts FROM raw_ai_input WHERE trade_id IS NOT NULL GROUP BY 1),
             t AS (SELECT DISTINCT coalesce(nullif(shared_ai_call_id, ''), trade_id) AS cid FROM raw_ai_tranche
                   UNION SELECT DISTINCT trade_id FROM raw_ai_tranche),
             lim AS (SELECT max(ts) AS tmax FROM raw_ai_tranche)
        SELECT count(*) AS inputs,
               count(*) FILTER (WHERE t.cid IS NULL AND i.ts < (SELECT tmax FROM lim)) AS unanswered,
               list(i.trade_id) FILTER (WHERE t.cid IS NULL AND i.ts < (SELECT tmax FROM lim)) AS ids
        FROM i LEFT JOIN t ON t.cid = i.trade_id""")
    if rows is None:
        return Finding("inv.ai_response", "Every AI call has a response row", "invariant", SKIP,
                       "AI logs unavailable on the mirror", "every ai_input row has a tranche row")
    r = rows[0]
    n = int(r.get("unanswered") or 0)
    sev = GREEN if n == 0 else (AMBER if n <= 5 else RED)
    return Finding("inv.ai_response", "Every AI call has a response row", "invariant", sev,
                   f"{r.get('inputs')} AI inputs on the mirror, {n} without a response/outcome row"
                   + (f" (e.g. {(r.get('ids') or [])[:5]})" if n else ""),
                   f"0 unanswered (inputs newer than the latest tranche row are still in flight; grace {fmt_age(grace)})",
                   causes=[] if sev == GREEN else attribute(["deploy_maintenance", "deepseek_credit", "cpu_saturation"], sig),
                   evidence={"ids": (r.get("ids") or [])[:20]},
                   drill_sql="SELECT i.* FROM raw_ai_input i WHERE trade_id NOT IN (SELECT coalesce(nullif(shared_ai_call_id,''),"
                             " trade_id) FROM raw_ai_tranche) ORDER BY ts DESC")


def check_custody(f, sig, store) -> Finding:
    now = f["now"]
    head = f.get("segment_head") or {}
    pull = f.get("pull") or {}
    shipped = head.get("shipped_seq")
    acked = pull.get("ackedSeq", head.get("laptop_acked_seq"))
    applied = pull.get("appliedSeq")
    published = pull.get("remotePublishedSeq")
    parity_age = now - (parse_ts(pull.get("lastParityAt")) or 0) if pull.get("lastParityAt") else None
    pull_age = now - (parse_ts(pull.get("finishedAt")) or 0) if pull.get("finishedAt") else None
    if shipped is None or acked is None:
        return Finding("inv.custody", "Every Fly segment has a verified laptop copy", "invariant", SKIP,
                       "segment head or pull status unavailable", "laptop applied+acked == Fly shipped")
    lag = int(shipped) - int(acked)
    problems = []
    sev = GREEN
    if lag > THRESHOLDS["segment_ack_lag_seq"] or (pull_age is not None and pull_age > THRESHOLDS["segment_ack_lag_sec"] and lag > 0):
        sev = AMBER
        problems.append(f"ack lag {lag} seq")
    if applied is not None and int(applied) < int(acked):
        sev = RED
        problems.append(f"acked {acked} > applied {applied} (ACK without a local copy)")
    if pull.get("error"):
        sev = max(sev, AMBER, key=_RANK.get)
        problems.append(f"pull error: {str(pull.get('error'))[:120]}")
    if parity_age is not None and parity_age > THRESHOLDS["parity_max_age_sec"]:
        sev = max(sev, AMBER, key=_RANK.get)
        problems.append(f"last parity {fmt_age(parity_age)} ago")
    return Finding("inv.custody", "Every Fly segment has a verified laptop copy", "invariant", sev,
                   f"Fly shipped {shipped}, published {published}, laptop applied {applied}, acked {acked}; "
                   f"last pull {fmt_age(pull_age)} ago, last parity {fmt_age(parity_age)} ago"
                   + (f" - {'; '.join(problems)}" if problems else ""),
                   f"applied >= acked, lag <= {THRESHOLDS['segment_ack_lag_seq']} seq, parity <= {fmt_age(THRESHOLDS['parity_max_age_sec'])}",
                   causes=[] if sev == GREEN else attribute(["shipper_stalled", "lock_holder", "deploy_maintenance"], sig),
                   evidence={"segment_head": head, "pull": pull},
                   drill_sql="SELECT * FROM raw_puller_acks ORDER BY json->>'logged_at' DESC LIMIT 50")


def check_revision_parity(f, sig, store) -> Finding:
    rt = f.get("runtime") or {}
    fly = str(rt.get("git_rev") or "").lower()
    ar = f.get("analyzer_run") or {}
    analyzer = str(ar.get("revision") or "").lower()
    head = str(f.get("analyzer_head") or "").lower()
    dash = str(f.get("analyzer_dashboard_rev") or "").lower()
    if not fly:
        return Finding("inv.revision_parity", "Analyzer runs the revision Fly runs", "parity", SKIP,
                       "Fly revision unknown (runtime snapshot missing)", "analyzer revision == Fly revision")
    contains = f.get("contains_fly") or {}
    # Laptop-only [skip ci] merges put v2c ahead of Fly; the invariant is that the analyzer contains Fly's revision.
    match = lambda r, k: bool(r) and (r.startswith(fly) or fly.startswith(r[:12]) or contains.get(k) is True)  # noqa: E731
    ok_head, ok_run, ok_dash = match(head, "head"), match(analyzer, "run"), match(dash, "dash")
    sev = GREEN if ok_head and ok_run else AMBER
    # The :9001 dashboard restarts at the end of a run, so a lagging dashboard is only a problem once no run is in flight.
    dash_stale = bool(dash) and not ok_dash and ar.get("state") != "RUNNING"
    if dash_stale:
        sev = AMBER
    return Finding("inv.revision_parity", "Analyzer runs the revision Fly runs", "parity", sev,
                   f"Fly {fly[:12]}; v2c HEAD {head[:12] or '?'}; last analyzer run {analyzer[:12] or '?'} ({ar.get('state')}); "
                   f":9001 dashboard {dash[:12] or '?'}" + (" (dashboard serves an older revision with no run in flight)" if dash_stale else ""),
                   "v2c HEAD, the latest analyzer run and (between runs) the :9001 dashboard contain the Fly revision",
                   causes=[] if sev == GREEN else attribute(["deploy_maintenance", "lock_holder"], sig, [
                       {"cause": "autoff_pending", "confidence": "possible",
                        "text": "v2c auto-ff has not run yet (it follows Fly only after a successful guarded deploy).",
                        "evidence": (f.get("autoff") or [{}])[-1]}]),
                   evidence={"fly": fly, "v2c_head": head, "analyzer_run": analyzer, "dashboard": dash,
                             "contains_fly": contains},
                   drill_sql="SELECT json FROM raw_autoff_receipts ORDER BY json->>'at' DESC LIMIT 20",
                   emit_alarm=True)


def check_dashboards(f, sig, store) -> Finding:
    a = ((f.get("svc_9001_health") or {}).get("body") or {})
    b = ((f.get("svc_9011") or {}).get("body") or {})
    rt = f.get("runtime") or {}
    lanes_rt = sorted(rt.get("active_tile_lanes") or [])
    enabled = rt.get("research_lane_enabled") or {}
    lanes_on = sorted(k for k, v in enabled.items() if v)
    problems = []
    va, vb = a.get("verdict"), b.get("verdict")
    if va and vb and va != vb:
        problems.append(f":9001 shows {va} but :9011 shows {vb}")
    if not va:
        problems.append(f":9001 system-health unavailable ({(f.get('svc_9001_health') or {}).get('error')})")
    if not vb:
        problems.append(f":9011 unavailable ({(f.get('svc_9011') or {}).get('error')})")
    if lanes_rt and enabled and set(lanes_rt) != set(enabled):
        problems.append(f"runtime roster {lanes_rt} != toggle keys {sorted(enabled)}")
    exports = None
    try:
        exports = json.loads((Path(store.paths.exports) / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    ex_lanes = sorted(t.get("lane") for t in ((exports or {}).get("generation") or {}).get("active_tiles") or [])
    if ex_lanes and lanes_rt and set(ex_lanes) != set(lanes_rt):
        problems.append(f"analyzer export roster {ex_lanes} != Fly roster {lanes_rt}")
    sev = GREEN if not problems else AMBER
    return Finding("inv.dashboards_agree", "Dashboards and rosters agree", "parity", sev,
                   "; ".join(problems) or f"verdicts agree ({va}); roster {lanes_rt}, ON {lanes_on}",
                   ":9001 == :9011 verdict; Fly roster == toggle keys == analyzer export roster",
                   causes=[] if sev == GREEN else attribute(["deploy_maintenance"], sig, [
                       {"cause": "export_lag", "confidence": "possible",
                        "text": "The analyzer export predates the latest registry change (next cycle fixes it).",
                        "evidence": (exports or {}).get("generated_at")}]),
                   evidence={"lanes_runtime": lanes_rt, "lanes_export": ex_lanes, "verdict_9001": va, "verdict_9011": vb})


def check_mirror_lag(f, sig, store) -> list[Finding]:
    now = f["now"]
    rt = f.get("runtime") or {}
    sp = rt.get("strategy_progress") or {}
    m = f.get("mirror") or {}
    out = []
    fly_ai = parse_ts(sp.get("last_ai_success_at"))
    mir_ai = (m.get("ai_tranche") or {}).get("max_ts")
    tape = (m.get("tape") or {}).get("max_ts")
    tape_lag = now - tape if tape else None
    if fly_ai is None or mir_ai is None:
        out.append(Finding("inv.mirror_ai_lag", "AI call log reaches the laptop", "invariant", SKIP,
                           f"Fly last AI success {_hhmm(fly_ai)}, mirror AI log max {_hhmm(mir_ai)}",
                           "mirror AI log within 30m of Fly's last AI success"))
    else:
        lag = fly_ai - mir_ai
        sev = GREEN if lag <= THRESHOLDS["mirror_ai_lag_amber_sec"] else (
            AMBER if lag <= THRESHOLDS["mirror_ai_lag_red_sec"] else RED)
        extra = []
        if sev != GREEN and tape_lag is not None and tape_lag < THRESHOLDS["tape_lag_amber_sec"]:
            extra.append({"cause": "shipper_batching", "text": CAUSES["shipper_batching"], "confidence": "possible",
                          "evidence": f"tape is fresh ({fmt_age(tape_lag)}) while AI logs lag {fmt_age(lag)}"})
        out.append(Finding("inv.mirror_ai_lag", "AI call log reaches the laptop", "invariant", sev,
                           f"Fly last AI success {_hhmm(fly_ai)}; newest AI row on the laptop mirror {_hhmm(mir_ai)} "
                           f"(lag {fmt_age(max(0.0, lag))})",
                           f"lag <= {fmt_age(THRESHOLDS['mirror_ai_lag_amber_sec'])}",
                           causes=[] if sev == GREEN else attribute(["shipper_stalled", "deploy_maintenance"], sig, extra),
                           evidence={"fly_last_ai_success": iso(fly_ai), "mirror_ai_max": iso(mir_ai)},
                           drill_sql="SELECT ts, trade_id, event, decision, deepseek_model, latency_ms FROM raw_ai_tranche "
                                     "ORDER BY ts DESC LIMIT 50"))
    for key, title in (("tape", "Bitfinex 1 s tape reaches the laptop"), ("cross_venue", "Cross-venue tape reaches the laptop"),
                       ("decision", "Decision ledger reaches the laptop")):
        mx = (m.get(key) or {}).get("max_ts")
        lag = now - mx if mx else None
        if lag is None:
            sev = SKIP
        else:
            sev = GREEN if lag <= THRESHOLDS["tape_lag_amber_sec"] else (AMBER if lag <= THRESHOLDS["tape_lag_red_sec"] else RED)
        out.append(Finding(f"inv.mirror_{key}_lag", title, "invariant", sev,
                           f"newest {key} row on the laptop mirror is {fmt_age(lag)} old ({_hhmm(mx)})",
                           f"<= {fmt_age(THRESHOLDS['tape_lag_amber_sec'])}",
                           causes=[] if sev in (GREEN, SKIP) else attribute(
                               ["shipper_stalled", "deploy_maintenance", "stale_venue_feed", "lock_holder"], sig)))
    return out


def check_ai_cadence(f, sig, store) -> Finding:
    rt = f.get("runtime") or {}
    sp = rt.get("strategy_progress") or {}
    ai_age = sp.get("ai_age_sec")
    fails = sp.get("ai_consecutive_failures")
    rows = _q(store, """
        WITH d AS (SELECT try_cast(ts AS TIMESTAMPTZ) AS t FROM raw_ai_tranche WHERE event = 'AI_DECISION'),
             m AS (SELECT max(t) AS mx FROM d)
        SELECT count(*) FILTER (WHERE t > (SELECT mx FROM m) - INTERVAL 2 HOUR) / 2.0 AS per_hour,
               epoch((SELECT mx FROM m)) AS newest FROM d""")
    per_hour = float(rows[0]["per_hour"]) if rows and rows[0].get("per_hour") is not None else None
    newest = rows[0].get("newest") if rows else None
    mirror_age = f["now"] - float(newest) if newest is not None else None
    age_basis = "Fly"
    if not isinstance(ai_age, (int, float)) and mirror_age is not None:
        ai_age, age_basis = mirror_age, "newest mirror AI row"
    paused = sig["paper_paused"]["on"]
    owner = str(rt.get("pause_owner") or "").upper()
    up = sp.get("process_startup_age_sec")
    sev = GREEN
    problems = []
    if not paused and not isinstance(ai_age, (int, float)) and per_hour is None:
        return Finding("prog.ai_cadence", "AI calls keep advancing", "progress", SKIP,
                       "neither Fly nor the mirror reports an AI success time", "AI success time is observable")
    if (paused and owner == "DEPLOY_MAINTENANCE" and isinstance(up, (int, float))
            and up > THRESHOLDS["deploy_pause_amber_sec"]):
        sev = AMBER
        problems.append(f"paper still paused by DEPLOY_MAINTENANCE {fmt_age(up)} after boot "
                        "(resume is an operator/deployer action; flag only)")
    if isinstance(ai_age, (int, float)) and ai_age > THRESHOLDS["fly_ai_stale_sec"] and not paused:
        sev = RED if ai_age > 3 * THRESHOLDS["fly_ai_stale_sec"] else AMBER
        problems.append(f"last AI success {fmt_age(ai_age)} ago ({age_basis})")
    if per_hour is not None and per_hour < THRESHOLDS["ai_calls_per_hour_min"]:
        sev = max(sev, AMBER, key=_RANK.get)
        problems.append(f"mirror shows {per_hour:.1f} AI decisions/h in its last 2 h")
    if isinstance(fails, (int, float)) and fails >= 3:
        sev = max(sev, AMBER, key=_RANK.get)
        problems.append(f"{fails} consecutive AI failures")
    if paused and sev == GREEN and isinstance(ai_age, (int, float)) and ai_age > THRESHOLDS["fly_ai_stale_sec"]:
        problems.append("AI idle because paper is paused (expected, not counted)")
    return Finding("prog.ai_cadence", "AI calls keep advancing", "progress", sev,
                   "; ".join(problems) or f"last AI success {fmt_age(ai_age)} ago ({age_basis}); "
                                          f"mirror {per_hour} decisions/h",
                   f"Fly AI success <= {fmt_age(THRESHOLDS['fly_ai_stale_sec'])} ago while paper runs; >= "
                   f"{THRESHOLDS['ai_calls_per_hour_min']} decisions/h",
                   causes=[] if sev == GREEN else attribute(["deepseek_credit", "cpu_saturation", "deploy_maintenance",
                                                              "paper_paused"], sig),
                   evidence={"ai_age_sec": ai_age, "age_basis": age_basis, "per_hour": per_hour,
                             "consecutive_failures": fails, "pause_owner": owner or None,
                             "process_startup_age_sec": up})


def check_orders_on_tiles(f, sig, store) -> Finding:
    now = f["now"]
    rt = f.get("runtime") or {}
    enabled = {k: v for k, v in (rt.get("research_lane_enabled") or {}).items() if v}
    if not enabled:
        return Finding("prog.tile_orders", "ON tiles keep producing paper orders", "progress", SKIP,
                       "no tile is ON (or runtime snapshot missing)", "each ON tile has recent order activity")
    rows = _q(store, """
        SELECT research_lane AS lane, max(decision_ts) AS last_eligible
        FROM raw_decision WHERE execution_disposition = 'ORDER_ELIGIBLE' GROUP BY 1""") or []
    last = {r["lane"]: r["last_eligible"] for r in rows}
    fills = {r["lane"]: parse_ts(r["last_fill"]) for r in _q(store, """
        SELECT research_lane AS lane, max(coalesce(close_ts, fill_ts)) AS last_fill FROM raw_execution GROUP BY 1""") or []}
    quiet = []
    detail = []
    for lane in sorted(enabled):
        t = last.get(lane)
        age = now - t if t else None
        detail.append(f"{lane}: last order-eligible {fmt_age(age)} ago, last fill/close {fmt_age(now - fills[lane]) if fills.get(lane) else 'never'} ago")
        if age is None or age > THRESHOLDS["orders_quiet_amber_sec"]:
            quiet.append(lane)
    paused = sig["paper_paused"]["on"]
    sev = AMBER if quiet and not paused else GREEN
    return Finding("prog.tile_orders", "ON tiles keep producing paper orders", "progress", sev,
                   "; ".join(detail), f"each ON tile order-eligible within {fmt_age(THRESHOLDS['orders_quiet_amber_sec'])}",
                   causes=[] if sev == GREEN else attribute(["paper_paused", "deploy_maintenance", "stale_venue_feed"], sig, [
                       {"cause": "no_signal", "confidence": "possible",
                        "text": "Entry filters legitimately found no signal (rare-trigger tiles such as XVL).",
                        "evidence": quiet}]),
                   evidence={"quiet": quiet},
                   drill_sql="SELECT research_lane, execution_disposition, exact_reason, count(*), max(decision_ts) "
                             "FROM raw_decision GROUP BY ALL ORDER BY 5 DESC")


def check_feeds(f, sig, store) -> Finding:
    rt = f.get("runtime") or {}
    sp = rt.get("strategy_progress") or {}
    cvh = rt.get("cross_venue_health") or {}
    xvl = rt.get("xvl_evaluator_health") or {}
    ws_age = sp.get("ws_age_sec", rt.get("ws_age"))
    stale = list(cvh.get("stale_venues") or [])
    problems = []
    if isinstance(ws_age, (int, float)) and ws_age > 30:
        problems.append(f"Bitfinex WS age {fmt_age(ws_age)}")
    if stale:
        problems.append(f"stale venues {stale}")
    if cvh and cvh.get("status") not in (None, "OK"):
        problems.append(f"cross-venue {cvh.get('status')} ({cvh.get('reason')})")
    if xvl and xvl.get("status") not in (None, "OK"):
        problems.append(f"XVL evaluator {xvl.get('status')} ({xvl.get('reason')})")
    sev = GREEN if not problems else (RED if isinstance(ws_age, (int, float)) and ws_age > THRESHOLDS["venue_stale_sec"] else AMBER)
    if sig["runtime_snapshot_stale"]["on"]:
        sev, problems = SKIP, [sig["runtime_snapshot_stale"]["detail"]]
    return Finding("prog.feeds", "Every venue feed is fresh", "progress", sev,
                   "; ".join(problems) or f"Bitfinex WS {fmt_age(ws_age)}, cross-venue {cvh.get('status')} "
                                          f"(collector {cvh.get('collector_age_s')}s), XVL {xvl.get('status')}",
                   "Bitfinex WS <= 30s, no stale cross-venue collector, XVL evaluator OK",
                   causes=[] if sev in (GREEN, SKIP) else attribute(["stale_venue_feed", "cpu_saturation", "deploy_maintenance"], sig),
                   evidence={"cross_venue_health": cvh, "xvl": xvl, "ws_age": ws_age})


def check_advancing(f, sig, store, state: dict[str, Any]) -> Finding:
    now = f["now"]
    head = f.get("segment_head") or {}
    m = f.get("mirror") or {}
    cur = {"shipped_seq": head.get("shipped_seq"), "decision_rows": (m.get("decision") or {}).get("rows"),
           "tape_rows": (m.get("tape") or {}).get("rows"), "acked_seq": (f.get("pull") or {}).get("ackedSeq")}
    prev = state.get("advancing") or {}
    last_change = dict(prev.get("last_change") or {})
    values = dict(prev.get("values") or {})
    for k, v in cur.items():
        if v is None:
            continue
        if values.get(k) != v:
            last_change[k] = now
            values[k] = v
        last_change.setdefault(k, now)
    state["advancing"] = {"values": values, "last_change": last_change}
    stuck = {k: now - t for k, t in last_change.items() if now - t > 20 * 60}
    paused = sig["paper_paused"]["on"]
    sev = GREEN if not stuck or paused else (RED if any(v > 2 * 3600 for v in stuck.values()) else AMBER)
    return Finding("prog.counts_advancing", "Counters keep advancing", "progress", sev,
                   ", ".join(f"{k}={values.get(k)} (changed {fmt_age(now - last_change[k])} ago)" for k in sorted(last_change)),
                   "shipped/acked segment seq, decision rows and tape rows change within 20 min while paper runs",
                   causes=[] if sev == GREEN else attribute(["shipper_stalled", "lock_holder", "deploy_maintenance",
                                                              "cpu_saturation"], sig),
                   evidence={"stuck": stuck})


def check_analyzer(f, sig, store) -> Finding:
    now = f["now"]
    ar = f.get("analyzer_run") or {}
    cyc = f.get("cycle") or {}
    last_ok = parse_ts(ar.get("lastSuccessAt"))
    age = now - last_ok if last_ok else None
    running = ar.get("state") == "RUNNING" and not ar.get("finishedAt")
    running_for = now - (parse_ts(ar.get("startedAt")) or now) if running else None
    cycle_for = now - (parse_ts(cyc.get("startedAt")) or now) if not cyc.get("finishedAt") else None
    problems = []
    sev = GREEN
    if age is None or age > THRESHOLDS["analyzer_success_amber_sec"]:
        sev = RED if age is None or age > THRESHOLDS["analyzer_success_red_sec"] else AMBER
        problems.append(f"last successful analyzer run {fmt_age(age)} ago")
    if running_for is not None and running_for > THRESHOLDS["analyzer_cycle_slow_sec"]:
        sev = max(sev, AMBER, key=_RANK.get)
        problems.append(f"analyzer run at {str(ar.get('revision'))[:9]} running for {fmt_age(running_for)} "
                        f"(cycle {cyc.get('phase')} for {fmt_age(cycle_for)})")
    if ar.get("state") == "FAILED":
        sev = max(sev, AMBER, key=_RANK.get)
        problems.append(f"last run FAILED: {str(ar.get('detail'))[:120]}")
    svc = f.get("svc_9001") or {}
    if svc.get("error"):
        sev = max(sev, AMBER, key=_RANK.get)
        problems.append(f":9001 /api/health unreachable ({svc.get('error')})")
    return Finding("prog.analyzer", "Analyzer cycles complete on time", "progress", sev,
                   "; ".join(problems) or f"last success {fmt_age(age)} ago; cycle {cyc.get('phase') or 'idle'}"
                                          + (f" for {fmt_age(cycle_for)}" if cycle_for else ""),
                   f"success <= {fmt_age(THRESHOLDS['analyzer_success_amber_sec'])} ago; analyzer run <= "
                   f"{fmt_age(THRESHOLDS['analyzer_cycle_slow_sec'])}",
                   causes=[] if sev == GREEN else attribute(["laptop_cpu", "lock_holder", "deploy_maintenance"], sig),
                   evidence={"analyzer_run": ar, "cycle": cyc})


def check_fly_reachability(f, sig, store, state: dict[str, Any]) -> Finding:
    """429 is AMBER 'rate-limited', not RED 'unreachable', unless it persists."""
    now = f["now"]
    w = watcher_check(f, "fly.process") or {}
    rt_fresh = not sig["runtime_snapshot_stale"]["on"]
    rl = sig["rate_limited"]["on"]
    rl_state = state.setdefault("rate_limited", {})
    if rl:
        rl_state.setdefault("since", now)
    else:
        rl_state.pop("since", None)
    rl_for = now - rl_state["since"] if rl_state.get("since") else 0
    status = w.get("status")
    if status in (None, GREEN, SKIP) and rt_fresh:
        sev, obs = GREEN, f"watcher fly.process {status}; runtime snapshot fresh"
    elif rl and rl_for < THRESHOLDS["rate_limit_persistent_sec"]:
        sev, obs = AMBER, f"rate-limited (HTTP 429) for {fmt_age(rl_for)}; watcher says {status}: {w.get('observed')}"
    elif rt_fresh:
        sev, obs = AMBER, f"watcher fly.process {status} ({w.get('observed')}) but the runtime snapshot is fresh"
    else:
        sev, obs = (RED if status == RED else AMBER), f"watcher fly.process {status}: {w.get('observed')}; " + sig["runtime_snapshot_stale"]["detail"]
    return Finding("fly.reachability", "Fly is reachable (429 counts as rate-limited)", "attribution", sev, obs,
                   "Fly answers; HTTP 429 is AMBER rate-limited unless it persists "
                   f"{fmt_age(THRESHOLDS['rate_limit_persistent_sec'])}",
                   causes=[] if sev == GREEN else attribute(["rate_limited", "cpu_saturation", "deploy_maintenance"], sig),
                   evidence={"watcher_fly_process": w, "rate_limited_for_sec": rl_for})


def check_engine(f, sig, store, state: dict[str, Any]) -> Finding:
    errs = {k: v for k, v in (state.get("job_errors") or {}).items() if v}
    mirror_errs = f.get("errors") or {}
    problems = [f"{k}: {v['error'][:120]}" for k, v in errs.items()] + [f"{k}: {v[:120]}" for k, v in mirror_errs.items()]
    problems += [f"view {k} failed to load: {v[:120]}" for k, v in (state.get("view_errors") or {}).items()]
    sev = GREEN if not problems else AMBER
    return Finding("self.engine", "Self-aware engine jobs succeed", "self", sev,
                   "; ".join(problems) or "all jobs succeeded on their last run", "no job errors",
                   causes=[] if sev == GREEN else [{"cause": "engine_error", "confidence": "likely",
                                                    "text": "A self-aware job failed; see the journal and logs.",
                                                    "evidence": problems[:5]}])


def check_data(f, sig, store) -> list[Finding]:
    """Data awareness: critical streams fresh, every slot filled, watched fields alive, room to grow."""
    d = f.get("data_awareness")
    t = THRESHOLDS
    ids = ("data.freshness", "data.completeness", "data.dead_fields", "data.capacity", "data.sufficiency")
    gen = parse_ts((d or {}).get("generated_at"))
    if not d or not gen or f["now"] - gen > t["data_doc_max_age_sec"]:
        return [Finding(i, "Data awareness", "data", SKIP, "data awareness has not run recently", "data job every 30 min",
                        emit_alarm=False) for i in ids]
    out = []
    stale = d.get("stale_critical") or []
    out.append(Finding("data.freshness", "Critical streams are fresh against the mirror head", "data",
                       AMBER if stale else GREEN,
                       f"stale or missing critical streams: {stale}" if stale else
                       f"all critical streams within their cadence of the mirror head {d.get('mirror_head')}",
                       "tape/cross-venue/market-context/AI streams within 3 min / 10 min / 6 h of the mirror head",
                       causes=attribute(["stale_venue_feed", "deploy_maintenance", "shipper_stalled"], sig) if stale else [],
                       drill_sql="SELECT stream, status, last_ts, lag_vs_mirror_head_sec FROM res_data_catalog "
                                 "WHERE critical ORDER BY lag_vs_mirror_head_sec DESC"))
    tape = d.get("tape") or {}
    probs = []
    fill = tape.get("fill_pct_24h_excl_interruptions")
    if fill is not None and fill < t["tape_fill_amber_pct"]:
        probs.append(f"Bitfinex 1 s tape {fill:.2f}% filled over 24 h outside Fly interruptions "
                     f"({tape.get('gaps_24h')} gaps, {tape.get('gap_sec_24h')} s)")
    big = [g for g in tape.get("unexplained_gaps_24h") or [] if g["sec"] >= t["tape_gap_amber_sec"]]
    if big:
        probs.append(f"unexplained tape gaps >= {t['tape_gap_amber_sec']}s: " +
                     ", ".join(f"{g['start'][11:19]}Z {g['sec']}s" for g in big[:3]))
    for name, m in (d.get("minute_streams") or {}).items():
        if m.get("fill_pct_24h") is not None and m["hours"] >= 3 and m["fill_pct_24h"] < t["minute_fill_amber_pct"]:
            probs.append(f"{name} {m['fill_pct_24h']:.1f}% of minutes present over {m['hours']} h")
    out.append(Finding("data.completeness", "Every time slot is filled", "data", AMBER if probs else GREEN,
                       "; ".join(probs) if probs else
                       f"tape {tape.get('fill_pct_24h_excl_interruptions')}% filled (24 h, outside interruptions; "
                       f"{tape.get('gaps_24h')} gaps >5 s, largest {((tape.get('largest_gaps') or [{}])[0]).get('sec')} s); "
                       + ", ".join(f"{k} {v.get('fill_pct_24h')}%" for k, v in (d.get("minute_streams") or {}).items()),
                       f"tape >= {t['tape_fill_amber_pct']}% and no unexplained gap >= {t['tape_gap_amber_sec']}s; "
                       f"minute streams >= {t['minute_fill_amber_pct']}%",
                       causes=attribute(["stale_venue_feed", "cpu_saturation", "deploy_maintenance"], sig) if probs else [],
                       evidence={"largest_gaps": tape.get("largest_gaps"), "minute_streams": d.get("minute_streams")},
                       drill_sql=None))
    wa = d.get("watch_alarms") or {}
    flat = [f"{s}.{x}" for s, xs in wa.items() for x in xs]
    out.append(Finding("data.dead_fields", "Watched fields are alive (not null, all-zero or constant)", "data",
                       AMBER if flat else GREEN,
                       (f"{len(flat)} watched field(s) dead or constant: " + "; ".join(flat[:8])) if flat else
                       f"all watched fields alive ({d.get('dead_field_count')} unwatched fields dead across "
                       f"{d.get('streams')} streams; see /api/selfaware/data/fields)",
                       "every watched field varies and is populated in the latest sample",
                       causes=[{"cause": "dead_input", "confidence": "likely",
                                "text": "The producer writes a placeholder (0/null/constant) instead of the real value; "
                                        "the field is useless for research and, for AI inputs, misleads the model.",
                                "evidence": flat[:8]}] if flat else [],
                       drill_sql="SELECT stream, field, n, null_pct, zero_pct, constant_value, status FROM res_data_fields "
                                 "WHERE watched AND status <> 'OK' ORDER BY stream, field"))
    cap = d.get("capacity") or {}
    lap, fly = cap.get("laptop") or {}, cap.get("fly") or {}
    days, hours = lap.get("days_to_90pct_cap"), fly.get("hours_to_full")
    sev = GREEN
    if (days is not None and days < t["laptop_days_to_cap_red"]) or (hours is not None and hours < t["fly_hours_to_full_red"]):
        sev = RED
    elif (days is not None and days < t["laptop_days_to_cap_amber"]) or (hours is not None and hours < t["fly_hours_to_full_amber"]):
        sev = AMBER
    out.append(Finding("data.capacity", "Room to keep collecting (laptop 50 GB cap, Fly volume)", "data", sev,
                       f"laptop {lap.get('bot_data_gb')}/{lap.get('cap_gb')} GB ({lap.get('usage_pct')}%), growing "
                       f"{lap.get('growth_gb_per_day')} GB/day -> {days} days to 90% of cap; Fly volume "
                       f"{fly.get('volume_free_gb')} GB free, {hours} h to full, ingest {fly.get('ingest_gb_per_day')} GB/day",
                       f"laptop >= {t['laptop_days_to_cap_amber']:.0f} days and Fly >= {t['fly_hours_to_full_amber']:.0f} h of headroom",
                       evidence={"laptop": lap, "fly": fly}))
    suff = d.get("sufficiency") or []
    out.append(Finding("data.sufficiency", "Research questions: data present and enough samples", "data", GREEN,
                       "; ".join(f"{q['id']} {q['status']}" + (f" (ETA {q['eta_ready'][:10]})" if q.get("eta_ready") else "")
                                 for q in suff) or "no questions registered",
                       "informational: READY questions release their gated edge screens", emit_alarm=False,
                       drill_sql="SELECT id, status, full_question_status, blockers, missing_for_full_answer, short_samples, "
                                 "eta_ready FROM res_data_sufficiency"))
    return out


_SECTION_TITLES = {
    "analyzer.sections": "Every :9001 analyzer section is readable, populated and fresh",
    "analyzer.dimensions": "Top-100 combos and Safe Policy Genome evaluate every policy dimension",
    "analyzer.consistency": "Analyzer sections agree with the collected data",
}


def check_analyzer_sections(f, sig, store) -> list[Finding]:
    """Analyzer dashboard sections (2-hourly): populated, fresh, dimension-complete, consistent with collection."""
    rows = analyzer_sections.findings(f.get("analyzer_sections"), f["now"], THRESHOLDS["sections_doc_max_age_sec"])
    return [Finding(r["id"], _SECTION_TITLES[r["id"]], "analyzer", r["severity"], r["observed"], r["expected"],
                    evidence=r.get("evidence") or {}, emit_alarm=r.get("emit_alarm", True),
                    drill_sql=None) for r in rows]


_COMPAT_TITLES = {
    "data.compat_mixed": "One data version / clean epoch per analysis input",
    "data.compat_schema": "Every stream schema announced (no silent drift)",
    "data.compat_declared": "Every row declares its data version / epoch",
    "data.compat_epoch_purity": "Analyzer results contain no pre-epoch rows",
}


def check_data_compat(f, sig, store) -> list[Finding]:
    """Data compatibility (30-min job): versions, epoch classes, schema drift, analyzer epoch purity."""
    doc = f.get("data_compat")
    rows = data_compat.findings(doc, f["now"], THRESHOLDS["compat_doc_max_age_sec"], (doc or {}).get("epoch_purity"))
    return [Finding(r["id"], _COMPAT_TITLES[r["id"]], "data", r["severity"], r["observed"], r["expected"],
                    evidence=r.get("evidence") or {}, emit_alarm=r.get("emit_alarm", True), drill_sql=None)
            for r in rows]


# ------------------------------------------------------------- run

CONTRACT_SURFACES = {"analyzer": "Analyzer :9001 sections", "fly": "Fly dashboard panels and snapshots",
                     "exports": "Analyzer exports", "selfaware": "Self-aware :9021 documents", "watcher": "Health watcher :9011"}


def check_contracts(f, sig, store) -> list[Finding]:
    """Section contracts: every dashboard section carries the content it claims (rows, dimensions, reconciliation)."""
    c = f.get("contracts")
    ids = [f"contract.{s}" for s in CONTRACT_SURFACES] + ["contract.collapse", "contract.archive_drift", "contract.coverage"]
    gen = parse_ts((c or {}).get("generated_at"))
    heavy = parse_ts((c or {}).get("heavy_at"))
    if not c or not gen or f["now"] - gen > 3 * 3600:
        return [Finding(i, "Section contracts", "contracts", SKIP, "section contracts have not run recently",
                        "contracts_light every 5 min, heavy every 2 h", emit_alarm=False) for i in ids]
    drill = ("SELECT \"at\", id, json_extract_string(doc, '$.status') AS status, json_extract(doc, '$.violations') AS violations "
             "FROM res_contract_history WHERE json_extract_string(doc, '$.status') <> 'GREEN' ORDER BY \"at\" DESC LIMIT 200")
    out = []
    for surface, title in CONTRACT_SURFACES.items():
        sev = (c.get("surfaces") or {}).get(surface, GREEN)
        bad = [o for o in c.get("offenders") or [] if o["id"].split(".")[0] == surface]
        out.append(Finding(f"contract.{surface}", f"{title} honour their content contracts", "contracts",
                           sev if sev in (RED, AMBER) else GREEN,
                           "; ".join(f"{o['id']} {o['status']}: {o['why'][:160]}" for o in bad[:4]) if bad else
                           f"every {surface} contract GREEN",
                           "reachable JSON, required fields populated, min rows, expected dimensions, reconciled counts",
                           evidence={"offenders": bad[:20], "api": f"/api/selfaware/contracts?surface={surface}"},
                           drill_sql=drill))
    col = c.get("collapse") or []
    out.append(Finding("contract.collapse", "No section collapsed (dimensions, rows, silent emptiness)", "contracts",
                       RED if col else GREEN,
                       "collapsed: " + ", ".join(f"{x['id']} ({'/'.join(x['kinds'])})" for x in col[:6]) if col else
                       "no dimension collapse, silent emptiness, label contradiction or dead Fly panel",
                       "every section keeps its expected dimensions and row counts vs its baseline",
                       evidence={"collapse": col}, drill_sql=drill))
    adf, adr = c.get("archive_drift_findings") or 0, c.get("archive_drift_red") or 0
    stale_heavy = not heavy or f["now"] - heavy > THRESHOLDS["contracts_heavy_max_age_sec"]
    out.append(Finding("contract.archive_drift", "Archived analyzer reports keep their shape across generations", "contracts",
                       AMBER if (adf or stale_heavy) else GREEN,
                       (f"heavy contract pass last ran {c.get('heavy_at')}; " if stale_heavy else "") +
                       (f"{adf} archive drift findings ({adr} RED)" if adf else "no report vanished or shrank across generations"),
                       "reports, list lengths and columns stable across the last 8 archive generations",
                       evidence={"api": "/api/selfaware/contracts"}, drill_sql=None))
    unc = c.get("uncovered") or []
    cov = c.get("coverage") or {}
    out.append(Finding("contract.coverage", "Every /details section has a content contract", "contracts",
                       AMBER if unc or cov.get("error") else GREEN if cov else SKIP,
                       f"sections without a contract: {unc}" if unc else
                       f"coverage parse failed: {cov['error']}" if cov.get("error") else
                       f"{cov.get('covered')}/{cov.get('sections')} sections covered" if cov else
                       "coverage is computed by the heavy pass",
                       "each REPORT_NAV_GROUPS section is named in a contract's 'covers'", emit_alarm=bool(unc)))
    return out


def run(paths: Paths, store, facts: dict[str, Any], state: dict[str, Any]) -> list[Finding]:
    sig = signals(facts)
    checks: list[Callable[[], Any]] = [
        lambda: check_fill_close(facts, sig, store),
        lambda: check_expired_filled(facts, sig, store),
        lambda: check_ai_response(facts, sig, store),
        lambda: check_custody(facts, sig, store),
        lambda: check_revision_parity(facts, sig, store),
        lambda: check_dashboards(facts, sig, store),
        lambda: check_mirror_lag(facts, sig, store),
        lambda: check_ai_cadence(facts, sig, store),
        lambda: check_orders_on_tiles(facts, sig, store),
        lambda: check_feeds(facts, sig, store),
        lambda: check_advancing(facts, sig, store, state),
        lambda: check_analyzer(facts, sig, store),
        lambda: check_fly_reachability(facts, sig, store, state),
        lambda: check_engine(facts, sig, store, state),
        lambda: check_data(facts, sig, store),
        lambda: check_analyzer_sections(facts, sig, store),
        lambda: check_contracts(facts, sig, store),
        lambda: check_data_compat(facts, sig, store),
    ]
    findings: list[Finding] = []
    for fn in checks:
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
            res = Finding(f"self.check_error.{getattr(fn, '__name__', 'check')}", "A diagnosis check crashed", "self", AMBER,
                          f"{type(exc).__name__}: {str(exc)[:200]}", "checks run without exceptions")
        findings.extend(res if isinstance(res, list) else [res])
    try:
        findings.append(check_fly_platform(facts, findings, state))
    except Exception as exc:  # noqa: BLE001
        findings.append(Finding("self.check_error.fly_platform", "A diagnosis check crashed", "self", AMBER,
                                f"{type(exc).__name__}: {str(exc)[:200]}", "checks run without exceptions"))
    return findings


def check_fly_platform(f, findings: list[Finding], state: dict[str, Any]) -> Finding:
    """Fly.io status page vs our Fly-facing findings; runs last so it can annotate them."""
    res = fly_platform.assess(f, findings, state, f["now"])
    f["fly_platform"] = fly_platform.fps.compact(res)
    fly_platform.annotate(findings, res)
    d = fly_platform.finding(res)
    return Finding(d["id"], d["title"], d["category"], d["severity"], d["observed"], d["expected"],
                   evidence=d["evidence"], emit_alarm=d["emit_alarm"])


def verdict(findings: list[Finding]) -> str:
    worst = max((_RANK[f.severity] for f in findings), default=0)
    return {3: RED, 2: AMBER}.get(worst, GREEN)


def transitions(findings: list[Finding], state: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """Edge-triggered changes vs the previous run; updates ``state['findings']``."""
    prev = state.setdefault("findings", {})
    events = []
    seen = set()
    for fd in findings:
        seen.add(fd.id)
        p = prev.get(fd.id) or {}
        old = p.get("severity", GREEN)
        new = fd.severity if fd.severity != SKIP else old
        if new != old:
            kind = "OPENED" if old == GREEN else ("CLEARED" if new == GREEN else "CHANGED")
            events.append({"at": iso(now), "kind": kind, "id": fd.id, "from": old, "to": new, "finding": fd.to_dict()})
            p = {"severity": new, "since": now}
        prev[fd.id] = {"severity": new, "since": p.get("since", now), "last_seen": now}
    for fid in list(prev):
        if fid not in seen and now - prev[fid].get("last_seen", now) > 86400:
            prev.pop(fid)
    return events


def preserve_evidence(paths: Paths, events: list[dict[str, Any]], facts: dict[str, Any]) -> list[str]:
    """Write an evidence bundle when a finding opens or worsens (bounded retention)."""
    out = []
    keep = {k: facts.get(k) for k in ("runtime", "segment_head", "pull", "analyzer_run", "cycle", "relay", "mirror",
                                      "proof_active", "analyzer_head", "analyzer_dashboard_rev", "errors")}
    keep["watcher_failing"] = (facts.get("watcher") or {}).get("failing")
    paths.evidence.mkdir(parents=True, exist_ok=True)
    for e in events:
        if e["kind"] == "CLEARED":
            continue
        name = f"{e['at'].replace(':', '').replace('-', '')}-{e['id']}.json"
        target = paths.evidence / name
        target.write_text(json.dumps({"event": e, "facts": keep}, default=str, indent=1), encoding="utf-8")
        out.append(str(target))
    files = sorted(paths.evidence.glob("*.json"))
    for old in files[:-THRESHOLDS["evidence_keep_files"]]:
        try:
            old.unlink()
        except OSError:
            pass
    return out
