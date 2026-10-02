"""Section contracts: registry validation, evaluator verdicts, drift, archive drift, coverage and the HTTP routes."""
from __future__ import annotations

import gzip
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import contracts as ct  # noqa: E402
from self_aware import diagnose  # noqa: E402
from self_aware.config import Paths  # noqa: E402
from self_aware.store import Store  # noqa: E402

ROSTER = ["FAMILY_A", "FAMILY_B"]


@pytest.fixture()
def paths(tmp_path: Path) -> Paths:
    p = Paths(home=tmp_path / "home", chain=tmp_path / "chain", mirror=tmp_path / "mirror",
              mirror_archive=tmp_path / "archive-mirror", puller=tmp_path / "puller", exports=tmp_path / "exports",
              archive=tmp_path / "analysis-archive", diagnostics=tmp_path / "diag", analyzer_repo=tmp_path / "v2c",
              retention=tmp_path / "retention")
    for d in (p.home, p.chain, p.exports):
        d.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture()
def store(paths: Paths):
    s = Store(paths, path=paths.home / "t.duckdb", threads=1, memory_limit="256MB")
    yield s
    s.close()


def _spec(**over) -> dict:
    base = {"id": "analyzer.demo", "surface": "analyzer", "title": "Demo", "tier": "light", "depends_on": ["x"],
            "source": {"kind": "http", "url": "http://127.0.0.1:1/none"}}
    base.update(over)
    return base


def _ctx(**over) -> dict:
    c = {"now": time.time(), "roster": ROSTER, "retired": {"FAMILY_OLD"}, "docs": {}, "facts": {}, "store": None,
         "genome_grid": None}
    c.update(over)
    return c


def _eval(spec: dict, obj, **ctx):
    return ct.evaluate(spec, obj, {"code": 200, "url": "u"}, _ctx(**ctx))


def _kinds(res: dict) -> set[str]:
    return {v["kind"] for v in res["violations"] if v["severity"] in ("RED", "AMBER")}


# ------------------------------------------------------------------ registry

def test_shipped_registry_is_valid_and_covers_every_surface():
    reg = ct.load_registry()
    ids = [s["id"] for s in reg["contracts"]]
    assert len(ids) == len(set(ids)) and len(ids) >= 60
    assert {s["surface"] for s in reg["contracts"]} >= {"analyzer", "fly", "exports", "selfaware", "watcher"}
    assert reg["registry_hash"]


def test_registry_rejects_duplicates_and_unknown_reconciler():
    with pytest.raises(ValueError):
        ct.validate_registry({"contracts": [_spec(), _spec()]})
    with pytest.raises(ValueError):
        ct.validate_registry({"contracts": [_spec(reconcile="no_such")]})
    with pytest.raises(ValueError):
        ct.validate_registry({"contracts": [_spec(tier="sometimes")]})


def test_get_path_dotted_index_and_len():
    obj = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert ct.get_path(obj, "a.b.1.c") == 2
    assert ct.get_path(obj, "#a.b") == 2
    assert ct.get_path(obj, "a.x") is ct.MISSING


# ------------------------------------------------------------------ evaluator

def test_silent_empty_is_red_declared_empty_is_amber():
    spec = _spec(tables=[{"path": "rows", "min_rows": 1, "declared_empty_paths": ["empty_reason"]}])
    assert _eval(spec, {"rows": []})["status"] == "RED"
    res = _eval(spec, {"rows": [], "empty_reason": "NO_DATA_YET"})
    assert res["status"] == "AMBER" and "EMPTY_DECLARED" in _kinds(res)


def test_dead_and_constant_columns():
    rows = [{"lane": "FAMILY_A", "pnl": 0, "ev": None, "mode": "DIRECT"} for _ in range(6)]
    spec = _spec(tables=[{"path": "rows", "live_columns": ["pnl", "ev", "mode"]}])
    res = _eval(spec, {"rows": rows})
    details = " ".join(v["detail"] for v in res["violations"])
    assert {"DEAD_COLUMN", "CONSTANT_COLUMN"} <= _kinds(res)
    assert "DEAD_ZERO" in details and "DEAD_NULL" in details
    spec["tables"][0]["allow_constant"] = ["mode"]
    assert "CONSTANT_COLUMN" not in _kinds(_eval(spec, {"rows": rows}))


def test_genome_collapse_is_detected():
    """The Top-100 collapse: rows only vary by ADX/spread/lane, no entry/exit genes."""
    rows = [{"adx": a, "directional_spread": 1, "entry_mode": "DIRECT", "lane": "FAMILY_A"} for a in range(5)]
    spec = _spec(tables=[{"path": "rows", "min_distinct": {"entry_mode": 2},
                          "genes": {"entry_offset": ["entry_offset"], "stop": ["stop_pct", "sl_"], "adx": ["adx"]}}])
    res = _eval(spec, {"rows": rows})
    assert res["status"] == "RED" and "DIMENSION_COLLAPSE" in _kinds(res)
    assert res["metrics"]["genes:rows"] == 1
    full = [{"adx": a, "entry_offset_pct": 0.3, "stop_pct": 1, "entry_mode": m, "lane": "FAMILY_A"}
            for a, m in ((1, "DIRECT"), (2, "CHASE"))]
    assert _eval(spec, {"rows": full})["status"] == "GREEN"


