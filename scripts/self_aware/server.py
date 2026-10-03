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
  /api/selfaware/changes            ?since=ISO&kind=DEPLOY,PAUSE,...&limit=  deploys, fast-forwards, manual
                                    interventions, Fly pause/revision/tile/arm transitions, AI model/prompt, epochs
  /api/selfaware/digest             ?history=N
  /api/selfaware/repairs            ?limit=
  /api/selfaware/tables             raw views + result tables + provenance
  /api/selfaware/query?sql=SELECT…  guarded single SELECT (also POST {"sql": …})
  /data                             HTML data-health view
  /api/selfaware/data               data-awareness summary (freshness, completeness, dead fields, capacity, sufficiency)
  /api/selfaware/data/catalog       ?stream=&catalogued=1   every collected stream
  /api/selfaware/data/completeness  ?stream=                hourly expected vs actual + gaps
  /api/selfaware/data/fields        ?stream=&status=DEAD_ZERO,DEAD_NULL,CONSTANT&watched=1
  /api/selfaware/data/capacity
  /api/selfaware/data/sufficiency
  /api/selfaware/sections           :9001 section health (populated, fresh, dimensions, consistency)
  /api/selfaware/fly-platform       Fly.io status page events classified vs our region/components + correlation
  /contracts                        HTML section-contract view
  /api/selfaware/contracts          ?surface=analyzer|fly|exports|selfaware|watcher&status=RED,AMBER
  /api/selfaware/contracts/registry declarative specs (section_contracts.json) + violation legend
  /api/selfaware/contracts/<id>     ?rows=N&history=N   spec, last result, history, raw rows fetched now
  /api/selfaware/data/compatibility ?stream=&severity=   data versions / clean epoch per stream, schema drift,
                                    dead-in-version fields, segregated partitions + manual delete command
  /api/selfaware/fees               ?detail=1  Bitfinex account fee truth (auth/r/summary, 6 h cache) vs
                                    bitfinex_cost_profile and every fee surface; selfaware.fees.truth
