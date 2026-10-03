"""Change timeline: deploys, laptop fast-forwards, manual interventions, Fly pause/revision/tile/arm
transitions, AI model/prompt switches and collection epochs, newest first.

Read-only: composed on request from the receipts document, the per-cycle ``runtime_history``
rows the engine already stores, ``res_ai_calls`` and the mirrored ``research_session.json``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .facts import iso, parse_ts

RUNTIME_FIELDS = (
    ("git_rev", "FLY_REVISION"), ("paused", "PAUSE"), ("pause_owner", "PAUSE_OWNER"),
    ("live_armed", "LIVE_ARMED"), ("tiles", "ACTIVE_TILES"),
)
_RUNTIME_SQL = """
WITH r AS (
  SELECT "at",
         json_extract_string(doc, '$.fly.git_rev') AS git_rev,
         json_extract_string(doc, '$.fly.paused') AS paused,
         coalesce(json_extract_string(doc, '$.fly.pause_owner'), '') AS pause_owner,
         json_extract_string(doc, '$.fly.live_armed') AS live_armed,
         CAST(json_extract(doc, '$.fly.active_tile_lanes') AS VARCHAR) AS tiles
  FROM res_runtime_history
  WHERE kind = 'HEALTH' AND json_extract_string(doc, '$.fly.git_rev') IS NOT NULL
), l AS (
  SELECT *, LAG(git_rev) OVER w AS p_git_rev, LAG(paused) OVER w AS p_paused,
         LAG(pause_owner) OVER w AS p_pause_owner, LAG(live_armed) OVER w AS p_live_armed,
         LAG(tiles) OVER w AS p_tiles
  FROM r WINDOW w AS (ORDER BY "at")
)
SELECT * FROM l
WHERE p_git_rev IS NOT NULL AND (git_rev IS DISTINCT FROM p_git_rev OR paused IS DISTINCT FROM p_paused
   OR pause_owner IS DISTINCT FROM p_pause_owner OR live_armed IS DISTINCT FROM p_live_armed
   OR tiles IS DISTINCT FROM p_tiles)
ORDER BY "at"
"""
_AI_SQL = """
SELECT coalesce(model_served, 'unknown') AS model, coalesce(prompt_id, 'unlogged') AS prompt,
       count(*) AS calls, min(ts) AS first_ts, max(ts) AS last_ts
FROM res_ai_calls GROUP BY 1, 2 ORDER BY first_ts
"""


def _ev(at: Any, kind: str, summary: str, source: str, **detail: Any) -> dict[str, Any] | None:
    ts = parse_ts(at)
    if ts is None:
        return None
    return {"at": iso(ts), "ts": ts, "kind": kind, "summary": summary[:300], "source": source,
            "detail": {k: v for k, v in detail.items() if v is not None}}


def from_receipts(doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    doc = doc or {}
    out = []
    for d in doc.get("deploys") or []:
        sha = str(d.get("headSha") or "")[:12]
        out.append(_ev(d.get("createdAt"), "DEPLOY",
                       f"Fly deploy run {d.get('databaseId')} {d.get('status')}/{d.get('conclusion')} at {sha}: "
                       f"{d.get('displayTitle') or ''}", "receipts.deploys",
                       run=d.get("databaseId"), sha=d.get("headSha"), conclusion=d.get("conclusion"),
                       finished_at=d.get("updatedAt")))
    for a in doc.get("auto_ff") or []:
        out.append(_ev(a.get("at"), "LAPTOP_FAST_FORWARD",
                       f"{a.get('outcome')}: {a.get('repoRoot')} {str(a.get('from') or '')[:9]} -> "
                       f"{str(a.get('to') or '')[:9]} ({a.get('commits')} commits)", "receipts.auto_ff",
                       outcome=a.get("outcome"), to=a.get("to"), fly_rev=a.get("flyRev")))
    for m in doc.get("manual_interventions") or []:
        out.append(_ev(m.get("at"), "MANUAL", str(m.get("action") or ""), "receipts.manual_interventions"))
    return [e for e in out if e]


def runtime_transitions(store) -> list[dict[str, Any]]:
    if not store.table_exists("res_runtime_history"):
        return []
    out = []
    for r in store.read(_RUNTIME_SQL):
        for col, kind in RUNTIME_FIELDS:
            before, after = r.get(f"p_{col}"), r.get(col)
            if before == after:
                continue
            if col == "paused":
                kind = "PAUSE" if str(after).lower() == "true" else "RESUME"
            out.append(_ev(r["at"], kind, f"Fly {col}: {before} -> {after}", "runtime_history",
                           field=col, before=before, after=after))
    return [e for e in out if e]


def ai_switches(store) -> list[dict[str, Any]]:
    if not store.table_exists("res_ai_calls"):
        return []
    out = []
    for r in store.read(_AI_SQL):
        out.append(_ev(r["first_ts"], "AI_MODEL_PROMPT",
                       f"AI calls first seen with model={r['model']} prompt={r['prompt']} ({r['calls']} calls, "
                       f"last {iso(r['last_ts'])})", "res_ai_calls",
                       model=r["model"], prompt=r["prompt"], calls=int(r["calls"]), last_seen=iso(r["last_ts"])))
    return [e for e in out if e]


def epochs(mirror: Path) -> list[dict[str, Any]]:
    p = Path(mirror) / "research_session.json"
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = [
        _ev(s.get("bot_start_time"), "BOT_START", f"Fly bot session {s.get('bot_version')} started",
            "research_session.json", bot_version=s.get("bot_version"), launcher=s.get("launcher")),
        _ev(s.get("collector_v22_epoch_ts"), "EPOCH", f"collector epoch {s.get('collector_v22_epoch_id')}",
            "research_session.json", epoch_id=s.get("collector_v22_epoch_id"),
            collector=s.get("collector_version")),
    ]
    if s.get("fresh_collection_mode"):
        out.append(_ev(s.get("fresh_collection_start_time"), "EPOCH", "fresh collection started",
                       "research_session.json"))
    return [e for e in out if e]


def timeline(store, receipts: dict[str, Any] | None, mirror: Path, *, since: Any = None, kinds: str | None = None,
             limit: int = 200) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    for name, build in (("receipts", lambda: from_receipts(receipts)), ("runtime_history", lambda: runtime_transitions(store)),
                        ("ai_calls", lambda: ai_switches(store)), ("research_session", lambda: epochs(mirror))):
        try:
            rows = build()
            sources[name] = {"status": "OK", "events": len(rows)}
            events.extend(rows)
        except Exception as exc:  # noqa: BLE001 - one broken source must not hide the others
            sources[name] = {"status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    cutoff = parse_ts(since) if since else None
    wanted = {k.strip().upper() for k in (kinds or "").split(",") if k.strip()}
    events = [e for e in events if (cutoff is None or e["ts"] >= cutoff) and (not wanted or e["kind"] in wanted)]
    events.sort(key=lambda e: e["ts"], reverse=True)
    total = len(events)
    events = events[:max(1, min(int(limit), 2000))]
    for e in events:
        e.pop("ts", None)
    return {"schema": "self_aware_changes_v1", "total": total, "returned": len(events), "sources": sources,
            "kinds": sorted({e["kind"] for e in events}), "events": events}
