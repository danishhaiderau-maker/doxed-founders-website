"""Self-aware daemon: one scheduler, one store, one HTTP server (127.0.0.1:9021).

Jobs run sequentially on one background thread at BELOW_NORMAL priority with
DuckDB capped at 2 threads / 768 MB, so the analyzer cycle and watcher keep
their CPU. Every job result lands in the store with provenance; the API and
the Alerts feed read those same results (compute once, never disagree).

Usage:
    python -m self_aware.engine            # daemon (server + scheduler)
    python -m self_aware.engine --once     # one full pass, print summary, exit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from typing import Any, Callable

import pandas as pd

from . import ai_scorecard, alarms, analyzer_sections, data_awareness, diagnose, digest, edges, repair, tiles, uptime
from .ai_scorecard import json_safe
from .config import ALARM_PREFIX, CADENCE_SEC, SCHEMA_VERSION, SERVER_PORT, Paths
from .facts import collect, iso, parse_ts
from .store import Store

HISTORY_KEEP_DAYS = {"findings_events": 30, "digests": 30, "uptime_history": 14, "runtime_history": 7}


def _below_normal() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes  # noqa: PLC0415
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)
    except Exception:  # noqa: BLE001
        pass


class Engine:
    def __init__(self, paths: Paths | None = None, *, store: Store | None = None, probe_local: bool = True,
                 repair_enabled: bool = True, emit_alarms: bool = True) -> None:
        self.paths = paths or Paths()
        self.paths.check()
        self.paths.home.mkdir(parents=True, exist_ok=True)
        self.store = store or Store(self.paths)
        self.probe_local = probe_local
        self.repair_enabled = repair_enabled
        self.emit_alarms = emit_alarms
        self.state: dict[str, Any] = self._load_state()
        self.facts: dict[str, Any] = {}
        self.docs: dict[str, Any] = {}
        self.started_at = time.time()
        self._stop = threading.Event()
        self.jobs: dict[str, Callable[[], Any]] = {
            "views": self.job_views, "diagnose": self.job_diagnose, "uptime": self.job_uptime,
            "tiles": self.job_tiles, "ai": self.job_ai, "edges": self.job_edges, "digest": self.job_digest,
            "data": self.job_data, "sections": self.job_sections,
        }

    # ------------------------------------------------------------ state
    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.paths.state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_state(self) -> None:
        tmp = self.paths.state.with_suffix(".tmp")
        tmp.write_text(json.dumps(json_safe(self.state), indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.paths.state)

    def _emit(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.emit_alarms:
            return {"written": 0, "pending": 0, "disabled": True}
        return alarms.flush(self.paths.health, self.state, events, wait_sec=45)

    # ------------------------------------------------------------- jobs
    def job_views(self) -> dict:
        res = self.store.refresh_views()
        errors = {r["view"]: str(r.get("error") or r["status"])[:200] for r in res if r["status"].startswith("ERROR")}
        self.state["view_errors"] = errors
        return {"status": "DEGRADED" if errors else "OK", "views": len(res),
                "missing": [r["view"] for r in res if r["status"] == "MISSING"], "errors": sorted(errors)}

    def job_diagnose(self) -> dict:
        now = time.time()
        self.facts = collect(self.paths, self.store, now, probe_local=self.probe_local)
        self.facts["data_awareness"] = (self.docs.get("data") or {}).get("summary")
        self.facts["analyzer_sections"] = self.docs.get("sections") or self.state.get("analyzer_sections_doc")
        found = diagnose.run(self.paths, self.store, self.facts, self.state)
        changes = diagnose.transitions(found, self.state, now)
        evidence = diagnose.preserve_evidence(self.paths, changes, self.facts)
        findings = [f.to_dict() for f in found]
        repairs = repair.run(self.paths, self.facts, findings, self.state, enabled=self.repair_enabled)
        alarm_changes = changes
        if not self.emit_alarms:
            self.state["alarm_baseline_at"] = None
        elif not self.state.get("alarm_baseline_at"):
            # Transitions seen while alarms were off never reached Alerts: publish what is open now, once.
            alarm_changes = [c for c in changes if c["kind"] != "CLEARED"] + [
                {"at": iso(now), "kind": "OPENED", "id": fd["id"], "from": "GREEN", "to": fd["severity"], "finding": fd}
                for fd in findings if fd["severity"] in ("AMBER", "RED") and fd["id"] not in {c["id"] for c in changes}]
            self.state["alarm_baseline_at"] = now
        flush = self._emit(alarms.events_for(alarm_changes, now))
        verdict = diagnose.verdict(found)
        sig = diagnose.signals(self.facts)
        rt = self.facts.get("runtime") or {}
        doc = {
            "schema": "self_aware_health_v1", "generated_at": iso(now), "verdict": verdict,
            "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in ("RED", "AMBER", "GREEN", "SKIP")},
            "findings": sorted(findings, key=lambda f: ({"RED": 0, "AMBER": 1, "SKIP": 2, "GREEN": 3}[f["severity"]], f["id"])),
            "signals": sig, "transitions": changes, "evidence_written": evidence, "repairs": repairs,
            "alarm_flush": flush, "watcher_verdict": (self.facts.get("watcher") or {}).get("verdict"),
            "fly": {"git_rev": rt.get("git_rev"), "paused": rt.get("execution_paused"), "pause_owner": rt.get("pause_owner"),
                    "active_tile_lanes": rt.get("active_tile_lanes"), "live_armed": rt.get("live_armed"),
                    "snapshot_at": rt.get("observedAt")},
            "engine": self.engine_status(),
        }
        self.docs["health"] = json_safe(doc)
        frame = pd.DataFrame([{k: (json.dumps(json_safe(v)) if isinstance(v, (dict, list)) else v)
                               for k, v in f.items()} for f in findings])
        self.store.publish("findings", frame, sources=["laptop-chain snapshots", "raw views", "local probes"],
                           window=(iso(now), iso(now)), note=f"verdict={verdict}")
        self.store.append("findings_events", changes, sources=["res_findings"])
        self.store.append("runtime_history", [{"at": iso(now), "kind": "HEALTH", "id": iso(now), "verdict": verdict,
                                               "counts": doc["counts"], "fly": doc["fly"]}], sources=["res_findings"])
        return {"verdict": verdict, "transitions": len(changes), "repairs": len(repairs), "alarms": flush}

    def _need_facts(self) -> None:
        if not self.facts:
            self.facts = collect(self.paths, self.store, probe_local=False)

    def job_uptime(self) -> dict:
        self._need_facts()
        doc = uptime.compute(self.store, self.facts, self.state)
        self.docs["uptime"] = json_safe(doc)
        self.store.append("uptime_history", [{**doc, "at": doc["generated_at"], "kind": "UPTIME", "id": doc["generated_at"]}],
                          sources=["raw_verdicts", "fly_runtime_snapshot_v1.json"])
        return {"running_sec": doc["running_uninterrupted_sec"], "interruptions_24h": doc["interruptions_24h"]}

    def job_tiles(self) -> dict:
        self._need_facts()
        frame = tiles.tile_stats(self.store, self.facts)
        if not frame.empty:
            self.store.publish("tile_stats", frame, sources=["raw_execution", "fly_runtime_snapshot_v1.json"],
                               note="net_pnl_usd summed per fill_id from the V3 execution ledger")
        doc = tiles.receipts(self.store, self.facts)
        self.docs["receipts"] = json_safe(doc)
        return {"tile_rows": int(len(frame)), "deploys": len(doc["deploys"])}

    def job_data(self) -> dict:
        self._need_facts()
        up = self.docs.get("uptime") or {}
        intervals = [(parse_ts(i["start"]), parse_ts(i["end"]), i["cause"]) for i in up.get("interruptions_7d") or []]
        # A Fly restart or deploy pause can drop a few seconds either side of the watcher's tick boundaries.
        intervals = [(s - 120, e + 120, c) for s, e, c in intervals if s and e]
        res = data_awareness.run(self.store, self.paths, self.facts, self.state, intervals)
        self.docs["data"] = json_safe(res)
        s = res["summary"]
        return {"streams": s["streams"], "uncatalogued": s["uncatalogued"], "watch_alarms": sum(len(v) for v in
                s["watch_alarms"].values()), "ms": s["ms"],
                "sufficiency": {q["id"]: q["status"] for q in s["sufficiency"]}}

    def job_sections(self) -> dict:
        doc = json_safe(analyzer_sections.run(self.paths, self.state))
        self.docs["sections"] = doc
        # Kept in state so the 2-hourly result survives a daemon restart instead of reading SKIP until the next run.
        self.state["analyzer_sections_doc"] = doc
        d = doc["dashboard"]
        return {"verdict": d.get("verdict"), "counts": d.get("counts"), "error": d.get("error"),
                "genome_grid_age_sec": doc["genome_grid"].get("age_sec"), "shrank": len(doc["shrank"])}

    def job_ai(self) -> dict:
        return ai_scorecard.run(self.store)

    def job_edges(self) -> dict:
        res = edges.run(self.store)
        if self.store.table_exists("res_edges"):
            cands = {f"{r['spec_id']}@{r['horizon']}": r for r in self.store.read(
                "SELECT spec_id, horizon, holdout_n, holdout_hit, holdout_net_bp, bh_q FROM res_edges WHERE status='CANDIDATE'")}
            prev = set(self.state.get("edge_candidates") or [])
            now = time.time()
            events = []
            for key in sorted(set(cands) - prev):
                r = cands[key]
                events.append(alarms._event("AMBER", ALARM_PREFIX + "edge_candidate", "AMBER", {
                    "observed": f"edge candidate {key}: holdout n={r['holdout_n']} hit={r['holdout_hit']:.1%} "
                                f"net={r['holdout_net_bp']:+.1f}bp q={r['bh_q']:.3f} (shadow only until user approval)",
                    "expected": "pre-registered screen passed walk-forward holdout, BH/Holm, min N and decay guards",
                    "runbook": "docs/SELF_AWARE_RUNBOOK.md#edge-candidate"}, now, None))
            if prev and not cands:
                events.append(alarms._event("AMBER_CLEAR", ALARM_PREFIX + "edge_candidate", "GREEN", {
                    "observed": "no edge candidate passes the guards any more", "expected": "",
                    "runbook": "docs/SELF_AWARE_RUNBOOK.md#edge-candidate"}, now, None))
            self.state["edge_candidates"] = sorted(cands)
            res["alarms"] = self._emit(events) if events else None
        return res

    def job_digest(self) -> dict:
        self._need_facts()
        health = self.docs.get("health") or {}
        up = self.docs.get("uptime") or {}
        doc = digest.build(self.store, self.facts, health.get("findings") or [], up, self.state)
        doc = json_safe(doc)
        self.docs["digest"] = doc
        self.store.append("digests", [doc], sources=["res_findings_events", "raw_alarms", "res_edges", "res_ai_scorecard",
                                                     "uptime"])
        active = bool(self.state.get("digest_alarm_active"))
        ev = alarms.digest_event(doc, active, time.time())
        self.state["digest_alarm_active"] = bool(doc.get("attention"))
        flush = self._emit([ev]) if ev else None
        for name, days in HISTORY_KEEP_DAYS.items():
            self.store.prune_history(name, days)
        return {"attention": doc["attention"], "headline": doc["headline"], "alarm": flush}

    # ---------------------------------------------------------- schedule
    def engine_status(self) -> dict:
        jobs = self.state.get("jobs") or {}
        return {"schema_version": SCHEMA_VERSION, "code_revision": self.store.revision, "pid": os.getpid(),
                "started_at": iso(self.started_at), "store": str(self.store.path),
                "store_mb": round(self.store.path.stat().st_size / 1e6, 1) if self.store.path.exists() else None,
                "jobs": jobs, "cadence_sec": CADENCE_SEC, "repair_enabled": self.repair_enabled,
                "pending_alarm_events": len(self.state.get("pending_alarm_events") or [])}

    def run_job(self, name: str) -> dict:
        started = time.time()
        js = self.state.setdefault("jobs", {}).setdefault(name, {})
        errs = self.state.setdefault("job_errors", {})
        try:
            result = self.jobs[name]()
            js.update(last_ok=iso(time.time()), result=json_safe(result))
            errs[name] = None
        except Exception as exc:  # noqa: BLE001 - a failed job is a finding, never a crash
            result = {"error": f"{type(exc).__name__}: {exc}"}
            errs[name] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "at": iso(time.time()),
                          "trace": traceback.format_exc()[-1500:]}
            print(f"[self-aware] job {name} failed: {exc}", file=sys.stderr, flush=True)
        js.update(last_run=iso(started), last_run_epoch=started, ms=int((time.time() - started) * 1000))
        self.save_state()
        return result

    def due(self, name: str, now: float) -> bool:
        last = ((self.state.get("jobs") or {}).get(name) or {}).get("last_run_epoch") or 0
        return now - last >= CADENCE_SEC[name]

    def run_once(self) -> dict:
        return {name: self.run_job(name) for name in ("views", "diagnose", "uptime", "tiles", "data", "sections", "ai",
                                                      "edges", "diagnose", "digest")}

    def loop(self) -> None:
        # Views and the cheap in-memory documents are rebuilt at start so no endpoint answers 503 after a restart.
        for name in ("views", "diagnose", "uptime", "tiles", "data", "diagnose"):
            self.run_job(name)
        while not self._stop.is_set():
            now = time.time()
            for name in self.jobs:
                if self._stop.is_set():
                    break
                if self.due(name, now):
                    self.run_job(name)
            self._stop.wait(10)

    def stop(self) -> None:
        self._stop.set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="run every job once and exit")
    ap.add_argument("--no-alarms", action="store_true", help="do not append to laptop-chain/health/alarms.jsonl")
    ap.add_argument("--no-repair", action="store_true", help="journal repair decisions without executing them")
    ap.add_argument("--port", type=int, default=SERVER_PORT)
    args = ap.parse_args(argv)
    _below_normal()
    if args.once:
        eng = Engine(emit_alarms=not args.no_alarms, repair_enabled=not args.no_repair)
        print(json.dumps(json_safe(eng.run_once()), indent=1, default=str))
        return 0
    from .server import make_server  # noqa: PLC0415
    try:
        httpd = make_server(None, args.port)
    except OSError as exc:
        print(f"[self-aware] port {args.port} busy ({exc}); another instance is running", flush=True)
        return 0
    # Serve before the engine loads so the keeper's ping sees "starting", never a hang, during a slow store open.
    threading.Thread(target=httpd.serve_forever, name="self-aware-http", daemon=True).start()
    eng = Engine(emit_alarms=not args.no_alarms, repair_enabled=not args.no_repair)
    httpd.engine = eng
    print(f"[self-aware] serving http://127.0.0.1:{args.port} pid={os.getpid()} rev={eng.store.revision[:12]}", flush=True)
    try:
        eng.loop()
    finally:
        eng.stop()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
