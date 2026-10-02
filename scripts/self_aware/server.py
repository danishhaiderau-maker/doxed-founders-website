"""Read-only HTTP API on 127.0.0.1:9021. Every response is computed by the engine and read from the store.

Endpoints (all GET, JSON unless noted):
  /                                 HTML overview (digest, findings, AI, edges, uptime)
  /api/ping
  /api/selfaware/health             verdict + findings (+ causes, runbook, drill SQL) + signals + engine
  /api/selfaware/findings/history   ?since=ISO&limit=
  /api/selfaware/uptime
  /api/selfaware/ai/calls           ?since=ISO|epoch&limit=&model=&prompt_id=&lane=&labeled=1
  /api/selfaware/ai/calls/<call_id>
  /api/selfaware/ai/scorecard       ?window=all|7d|24h&horizon=5m&slice=overall&strategy=
  /api/selfaware/edges              ?status=CANDIDATE,HINT
  /api/selfaware/edges/playbook
  /api/selfaware/tiles              ?window=24h|7d|all
  /api/selfaware/receipts
  /api/selfaware/digest             ?history=N
  /api/selfaware/repairs            ?limit=
  /api/selfaware/tables             raw views + result tables + provenance
  /api/selfaware/query?sql=SELECT…  guarded single SELECT (also POST {"sql": …})
"""
from __future__ import annotations

import html
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .ai_scorecard import headline as ai_headline, json_safe
from .config import SERVER_PORT
from .facts import parse_ts, tail_jsonl
from .store import QueryRejected


class SelfAwareServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    engine = None


def make_server(engine, port: int = SERVER_PORT) -> SelfAwareServer:
    srv = SelfAwareServer(("127.0.0.1", port), Handler)
    srv.engine = engine
    return srv


def _rows(store, sql: str, params: list | None = None) -> list[dict[str, Any]]:
    return json_safe(store.read(sql, params or []))