"""
from __future__ import annotations

import html
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import changes, contracts
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
        u = urlparse(self.path)
        if self.eng is None:
            if u.path.rstrip("/") == "/api/ping":
                return self._send(200, {"ok": True, "service": "self_aware", "starting": True})
            return self._send(503, {"error": "engine starting"})
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        path = u.path.rstrip("/") or "/"
        try:
            route = ROUTES.get(path)
            if route:
                return route(self, q)
            if path.startswith("/api/selfaware/ai/calls/"):
                return self.ai_call(unquote(path.rsplit("/", 1)[1]))
            if path.startswith("/api/selfaware/contracts/"):
                return self.contract_detail(unquote(path.rsplit("/", 1)[1]), q)
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
                                    ("CANDIDATE", "HINT", "WATCH", "REJECTED", "INSUFFICIENT", "QUEUED_DATA")},
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

    def changes(self, q):
        self._send(200, changes.timeline(self.eng.store, self.eng.docs.get("receipts"), self.eng.paths.mirror,
                                         since=q.get("since"), kinds=q.get("kind"), limit=int(q.get("limit", 200))))

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

    # ------------------------------------------------------------ data awareness
    def _data(self):
        doc = self.eng.docs.get("data")
        if not doc:
            self._send(503, {"error": "data awareness not computed yet (job runs at start and every 30 min)"})
        return doc

    def data_summary(self, q):
        doc = self._data()
        if doc:
            self._send(200, {**doc["summary"], "provenance": self._prov("data_catalog", "data_fields", "data_sufficiency")})

    def data_catalog(self, q):
        doc = self._data()
        if not doc:
            return
        streams = doc["streams"]
        if q.get("stream"):
            streams = [s for s in streams if s["stream"] == q["stream"] or s.get("path") == q["stream"]]
        elif q.get("catalogued") in ("1", "true"):
            streams = [s for s in streams if s.get("catalogued")]
        slim = [{k: v for k, v in s.items() if k not in ("hourly", "gaps", "exact")} for s in streams]
        if q.get("stream") and streams:
            slim = streams
        self._send(200, {"schema": "self_aware_data_catalog_v1", "generated_at": doc["summary"]["generated_at"],
                         "mirror_head": doc["summary"]["mirror_head"], "streams": slim,
                         "provenance": self._prov("data_catalog")})

    def data_completeness(self, q):
        doc = self._data()
        if not doc:
            return
        out = []
        for s in doc["streams"]:
            if q.get("stream") and s["stream"] != q["stream"]:
                continue
            if not q.get("stream") and not s.get("critical"):
                continue
            out.append({"stream": s["stream"], "cadence": s.get("cadence"), "status": s.get("status"),
                        "expected_per_hour": s.get("expected_per_hour"), "observed_per_hour": s.get("observed_per_hour"),
                        "sample_span_h": s.get("sample_span_h"), "lag_vs_mirror_head_sec": s.get("lag_vs_mirror_head_sec"),
                        "exact": s.get("exact"), "hourly": s.get("hourly"), "gaps": s.get("gaps")})
        self._send(200, {"schema": "self_aware_data_completeness_v1", "generated_at": doc["summary"]["generated_at"],
                         "streams": out, "note": "critical streams by default; ?stream=<name> for any stream"})

    def data_fields(self, q):
        st = self.eng.store
        if not st.table_exists("res_data_fields"):
            return self._send(503, {"error": "field liveness not computed yet"})
        where, params = [], []
        if q.get("stream"):
            where.append("stream = ?")
            params.append(q["stream"])
        if q.get("status"):
            sts = [s.strip().upper() for s in q["status"].split(",") if s.strip()]
            where.append(f"status IN ({','.join('?' * len(sts))})")
            params.extend(sts)
        if q.get("watched") in ("1", "true"):
            where.append("watched")
        sql = ('SELECT stream, field, n, null_pct, "distinct", zero_pct, constant_value, status, watched, alarm '
               'FROM res_data_fields ' + (("WHERE " + " AND ".join(where)) if where else "") +
               " ORDER BY alarm DESC, stream, field LIMIT ?")
        rows = _rows(st, sql, params + [min(int(q.get("limit", 2000)), 20000)])
        self._send(200, {"schema": "self_aware_data_fields_v1", "fields": rows,
                         "status_legend": {"DEAD_NULL": "always null/empty", "DEAD_ZERO": "numeric measure always 0",
                                           "CONSTANT": "one value across >= 50 rows", "MISSING": "watched field absent"},
                         "provenance": self._prov("data_fields")})

    def data_capacity(self, q):
        doc = self._data()
        if doc:
            self._send(200, {"schema": "self_aware_data_capacity_v1", **doc["capacity"]})

    def data_sufficiency(self, q):
        doc = self._data()
        if doc:
            self._send(200, {"schema": "self_aware_data_sufficiency_v1", "questions": doc["sufficiency"],
                             "status_legend": {"READY": "fields alive and enough independent samples; gated screens released",
                                               "ACCUMULATING": "fields alive, samples short; see eta_ready",
                                               "BLOCKED": "a required field is missing, dead or constant"},
                             "provenance": self._prov("data_sufficiency")})

    def sections(self, q):
        doc = self.eng.docs.get("sections") or self.eng.state.get("analyzer_sections_doc")
        self._send(200 if doc else 503, doc or {"error": "section check not run yet (every 2 h)"})

    def fly_platform(self, q):
        doc = (self.eng.docs.get("health") or {}).get("fly_platform")
        self._send(200 if doc else 503, doc or {"error": "fly platform status not evaluated yet (every diagnose pass)"})

    def fees(self, q):
        doc = self.eng.docs.get("fees") or (getattr(self.eng, "state", None) or {}).get("fees_doc")
        if not doc:
            return self._send(503, {"error": "fee truth not computed yet (job runs every 30 min)"})
        if q.get("detail") != "1":
            doc = {k: v for k, v in doc.items() if k not in ("surfaces", "fee_literals", "raw_fee_block")}
        self._send(200, doc)

    def data_compat(self, q):
        doc = self.eng.docs.get("compat")
        if not doc:
            return self._send(503, {"error": "compatibility scan not computed yet (job runs every 30 min)"})
        if q.get("stream"):
            rows = [s for s in doc["streams"] if s["stream"] == q["stream"]]
            return self._send(200 if rows else 404, {"schema": doc["schema"], "generated_at": doc["generated_at"],
                                                     "epoch": doc["epoch"], "streams": rows})
        streams = doc["streams"]
        if q.get("severity"):
            want = {s.strip().upper() for s in q["severity"].split(",")}
            streams = [s for s in streams if s["severity"] in want]
        self._send(200, {**{k: v for k, v in doc.items() if k != "streams"}, "streams": streams})

    def data_view(self, q):
        self._send(200, render_data(self.eng), "text/html")

    # ------------------------------------------------------------ section contracts
    def contracts_summary(self, q):
        doc = self.eng.docs.get("contracts")
        if not doc:
            return self._send(503, {"error": "section contracts not evaluated yet (light pass at start, heavy every 2 h)"})
        rows = doc["contracts"]
        if q.get("surface"):
            rows = [r for r in rows if r["surface"] == q["surface"]]
        if q.get("status"):
            want = {s.strip().upper() for s in q["status"].split(",") if s.strip()}
            rows = [r for r in rows if r["status"] in want]
        self._send(200, {**{k: v for k, v in doc.items() if k != "contracts"}, "returned": len(rows), "contracts": rows,
                         "detail": "/api/selfaware/contracts/<id>?rows=N&history=N drills to the raw rows behind a section",
                         "registry": "/api/selfaware/contracts/registry", "provenance": self._prov(contracts.HISTORY_TABLE)})

    def contracts_registry(self, q):
        reg = contracts.load_registry()
        self._send(200, {"schema": "self_aware_contract_registry_v1", "registry_hash": reg["registry_hash"],
                         "file": str(contracts.REGISTRY_FILE.name), "contracts": reg["contracts"],
                         "violation_kinds": contracts.VIOLATION_HELP})

    def contract_detail(self, cid: str, q):
        reg = contracts.load_registry()
        spec = next((s for s in reg["contracts"] if s["id"] == cid), None)
        if spec is None:
            return self._send(404, {"error": "unknown contract", "ids": [s["id"] for s in reg["contracts"]]})
        last = next((r for r in (self.eng.docs.get("contracts") or {}).get("contracts") or [] if r["id"] == cid), None)
        body: dict[str, Any] = {"schema": "self_aware_contract_detail_v1", "spec": spec, "last": last}
        n = min(int(q.get("history", 24) or 0), 500)
        if n:
            body["history"] = self.eng.store.history(contracts.HISTORY_TABLE, limit=n, kind="CONTRACT", id=cid)
        rows = min(int(q.get("rows", 50) or 0), 1000)
        if rows:
            body["raw"] = contracts.drill(spec, contracts.Fetcher(self.eng.paths, self.eng.docs), rows)
        self._send(200, body)

    def contracts_view(self, q):
        self._send(200, render_contracts(self.eng), "text/html")


ROUTES = {
    "/": Handler.index, "/api/ping": Handler.ping, "/api/selfaware/health": Handler.health,
    "/api/selfaware/findings/history": Handler.findings_history, "/api/selfaware/uptime": Handler.uptime,
    "/api/selfaware/ai/calls": Handler.ai_calls, "/api/selfaware/ai/scorecard": Handler.ai_scorecard,
    "/api/selfaware/edges": Handler.edges, "/api/selfaware/edges/playbook": Handler.playbook,
    "/api/selfaware/tiles": Handler.tiles, "/api/selfaware/receipts": Handler.receipts,
    "/api/selfaware/changes": Handler.changes,
    "/api/selfaware/digest": Handler.digest, "/api/selfaware/repairs": Handler.repairs,
    "/api/selfaware/tables": Handler.tables, "/api/selfaware/query": Handler.query,
    "/data": Handler.data_view, "/api/selfaware/data": Handler.data_summary,
    "/api/selfaware/data/catalog": Handler.data_catalog, "/api/selfaware/data/completeness": Handler.data_completeness,
    "/api/selfaware/data/fields": Handler.data_fields, "/api/selfaware/data/capacity": Handler.data_capacity,
    "/api/selfaware/data/sufficiency": Handler.data_sufficiency, "/api/selfaware/sections": Handler.sections,
    "/api/selfaware/fly-platform": Handler.fly_platform,
    "/contracts": Handler.contracts_view, "/api/selfaware/contracts": Handler.contracts_summary,
    "/api/selfaware/contracts/registry": Handler.contracts_registry,
    "/api/selfaware/data/compatibility": Handler.data_compat, "/api/selfaware/fees": Handler.fees,
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
 · engine rev {_e(eng.store.revision[:9])} · <a href='/api/selfaware/health'>health JSON</a> · <a href='/api/selfaware/tables'>tables</a>
 · <a href='/data'>data health</a> · <a href='/contracts'>section contracts</a></div>
{render_fly_platform(h.get('fly_platform'))}
<h2>Hourly digest</h2><p><b>{_e(d.get('headline'))}</b><br><span class=m>{_e(d.get('summary_line'))}</span></p>
<h2>Self-diagnosis ({len(rows)} open, {greens} green)</h2>
<table><tr><th>sev</th><th>check</th><th>observed · probable cause</th><th></th></tr>{''.join(rows) or '<tr><td colspan=4>all green</td></tr>'}</table>
<h2>Uptime</h2><p>running uninterrupted {(run or 0) / 3600:.1f} h · interruptions 24h {_e(up.get('interruptions_24h'))}
 · proof {_e((up.get('proof') or {}).get('hours_elapsed'))}/48 h {_e((up.get('proof') or {}).get('result'))}</p>
<h2>AI scorecard (all-time, after cost)</h2><table><tr><th>horizon</th><th>strategy</th><th>n</th><th>hit</th><th>net bp</th><th>95% CI</th></tr>{''.join(ai_rows)}</table>
<h2>Edges (pre-registered, walk-forward holdout)</h2><table><tr><th>status</th><th>screen</th><th>n</th><th>hit</th><th>net bp</th><th>BH q</th><th>why</th></tr>{''.join(edge_rows) or '<tr><td colspan=7>no candidate, hint or watch</td></tr>'}</table>
<p class=m>Drill-down: <code>/api/selfaware/query?sql=SELECT …</code> over raw_* views and res_* tables. Refreshed {time.strftime('%H:%M:%S')}.</p>
</body></html>"""