def test_roster_mismatch_and_retired_lane():
    spec = _spec(tables=[{"path": "rows", "roster_field": "lane", "roster": "exact", "no_retired": True}])
    assert _eval(spec, {"rows": [{"lane": "FAMILY_A"}, {"lane": "FAMILY_B"}]})["status"] == "GREEN"
    res = _eval(spec, {"rows": [{"lane": "FAMILY_A"}, {"lane": "FAMILY_OLD"}]})
    assert {"ROSTER_MISMATCH", "RETIRED_LANE_PRESENT"} <= _kinds(res)
    dict_spec = _spec(rosters=[{"path": "by_lane", "roster": "superset"}])
    assert "ROSTER_MISMATCH" in _kinds(_eval(dict_spec, {"by_lane": {"FAMILY_A": {}}}))


def test_invariant_counter_ratio_expect():
    spec = _spec(invariants=[{"if_positive": "evaluated", "then_min": "materialized", "kind": "GENOME_EMPTY"}],
                 counters=[{"path": "n", "min": 10}], ratios=[{"num": "exact", "den": "total", "min": 0.5}],
                 expect=[{"path": "state", "equals": "OK"}])
    res = _eval(spec, {"evaluated": 21280, "materialized": 0, "n": 3, "exact": 0, "total": 100, "state": "BAD"})
    assert {"GENOME_EMPTY", "COUNTER_LOW", "LOW_COVERAGE", "UNEXPECTED_VALUE"} <= _kinds(res)
    ok = _eval(spec, {"evaluated": 5, "materialized": 5, "n": 30, "exact": 90, "total": 100, "state": "OK"})
    assert ok["status"] == "GREEN"


def test_label_contradiction():
    spec = _spec(status_path="status", tables=[{"path": "rows", "min_rows": 1, "empty_silent_severity": "AMBER"}])
    assert "LABEL_CONTRADICTION" in _kinds(_eval(spec, {"status": "OK", "rows": []}))


def test_stale_and_http_failures():
    spec = _spec(freshness={"path": "generated_at", "max_age_sec": 60})
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    assert _eval(spec, {"generated_at": old})["status"] == "RED"
    res = ct.evaluate(_spec(), ct.MISSING, {"code": 500, "error": "boom", "url": "u"}, _ctx())
    assert res["status"] == "RED"
    allowed = ct.evaluate(_spec(allow_http={"503": "AMBER"}), {"ok": False}, {"code": 503, "url": "u"}, _ctx())
    assert allowed["status"] == "AMBER"
    not_yet = ct.evaluate(_spec(allow_http={"404": "AMBER"}), ct.MISSING, {"code": 404, "not_json": True, "url": "u"}, _ctx())
    assert not_yet["status"] == "AMBER"
    fetched = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3570))
    cached = ct.evaluate(spec, {"generated_at": old}, {"code": 200, "url": "u", "cached_at": fetched}, _ctx())
    assert cached["status"] == "GREEN"


# ------------------------------------------------------------------ drift

def test_drift_collapse_and_dims_dropped():
    spec = _spec()
    hist = [{"id": spec["id"], "metrics": {"rows:top": 100}, "dims": {"columns:top": ["a", "b", "c"]}} for _ in range(4)]
    res = {"status": "GREEN", "metrics": {"rows:top": 10}, "dims": {"columns:top": ["a"]}, "violations": []}
    kinds = {v["kind"] for v in ct.drift(spec, res, hist)}
    assert kinds == {"DRIFT_COLLAPSE", "DRIFT_DIMS_DROPPED"}
    steady = {"status": "GREEN", "metrics": {"rows:top": 95}, "dims": {"columns:top": ["a", "b", "c"]}, "violations": []}
    assert ct.drift(spec, steady, hist) == []


def test_archive_drift_flags_vanished_and_shrunk_reports(paths):
    gen = paths.archive / "generations" / "2026-10-02"
    for i, (n, extra) in enumerate(((50, True), (50, True), (2, False))):
        rd = gen / f"20261002T0{i}0000Z" / "reports"
        rd.mkdir(parents=True)
        with gzip.open(rd / "combos.json.gz", "wt", encoding="utf-8") as fh:
            json.dump({"rows": [{"a": j, "b": j} for j in range(n)]}, fh)
        if extra:
            with gzip.open(rd / "gone.json.gz", "wt", encoding="utf-8") as fh:
                json.dump({"rows": [{"x": 1}]}, fh)
    out = ct.archive_drift(paths, {})
    kinds = {(f["report"], f["kind"]) for f in out["findings"]}
    assert ("gone", "REPORT_DISAPPEARED") in {(r.split(".")[0], k) for r, k in kinds}
    assert any(k == "REPORT_LIST_COLLAPSE" for _, k in kinds)