class Handler(BaseHTTPRequestHandler):
    server_version = "SelfAware/1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        return

    # ----------------------------------------------------------- plumbing
    @property
    def eng(self):
        return self.server.engine

    def _send(self, code: int, body: Any, ctype: str = "application/json") -> None:
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(json_safe(body), allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _prov(self, *names: str) -> dict[str, Any]:
        return {n: self.eng.store.provenance(n) for n in names}

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/selfaware/query":
            return self._send(404, {"error": "not found"})
        length = min(int(self.headers.get("Content-Length") or 0), 100_000)
        try:
            sql = json.loads(self.rfile.read(length) or b"{}").get("sql", "")
        except ValueError:
            return self._send(400, {"error": "body must be JSON {\"sql\": ...}"})
        self._query(sql, None)

    def do_GET(self) -> None:  # noqa: N802
        if self.eng is None:
            return self._send(503, {"error": "engine starting"})
        u = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        path = u.path.rstrip("/") or "/"
        try:
            route = ROUTES.get(path)
            if route:
                return route(self, q)
            if path.startswith("/api/selfaware/ai/calls/"):
                return self.ai_call(unquote(path.rsplit("/", 1)[1]))
            self._send(404, {"error": "not found", "endpoints": sorted(ROUTES)})
        except QueryRejected as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"})

    # ------------------------------------------------------------- routes
    def ping(self, q):
        self._send(200, {"ok": True, "service": "self_aware", "engine": self.eng.engine_status()})

    def health(self, q):
        doc = self.eng.docs.get("health")
        if not doc:
            rows = _rows(self.eng.store, "SELECT * FROM res_findings") if self.eng.store.table_exists("res_findings") else []
            doc = {"schema": "self_aware_health_v1", "verdict": None, "findings": rows, "note": "engine warming up; last stored findings",
                   "engine": self.eng.engine_status()}
        self._send(200, {**doc, "provenance": self._prov("findings")})

    def findings_history(self, q):
        self._send(200, {"events": self.eng.store.history("findings_events", limit=min(int(q.get("limit", 200)), 2000),
                                                          since=q.get("since"))})

    def uptime(self, q):
        doc = self.eng.docs.get("uptime") or (self.eng.store.history("uptime_history", limit=1) or [None])[0]
        self._send(200 if doc else 503, doc or {"error": "uptime not computed yet"})

    def ai_calls(self, q):
        st = self.eng.store
        if not st.table_exists("res_ai_calls"):
            return self._send(503, {"error": "AI call log not computed yet"})
        where, params = [], []
        if q.get("since"):
            where.append("ts >= ?")
            params.append(parse_ts(q["since"]) or 0)
        for key, col in (("model", "model_served"), ("prompt_id", "prompt_id"), ("lane", "lane")):
            if q.get(key):
                where.append(f"{col} = ?")
                params.append(q[key])
        if q.get("labeled") == "1":
            where.append("labels_complete")
        limit = max(1, min(int(q.get("limit", 100)), 1000))
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        cols = st.read("DESCRIBE res_ai_calls")
        keep = [c["column_name"] for c in cols if c["column_name"] not in ("raw_response",)]
        rows = _rows(st, f"SELECT {', '.join(chr(34) + c + chr(34) for c in keep)} FROM res_ai_calls {clause} "
                         f"ORDER BY ts DESC LIMIT {limit}", params)
        total = st.read(f"SELECT count(*) AS n FROM res_ai_calls {clause}", params)[0]["n"]
        self._send(200, {"schema": "self_aware_ai_calls_v1", "total": total, "returned": len(rows), "calls": rows,
                         "detail": "/api/selfaware/ai/calls/<call_id> adds raw_response and drill-down SQL",
                         "provenance": self._prov("ai_calls")})

    def ai_call(self, call_id: str):
        st = self.eng.store
        rows = _rows(st, "SELECT * FROM res_ai_calls WHERE call_id = ?", [call_id]) if st.table_exists("res_ai_calls") else []
        if not rows:
            return self._send(404, {"error": "unknown call_id"})
        cid = call_id.replace("'", "''")
        self._send(200, {"call": rows[0], "drill_sql": {
            "tranche": f"SELECT * FROM raw_ai_tranche WHERE shared_ai_call_id = '{cid}'",
            "input": f"SELECT * FROM raw_ai_input WHERE trade_id = '{(rows[0].get('input_ref') or '').replace(chr(39), '')}'",
            "challengers": f"SELECT * FROM raw_ai_challengers WHERE shared_ai_call_id = '{cid}'",
            "compact": f"SELECT * FROM raw_ai_compact WHERE shared_ai_call_id = '{cid}'"},
            "provenance": self._prov("ai_calls")})

    def ai_scorecard(self, q):
        st = self.eng.store
        if not st.table_exists("res_ai_scorecard"):
            return self._send(503, {"error": "AI scorecard not computed yet"})
        where = ['"window" = ?', "slice_dim = ?"]
        params = [q.get("window", "all"), q.get("slice", "overall")]
        if q.get("horizon"):
            where.append("horizon = ?")
            params.append(q["horizon"])
        if q.get("strategy"):
            where.append("strategy = ?")
            params.append(q["strategy"])
        rows = _rows(st, f"SELECT * FROM res_ai_scorecard WHERE {' AND '.join(where)} ORDER BY horizon_sec, slice_value, strategy",
                     params)
        self._send(200, {"schema": "self_aware_ai_scorecard_v1", "filters": dict(zip(("window", "slice"), params[:2])),
                         "headline": json_safe(ai_headline(st)), "rows": rows,
                         "method": "entry = decision second +1 s Bitfinex 1 s L1 mid; cost = half-spread in+out; "
                                   "hit CI Wilson 95%; net CI hour-cluster bootstrap 95%",
                         "provenance": self._prov("ai_scorecard", "ai_calls")})

    def edges(self, q):
        st = self.eng.store
        if not st.table_exists("res_edges"):
            return self._send(503, {"error": "edges not computed yet"})
        statuses = [s.strip().upper() for s in q.get("status", "").split(",") if s.strip()]
        clause = f"WHERE status IN ({','.join('?' * len(statuses))})" if statuses else ""
        rows = _rows(st, f"SELECT * FROM res_edges {clause} ORDER BY CASE status WHEN 'CANDIDATE' THEN 0 WHEN 'HINT' THEN 1 "
                         f"WHEN 'WATCH' THEN 2 ELSE 3 END, holdout_net_bp DESC", statuses)
        from .edges import load_registry  # noqa: PLC0415
        reg = load_registry()
        self._send(200, {"schema": "self_aware_edges_v1", "registry_hash": reg["registry_hash"],
                         "registered_at": reg.get("registered_at"), "guards": reg.get("guards"),
                         "counts": {s: sum(1 for r in rows if r["status"] == s) for s in
                                    ("CANDIDATE", "HINT", "WATCH", "REJECTED", "INSUFFICIENT")},
                         "edges": rows, "provenance": self._prov("edges", "edge_events")})

    def playbook(self, q):
        st = self.eng.store
        rows = _rows(st, "SELECT * FROM res_regime_playbook") if st.table_exists("res_regime_playbook") else []
        self._send(200, {"schema": "self_aware_regime_playbook_v1", "cells": rows,
                         "note": "descriptive: best pre-registered screen per regime cell on holdout; not a trading signal",
                         "provenance": self._prov("regime_playbook")})

    def tiles(self, q):
        st = self.eng.store
        rows = (_rows(st, 'SELECT * FROM res_tile_stats WHERE "window" = ? ORDER BY lane', [q.get("window", "all")])
                if st.table_exists("res_tile_stats") else [])
        self._send(200, {"schema": "self_aware_tiles_v1", "window": q.get("window", "all"), "tiles": rows,
                         "provenance": self._prov("tile_stats")})

    def receipts(self, q):
        doc = self.eng.docs.get("receipts")
        self._send(200 if doc else 503, doc or {"error": "receipts not computed yet"})

    def digest(self, q):
        n = min(int(q.get("history", 0) or 0), 200)
        latest = self.eng.docs.get("digest") or (self.eng.store.history("digests", limit=1) or [None])[0]
        body = {"schema": "self_aware_digest_v1", "latest": latest}
        if n:
            body["history"] = self.eng.store.history("digests", limit=n)
        self._send(200, body)

    def repairs(self, q):
        self._send(200, {"journal": tail_jsonl(self.eng.paths.journal, min(int(q.get("limit", 50)), 500)),
                         "policy": "AUTO = run the owner's existing scheduled task early (cooldown 15 min, cap 6/day); "
                                   "FLAG = trading/relay/Bitfinex, never executed"})

    def tables(self, q):
        self._send(200, self.eng.store.catalog())

    def query(self, q):
        self._query(q.get("sql", ""), q.get("max_rows"))

    def _query(self, sql: str, max_rows: Any) -> None:
        try:
            res = self.eng.store.query(sql, max_rows=int(max_rows or 1000))
        except QueryRejected as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._send(400, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        self._send(200, res)

    def index(self, q):
        self._send(200, render_overview(self.eng), "text/html")


ROUTES = {
    "/": Handler.index, "/api/ping": Handler.ping, "/api/selfaware/health": Handler.health,
    "/api/selfaware/findings/history": Handler.findings_history, "/api/selfaware/uptime": Handler.uptime,
    "/api/selfaware/ai/calls": Handler.ai_calls, "/api/selfaware/ai/scorecard": Handler.ai_scorecard,
    "/api/selfaware/edges": Handler.edges, "/api/selfaware/edges/playbook": Handler.playbook,
    "/api/selfaware/tiles": Handler.tiles, "/api/selfaware/receipts": Handler.receipts,
    "/api/selfaware/digest": Handler.digest, "/api/selfaware/repairs": Handler.repairs,
    "/api/selfaware/tables": Handler.tables, "/api/selfaware/query": Handler.query,
}

_COLOR = {"RED": "#e5484d", "AMBER": "#f5a524", "GREEN": "#30a46c", "SKIP": "#8b8d98", None: "#8b8d98"}


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def render_overview(eng) -> str:
    h = eng.docs.get("health") or {}
    d = eng.docs.get("digest") or (eng.store.history("digests", limit=1) or [{}])[0]
    up = eng.docs.get("uptime") or {}
    ai = ai_headline(eng.store) if eng.store.table_exists("res_ai_scorecard") else {}
    edges = (eng.store.read("SELECT spec_id, horizon, status, holdout_n, holdout_hit, holdout_net_bp, bh_q, reasons FROM res_edges "
                            "WHERE status IN ('CANDIDATE','HINT','WATCH') ORDER BY status, holdout_net_bp DESC LIMIT 12")
             if eng.store.table_exists("res_edges") else [])
    v = h.get("verdict")
    rows = []
    for f in h.get("findings") or []:
        if f["severity"] == "GREEN":
            continue
        causes = "; ".join(c.get("text", "") for c in (f.get("causes") or [])[:2])
        rows.append(f"<tr><td><b style='color:{_COLOR[f['severity']]}'>{_e(f['severity'])}</b></td><td>{_e(f['id'])}</td>"
                    f"<td>{_e(f['observed'])}<div class=m>{_e(causes)}</div></td>"
                    f"<td><a href='{_e(f.get('runbook_url'))}'>runbook</a></td></tr>")
    greens = sum(1 for f in h.get("findings") or [] if f["severity"] == "GREEN")
    ai_rows = []
    for hz, strat in ((ai.get("all") or {}).items()):
        for s, r in strat.items():
            ai_rows.append(f"<tr><td>{_e(hz)}</td><td>{_e(s)}</td><td>{_e(r.get('n'))}</td>"
                           f"<td>{(r.get('hit_rate') or 0):.1%}</td><td>{(r.get('net_bp') or 0):+.2f}</td>"
                           f"<td>[{(r.get('net_lo') or 0):+.1f}, {(r.get('net_hi') or 0):+.1f}]</td></tr>")
    edge_rows = [f"<tr><td>{_e(r['status'])}</td><td>{_e(r['spec_id'])}@{_e(r['horizon'])}</td><td>{_e(r['holdout_n'])}</td>"
                 f"<td>{(r['holdout_hit'] or 0):.1%}</td><td>{(r['holdout_net_bp'] or 0):+.2f}</td><td>{(r['bh_q'] or 1):.3f}</td>"
                 f"<td class=m>{_e(r['reasons'])}</td></tr>" for r in edges]
    run = up.get("running_uninterrupted_sec")
    return f"""<!doctype html><html><head><meta charset=utf-8><meta http-equiv=refresh content=60>
<title>Self-aware · {_e(v)}</title><style>
body{{font:14px system-ui;background:#111113;color:#edeef0;margin:24px;max-width:1200px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 24px}}td,th{{border-bottom:1px solid #2e3035;padding:6px;text-align:left;vertical-align:top}}
.m{{color:#a0a1a7;font-size:12px}}a{{color:#7cb7ff}}h2{{margin-top:28px}}.pill{{padding:2px 10px;border-radius:10px;color:#111}}
</style></head><body>
<h1>Self-aware <span class=pill style='background:{_COLOR.get(v, "#8b8d98")}'>{_e(v or "warming up")}</span></h1>
<div class=m>generated {_e(h.get('generated_at'))} · Fly {_e((h.get('fly') or {}).get('git_rev', '')[:9])}
 · paused={_e((h.get('fly') or {}).get('paused'))} · watcher {_e(h.get('watcher_verdict'))}
 · engine rev {_e(eng.store.revision[:9])} · <a href='/api/selfaware/health'>health JSON</a> · <a href='/api/selfaware/tables'>tables</a></div>
<h2>Hourly digest</h2><p><b>{_e(d.get('headline'))}</b><br><span class=m>{_e(d.get('summary_line'))}</span></p>
<h2>Self-diagnosis ({len(rows)} open, {greens} green)</h2>
<table><tr><th>sev</th><th>check</th><th>observed · probable cause</th><th></th></tr>{''.join(rows) or '<tr><td colspan=4>all green</td></tr>'}</table>
<h2>Uptime</h2><p>running uninterrupted {(run or 0) / 3600:.1f} h · interruptions 24h {_e(up.get('interruptions_24h'))}
 · proof {_e((up.get('proof') or {}).get('hours_elapsed'))}/48 h {_e((up.get('proof') or {}).get('result'))}</p>
<h2>AI scorecard (all-time, after cost)</h2><table><tr><th>horizon</th><th>strategy</th><th>n</th><th>hit</th><th>net bp</th><th>95% CI</th></tr>{''.join(ai_rows)}</table>
<h2>Edges (pre-registered, walk-forward holdout)</h2><table><tr><th>status</th><th>screen</th><th>n</th><th>hit</th><th>net bp</th><th>BH q</th><th>why</th></tr>{''.join(edge_rows) or '<tr><td colspan=7>no candidate, hint or watch</td></tr>'}</table>
<p class=m>Drill-down: <code>/api/selfaware/query?sql=SELECT …</code> over raw_* views and res_* tables. Refreshed {time.strftime('%H:%M:%S')}.</p>
</body></html>"""