_PLATFORM_COLOR = {"INFO": "#7cb7ff", "NONE": "#30a46c", "UNREACHABLE": "#8b8d98"}


def render_fly_platform(fp: Any) -> str:
    if not fp:
        return "<p class=m>Fly platform: not evaluated yet</p>"
    cls = fp.get("classification")
    events = "".join(
        f"<li><b style='color:{_COLOR.get(e.get('level')) if e.get('level') != 'INFO' else _PLATFORM_COLOR['INFO']}'>"
        f"{_e(e.get('level'))}</b> {_e(e.get('kind'))}: <a href='{_e(e.get('url'))}'>{_e(e.get('title'))}</a>"
        f" <span class=m>{_e(', '.join(e.get('matched') or []) or 'not our region/components')}"
        f"{' · starts ' + _e(e.get('starts_at')) if e.get('kind') == 'scheduled' else ''}</span></li>"
        for e in fp.get("events") or [])
    return (f"<h2>Fly platform <span class=pill style='background:{_PLATFORM_COLOR.get(cls) or _COLOR.get(cls, '#8b8d98')}'>"
            f"{_e(cls)}</span></h2><p>{_e(fp.get('summary'))}<br><span class=m>{_e(fp.get('app'))} region "
            f"{_e(str(fp.get('region') or '').upper())} · feed {_e(fp.get('source'))} fetched {_e(fp.get('fetched_at'))}"
            f" · <a href='{_e(fp.get('status_page'))}'>status page</a> · <a href='/api/selfaware/fly-platform'>JSON</a></span></p>"
            f"{'<ul>' + events + '</ul>' if events else ''}")