def test_coverage_reads_nav_groups_from_dashboard_source(paths):
    rd = paths.analyzer_repo / "services" / "btc-conservative-agent" / "research"
    rd.mkdir(parents=True)
    (rd / "research_dashboard.py").write_text(
        'REPORT_NAV_GROUPS = [("core", "Core", [("lanes", "Lanes", "x.json"), ("combos", "Combos", "y.json")])]\n'
        'DECISION_NAV_LINKS = [("Alerts", "/alerts")]\n', encoding="utf-8")
    reg = {"contracts": [_spec(covers=["details#lanes", "page:/alerts"])]}
    cov = ct.coverage(paths.analyzer_repo, reg)
    assert cov["sections"] == 3 and [u["section"] for u in cov["uncovered"]] == ["details#combos"]


# ------------------------------------------------------------------ run / diagnose

def test_run_publishes_history_and_summary_feeds_findings(paths, store):
    (paths.exports / "summary.json").write_text(json.dumps({"generated_at": ct.iso(time.time()), "rows": []}), encoding="utf-8")
    reg = ct.validate_registry({"contracts": [
        _spec(id="exports.demo", surface="exports", source={"kind": "file", "path": "{exports}/summary.json"},
              tables=[{"path": "rows", "min_rows": 1}]),
        _spec(id="analyzer.ok", source={"kind": "file", "path": "{exports}/summary.json"}, tier="heavy")]})
    state: dict = {}
    doc = ct.run(store, paths, {"runtime": {"active_tile_lanes": ROSTER}}, state, {}, tier="heavy", registry=reg)
    assert doc["counts"]["RED"] == 1 and doc["surfaces"]["exports"] == "RED"
    assert len(store.history(ct.HISTORY_TABLE, limit=10, kind="CONTRACT")) == 2
    light = ct.run(store, paths, {"runtime": {}}, state, {}, tier="light", registry=reg)
    assert light["evaluated"] == 1 and len(light["contracts"]) == 2
    summ = ct.summary(doc)
    found = {f.id: f for f in diagnose.check_contracts({"now": time.time(), "contracts": summ}, {}, store)}
    assert found["contract.exports"].severity == "RED" and found["contract.analyzer"].severity == "GREEN"
    assert found["contract.collapse"].severity == "RED"
    assert {f.severity for f in diagnose.check_contracts({"now": time.time(), "contracts": None}, {}, store)} == {"SKIP"}


def test_fly_fetch_is_capped_and_cached(paths, monkeypatch):
    calls = []

    def fake_http(url, max_bytes, timeout=20):
        calls.append(url)
        return {"ok": True}, {"code": 200, "url": url}

    monkeypatch.setattr(ct, "_http", fake_http)
    f = ct.Fetcher(paths, {})
    for i in range(ct.FLY_MAX_CALLS_PER_RUN + 3):
        f.get({"kind": "fly", "path": f"/api/x{i}"})
    assert len(calls) == ct.FLY_MAX_CALLS_PER_RUN
    g = ct.Fetcher(paths, {})
    obj, meta = g.get({"kind": "fly", "path": "/api/x0"})
    assert obj == {"ok": True} and len(calls) == ct.FLY_MAX_CALLS_PER_RUN


# ------------------------------------------------------------------ server

def test_contract_routes(paths, store):
    from self_aware.server import make_server  # noqa: PLC0415

    class Eng:
        def __init__(self):
            self.paths, self.store = paths, store
            spec = ct.load_registry()["contracts"][0]
            self.docs = {"contracts": {"schema": ct.SCHEMA, "generated_at": ct.iso(time.time()), "tier": "light",
                                       "registry_hash": "h", "contracts_total": 1, "evaluated": 1,
                                       "counts": {"RED": 0, "AMBER": 1, "GREEN": 0, "SKIP": 0}, "surfaces": {"analyzer": "AMBER"},
                                       "fly_calls": 0, "ms": 1, "heavy_at": None,
                                       "contracts": [{"id": spec["id"], "surface": "analyzer", "title": "t", "status": "AMBER",
                                                      "violations": [{"kind": "STALE", "severity": "AMBER", "detail": "d"}],
                                                      "metrics": {}}]}}
            self.spec_id = spec["id"]

        def engine_status(self):
            return {}

    eng = Eng()
    srv = make_server(eng, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        body = json.loads(urllib.request.urlopen(base + "/api/selfaware/contracts?status=AMBER", timeout=10).read())
        assert body["returned"] == 1
        reg = json.loads(urllib.request.urlopen(base + "/api/selfaware/contracts/registry", timeout=10).read())
        assert reg["contracts"] and "EMPTY_SILENT" in reg["violation_kinds"]
        det = json.loads(urllib.request.urlopen(base + f"/api/selfaware/contracts/{eng.spec_id}?rows=0", timeout=10).read())
        assert det["spec"]["id"] == eng.spec_id and det["last"]["status"] == "AMBER"
        html = urllib.request.urlopen(base + "/contracts", timeout=10).read().decode()
        assert "Section contracts" in html and eng.spec_id in html
    finally:
        srv.shutdown()