def render_contracts(eng) -> str:
    doc = eng.docs.get("contracts")
    if not doc:
        return "<!doctype html><meta http-equiv=refresh content=20><body style='font:14px system-ui'>section contracts warming up…"
    rows = []
    for r in doc["contracts"]:
        bad = [v for v in r["violations"] if v["severity"] in ("RED", "AMBER")]
        info = [v for v in r["violations"] if v["severity"] not in ("RED", "AMBER")]
        rows.append(
            f"<tr><td><b style='color:{_COLOR.get(r['status'], '#8b8d98')}'>{_e(r['status'])}</b></td>"
            f"<td><a href='/api/selfaware/contracts/{_e(r['id'])}?rows=50'>{_e(r['id'])}</a><div class=m>{_e(r['title'])}</div></td>"
            f"<td>{_e(r['surface'])}</td>"
            f"<td>{'<br>'.join(_e(v['kind'] + ': ' + v['detail']) for v in bad) or '—'}"
            f"{('<div class=m>' + _e('; '.join(v['kind'] for v in info)) + '</div>') if info else ''}</td>"
            f"<td class=m>{_e(', '.join(f'{k}={v}' for k, v in list((r.get('metrics') or {}).items())[:6]))}</td></tr>")
    ad = doc.get("archive_drift") or {}
    adrows = "".join(f"<tr><td><b style='color:{_COLOR.get(f['severity'])}'>{_e(f['severity'])}</b></td><td>{_e(f['series'])}</td>"
                     f"<td>{_e(f['report'])}</td><td>{_e(f['kind'])}: {_e(f['detail'])}</td></tr>" for f in ad.get("findings") or [])
    cov = doc.get("coverage") or {}
    counts = " · ".join(f"<b style='color:{_COLOR[s]}'>{s} {n}</b>" for s, n in doc["counts"].items())
    surf = " · ".join(f"{_e(k)} <b style='color:{_COLOR.get(v)}'>{_e(v)}</b>" for k, v in doc["surfaces"].items())
    return f"""<!doctype html><html><head><meta charset=utf-8><meta http-equiv=refresh content=120>
<title>Section contracts</title><style>
body{{font:14px system-ui;background:#111113;color:#edeef0;margin:24px;max-width:1400px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 24px}}td,th{{border-bottom:1px solid #2e3035;padding:6px;text-align:left;vertical-align:top}}
.m{{color:#a0a1a7;font-size:12px}}a{{color:#7cb7ff}}h2{{margin-top:28px}}
</style></head><body>
<h1>Section contracts</h1><div class=m>{doc['contracts_total']} contracts · last {_e(doc['tier'])} pass {_e(doc['generated_at'])}
 · heavy {_e(doc.get('heavy_at'))} · registry {_e(doc['registry_hash'][:12])} · <a href='/'>overview</a>
 · <a href='/api/selfaware/contracts'>JSON</a> · <a href='/api/selfaware/contracts/registry'>registry</a></div>
<p>{counts}</p><p class=m>{surf}</p>
<p class=m>/details coverage: {_e(cov.get('covered'))}/{_e(cov.get('sections'))} sections ·
uncovered: {_e(', '.join(s['section'] for s in cov.get('uncovered') or []) or 'none')}</p>
<h2>Contracts</h2><table><tr><th>status</th><th>section</th><th>surface</th><th>violations</th><th>metrics</th></tr>{''.join(rows)}</table>
<h2>Archive drift (last {len((ad.get('snapshots') or {}).get('generations') or [])} generations)</h2>
<table>{adrows or '<tr><td>no report vanished, shrank or lost columns</td></tr>'}</table>
</body></html>"""


_STATUS_COLOR = {"FRESH": "#30a46c", "READY": "#30a46c", "OK": "#30a46c", "IDLE": "#8b8d98", "ACCUMULATING": "#f5a524",
                 "STALE": "#e5484d", "MISSING": "#e5484d", "BLOCKED": "#e5484d", "NO_TIMESTAMP": "#8b8d98"}


def _mb(b: Any) -> str:
    return "" if b is None else f"{float(b) / 1e6:,.1f} MB"


def render_compat(eng) -> str:
    doc = eng.docs.get("compat")
    if not doc:
        return "<h2>Data compatibility</h2><p class=m>compatibility scan warming up (every 30 min)</p>"
    ep, seg, cov = doc["epoch"], doc["segregated"], doc["coverage"]
    rows = []
    for s in sorted(doc["streams"], key=lambda x: ({"RED": 0, "AMBER": 1, "GREEN": 2}[x["severity"]], -x["segregated_bytes"])):
        if s["severity"] == "GREEN" and not s["segregated_bytes"]:
            continue
        vers = "; ".join(f"{k.split('=', 1)[-1]} {v:,}" for k, v in list(s["versions"].items())[:4])
        rows.append(f"<tr><td><b style='color:{_COLOR.get(s['severity'])}'>{_e(s['severity'])}</b></td>"
                    f"<td><a href='/api/selfaware/data/compatibility?stream={_e(s['stream'])}'>{_e(s['stream'])}</a></td>"
                    f"<td>{s['rows']:,}<div class=m>{_e(vers)}</div></td><td>{_mb(s['segregated_bytes'])}</td>"
                    f"<td class=m>{_e('; '.join(s['problems']))}</td></tr>")
    parts = "".join(f"<li>{_e(k)}: {v['rows']:,} rows · {_mb(v['bytes'])} · {v['streams']} streams</li>"
                    for k, v in seg["partitions"].items())
    cmd = seg["delete_command"]
    return f"""<h2>Data compatibility</h2>
<p>epoch <b>{_e(ep['epoch_id'] or 'none declared')}</b> {_e(ep.get('started_at_utc') or '')} · release {_e(ep.get('release'))}
· index {_e(cov['scanned_pct'])}% of {_mb(cov['bytes_total'])} · {_e(doc['counts'])} · <a href='/api/selfaware/data/compatibility'>JSON</a></p>
<p class=m>{_e(ep.get('note') or '')}</p>
<p><b>Segregated (never merged into current-cohort analysis): {_mb(seg['bytes'])}</b></p><ul>{parts or '<li>nothing segregated</li>'}</ul>
<p class=m>{_e(seg['note'])}. Deletion is manual:</p>
<pre id=wipecmd style='white-space:pre-wrap;background:#1c1d21;padding:8px'>{_e(cmd)}</pre>
<button onclick="navigator.clipboard.writeText(document.getElementById('wipecmd').innerText)">copy delete command</button>
<table><tr><th>sev</th><th>stream</th><th>rows · versions</th><th>segregated</th><th>problems</th></tr>{''.join(rows) or '<tr><td colspan=5>all streams compatible</td></tr>'}</table>"""


def render_data(eng) -> str:
    doc = eng.docs.get("data")
    if not doc:
        return "<!doctype html><meta http-equiv=refresh content=20><body style='font:14px system-ui'>data awareness warming up…"
    s, cap = doc["summary"], doc["capacity"]
    lap, fly = cap.get("laptop") or {}, cap.get("fly") or {}
    tape = s.get("tape") or {}
    fnd = {f["id"]: f for f in ((eng.docs.get("health") or {}).get("findings") or []) if f["id"].startswith("data.")}
    frows = "".join(f"<tr><td><b style='color:{_COLOR.get(f['severity'])}'>{_e(f['severity'])}</b></td><td>{_e(f['id'])}</td>"
                    f"<td>{_e(f['observed'])}</td></tr>" for f in fnd.values())
    qrows = "".join(
        f"<tr><td><b style='color:{_STATUS_COLOR.get(q['status'], '#8b8d98')}'>{_e(q['status'])}</b></td><td>{_e(q['question'])}"
        f"<div class=m>{_e(q['id'])} · screens: {_e(', '.join(q['screens']) or 'none')}"
        f"{' (released)' if q['screens_released'] else ''}</div></td>"
        f"<td>{_e('; '.join(q['blockers'] + q['short_samples']) or '—')}<div class=m>ETA {_e(q.get('eta_ready') or '—')}</div></td>"
        f"<td><b style='color:{_STATUS_COLOR.get(q['full_question_status'], '#8b8d98')}'>{_e(q['full_question_status'])}</b>"
        f"<div class=m>{_e('; '.join(q['missing_for_full_answer']) or 'nothing missing')}</div></td></tr>"
        for q in doc["sufficiency"])
    wa = "".join(f"<tr><td>{_e(k)}</td><td>{_e('; '.join(v))}</td></tr>" for k, v in (s.get("watch_alarms") or {}).items())
    srows = []
    for st in sorted(doc["streams"], key=lambda x: (not x.get("critical"), not x.get("catalogued"), -(x.get("bytes") or 0))):
        srows.append(
            f"<tr><td>{'★ ' if st.get('critical') else ''}<a href='/api/selfaware/data/catalog?stream={_e(st['stream'])}'>{_e(st['stream'])}</a>"
            f"<div class=m>{_e(st.get('what'))}</div></td>"
            f"<td><b style='color:{_STATUS_COLOR.get(st.get('status'), '#8b8d98')}'>{_e(st.get('status'))}</b></td>"
            f"<td>{_e(st.get('cadence'))}<div class=m>exp {_e(st.get('expected_per_hour'))}/h · obs {_e(st.get('observed_per_hour'))}/h</div></td>"
            f"<td>{_e(st.get('last_ts'))}<div class=m>lag {_e(st.get('lag_vs_mirror_head_sec'))} s</div></td>"
            f"<td>{_mb(st.get('bytes'))}<div class=m>{_e(st.get('files'))} file(s) · ~{_e(st.get('rows_estimated'))} rows · "
            f"{_mb(st.get('bytes_per_day'))}/day</div></td>"
            f"<td>{_e(st.get('retention_tier'))}</td><td class=m>{_e(', '.join(st.get('schema') or []))}<br>{_e(st.get('fields'))} fields</td></tr>")
    hours = "".join(f"<span title='{_e(h['hour'])} {h['fill_pct']}%' style='display:inline-block;width:9px;height:22px;margin-right:1px;"
                    f"background:{'#30a46c' if h['fill_pct'] >= 99 else '#f5a524' if h['fill_pct'] >= 95 else '#e5484d'}'></span>"
                    for h in ((next((x for x in doc["streams"] if x["stream"] == "bitfinex_tape_1s"), {}).get("exact") or {})
                              .get("hourly") or []))
    return f"""<!doctype html><html><head><meta charset=utf-8><meta http-equiv=refresh content=120>
<title>Data health</title><style>
body{{font:14px system-ui;background:#111113;color:#edeef0;margin:24px;max-width:1300px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 24px}}td,th{{border-bottom:1px solid #2e3035;padding:6px;text-align:left;vertical-align:top}}
.m{{color:#a0a1a7;font-size:12px}}a{{color:#7cb7ff}}h2{{margin-top:28px}}.k{{display:inline-block;margin-right:28px}}.k b{{font-size:20px}}
</style></head><body>
<h1>Data health</h1><div class=m>generated {_e(s['generated_at'])} · mirror head {_e(s.get('mirror_head'))} · {_e(s['streams'])} streams
({_e(s['catalogued'])} catalogued, {_e(s['uncatalogued'])} auto-discovered) · pass {_e(s['ms'])} ms · <a href='/'>overview</a>
· <a href='/api/selfaware/data'>JSON</a></div>
<h2>Checks</h2><table>{frows or '<tr><td>no data findings yet</td></tr>'}</table>
{render_compat(eng)}
<h2>Capacity</h2>
<div class=k>laptop<br><b>{_e(lap.get('bot_data_gb'))}/{_e(lap.get('cap_gb'))} GB</b><div class=m>{_e(lap.get('usage_pct'))}% · +{_e(lap.get('growth_gb_per_day'))} GB/day · {_e(lap.get('days_to_90pct_cap'))} days to 90%</div></div>
<div class=k>Fly volume<br><b>{_e(fly.get('volume_free_gb'))} GB free</b><div class=m>{_e(fly.get('hours_to_full'))} h to full · segment store {_e(fly.get('segment_store_pct_of_cap'))}% of cap</div></div>
<div class=k>ingest<br><b>{_e(fly.get('ingest_gb_per_day'))} GB/day</b><div class=m>sum of per-stream rates</div></div>
<div class=m>{_e(lap.get('growth_basis'))}</div>
<h2>Bitfinex 1 s tape completeness</h2>
<p>{_e(tape.get('fill_pct_24h_excl_interruptions'))}% of seconds present over 24 h outside Fly interruptions ({_e(tape.get('fill_pct_24h'))}% raw;
{_e(tape.get('gaps_24h'))} gaps &gt;5 s totalling {_e(tape.get('gap_sec_24h'))} s) · window {_e(tape.get('window_start'))} → {_e(tape.get('window_end'))}</p>
<div>{hours}</div><div class=m>one bar per hour, last 48 h (green ≥99%, amber ≥95%)</div>
<h2>Research sufficiency</h2><table><tr><th>screen status</th><th>question</th><th>short / blocked</th><th>full answer</th></tr>{qrows}</table>
<h2>Dead or constant watched fields</h2><table>{wa or '<tr><td>none</td></tr>'}</table>
<p class=m>All fields: <a href='/api/selfaware/data/fields?status=DEAD_ZERO,DEAD_NULL,CONSTANT'>/api/selfaware/data/fields?status=DEAD_ZERO,DEAD_NULL,CONSTANT</a></p>
<h2>Catalog</h2><table><tr><th>stream</th><th>status</th><th>cadence</th><th>last row</th><th>size</th><th>tier</th><th>schema</th></tr>{''.join(srows)}</table>
</body></html>"""
