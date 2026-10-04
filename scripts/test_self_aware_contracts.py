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

pytest.importorskip("duckdb")
pytest.importorskip("pandas")

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


class _CohortFetch:
    def __init__(self, cohort: dict):
        self.summary = {"ledger_reconciliation": {"analyzer_cohort": cohort}}

    def get(self, source):
        return self.summary, {}


def test_accumulator_reconciles_in_usd_against_the_ledger():
    cohort = {"FAMILY_A": {"n": 25, "net_pnl_usd": -0.188739}}
    ctx = _ctx(fetch=_CohortFetch(cohort))
    legacy = {"by_lane": {"FAMILY_A": {"n": 26, "pnl": 2.49}}}
    viol, _ = ct._rec_accumulator_vs_cohort(legacy, ctx)
    assert {"UNIT_UNLABELLED", "UNIT_OR_SIGN_MISMATCH"} <= {v["kind"] for v in viol}

    lane = {"FAMILY_A": {"accumulator_n": 26, "ledger_n": 26, "accumulator_net_pnl_usd": -0.220617,
                         "ledger_net_pnl_usd": -0.220617, "status": "MATCH"}}
    fixed = {"pnl_unit": "USD", "by_lane": {"FAMILY_A": {"n": 26, "net_pnl_usd": -0.220617, "pnl": -0.220617}},
             "ledger_reconciliation": {"status": "MATCH", "mismatched_lanes": [], "lanes": lane}}
    viol, met = ct._rec_accumulator_vs_cohort(fixed, ctx)
    assert viol == [] and met["accumulator:ledger_reconciliation"] == "MATCH"

    fixed["ledger_reconciliation"] = {"status": "MISMATCH", "mismatched_lanes": ["FAMILY_A"],
                                      "lanes": {"FAMILY_A": {**lane["FAMILY_A"], "ledger_net_pnl_usd": -0.46}}}
    assert [v["kind"] for v in ct._rec_accumulator_vs_cohort(fixed, ctx)[0]] == ["RECONCILE_MISMATCH"]


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


def test_fly_chase_buckets_dead_vs_not_applicable():
    spec = _spec(id="fly.chase_buckets", surface="fly", reconcile="fly_chase_buckets")
    assert _eval(spec, {})["status"] == "GREEN"
    dead = _eval(spec, {"chase_analytics": {"status": "UNAVAILABLE", "reason": "NO_VALIDATED_ANALYZER_BUNDLE"}})
    assert dead["status"] == "RED" and "DEAD_SECTION" in _kinds(dead)
    assert _eval(spec, {"chase_analytics": {"status": "NOT_APPLICABLE"}})["status"] == "GREEN"


class _GenerationFetch:
    def __init__(self, completed_at: str):
        self.summary = {"generation": {"analyzer_completed_at": completed_at}}

    def get(self, source):
        return self.summary, {}


def test_fly_analyzer_mirror_must_serve_the_current_laptop_generation():
    def iso(offset_sec: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - offset_sec))

    panel = {"mode": "external_desktop_analyzer", "ok": False, "endpoint": "summary", "mirror_available": True,
             "mirror_status": {"analyzer_generated_at": iso(3000)}}
    dead = ct._rec_fly_analyzer_mirror({**panel, "mirror_available": False}, _ctx(fetch=_GenerationFetch(iso(60))))
    assert [v["kind"] for v in dead[0]] == ["DEAD_SECTION"]
    same = ct._rec_fly_analyzer_mirror(panel, _ctx(fetch=_GenerationFetch(panel["mirror_status"]["analyzer_generated_at"])))
    assert same[0] == [] and same[1]["mirror_matches_local_generation"] is True
    # A newer laptop generation inside the publish grace window is not yet a failure.
    assert ct._rec_fly_analyzer_mirror(panel, _ctx(fetch=_GenerationFetch(iso(300))))[0] == []
    lagging = ct._rec_fly_analyzer_mirror(panel, _ctx(fetch=_GenerationFetch(iso(2400))))[0]
    assert [(v["kind"], v["severity"]) for v in lagging] == [("STALE", "AMBER")]
    ancient = {**panel, "mirror_status": {"analyzer_generated_at": iso(5 * 3600)}}
    assert [v["severity"] for v in ct._rec_fly_analyzer_mirror(ancient, _ctx(fetch=_GenerationFetch(iso(2400))))[0]] == ["RED"]


def test_fill_model_headline_requires_realistic_v1():
    spec = _spec(id="analyzer.genome_grid_fill_model", reconcile="fill_model_headline")
    good = {"fill_model": {"fill_model": "REALISTIC_V1", "shadow_fill_model": "OPTIMISTIC_TOUCH_V1"},
            "headline_fill_world": "REALISTIC_V1",
            "rows": [{"fill_world": "REALISTIC_V1", "fill_model": "REALISTIC_V1", "fill_model_role": "HEADLINE"},
                     {"fill_world": "OPTIMISTIC_TOUCH_SHADOW", "fill_model": "OPTIMISTIC_TOUCH_V1",
                      "fill_model_role": "COMPARISON_SHADOW_NOT_HEADLINE"}]}
    assert _eval(spec, good)["status"] == "GREEN"
    legacy = _eval(spec, {"rows": [{"fill_world": "IDEAL_TOUCH"}]})
    assert legacy["status"] == "RED" and "FILL_MODEL_UNDECLARED" in _kinds(legacy)
    swapped = _eval(spec, {**good, "headline_fill_world": "OPTIMISTIC_TOUCH_SHADOW"})
    assert swapped["status"] == "RED" and "FILL_MODEL_OPTIMISTIC_HEADLINE" in _kinds(swapped)
    mislabelled = dict(good, rows=[{"fill_world": "IDEAL_TOUCH", "fill_model": "OPTIMISTIC_TOUCH_V1", "fill_model_role": "HEADLINE"}])
    assert "FILL_MODEL_OPTIMISTIC_HEADLINE" in _kinds(_eval(spec, mislabelled))


def test_fill_model_undeclared_severity_is_per_contract_but_optimistic_is_always_red():
    spec = _spec(id="fly.fill_model", surface="fly", reconcile="fill_model_headline",
                 fill_model_undeclared_severity="AMBER", fill_model_pending="ships post-freeze")
    pending = _eval(spec, {"trades": []})
    assert pending["status"] == "AMBER" and "ships post-freeze" in pending["violations"][0]["detail"]
    assert _eval(spec, {"fill_model": "OPTIMISTIC_TOUCH_V1"})["status"] == "RED"
    assert _eval(spec, {"fill_model": {"fill_model": "REALISTIC_V1"}})["status"] == "GREEN"


def test_edges_fill_model_reads_published_rows(store):
    import pandas as pd
    spec = _spec(id="selfaware.edges_fill_model", surface="selfaware", reconcile="edges_fill_model")
    assert _eval(spec, {"ok": 1}, store=store)["status"] == "GREEN"
    store.publish("edges", pd.DataFrame({"edge": ["A"], "holdout_hit": [0.55]}), sources=["t"])
    legacy = _eval(spec, {"ok": 1}, store=store)
    assert legacy["status"] == "RED" and "FILL_MODEL_UNDECLARED" in _kinds(legacy)
    store.publish("edges", pd.DataFrame({"edge": ["A", "B"], "fill_model": ["REALISTIC_V1", "REALISTIC_V1"]}), sources=["t"])
    res = _eval(spec, {"ok": 1}, store=store)
    assert res["status"] == "GREEN" and res["metrics"]["edges:REALISTIC_V1"] == 2


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


def test_drift_baseline_is_the_current_epoch_and_tile_registry(paths):
    spec = _spec()
    old = {"epoch_id": "ce-20261004-v31-final", "tile_registry_signature": "11tiles"}
    cur = {"epoch_id": "ce-20261004-v31-final-e", "tile_registry_signature": "8tiles"}
    pre = [{"id": spec["id"], "metrics": {"rows:top": 13}, "dims": {"active_tiles:keys": ["FAMILY_XVENUE_LEAD_60S", "A"]},
            "baseline": old} for _ in range(8)]
    unstamped = [{"id": spec["id"], "metrics": {"rows:top": 13}, "dims": {"active_tiles:keys": ["X", "A"]}}] * 3
    res = {"status": "GREEN", "metrics": {"rows:top": 0}, "dims": {"active_tiles:keys": ["A"]}, "violations": []}
    assert ct.drift(spec, res, pre + unstamped, cur) == []
    same = [{"id": spec["id"], "metrics": {"rows:top": 10}, "dims": {"active_tiles:keys": ["A", "B"]}, "baseline": cur}] * 3
    kinds = {v["kind"] for v in ct.drift(spec, res, same + pre, cur)}
    assert kinds == {"DRIFT_COLLAPSE", "DRIFT_DIMS_DROPPED"}
    (paths.mirror / "data_epoch.json").parent.mkdir(parents=True, exist_ok=True)
    (paths.mirror / "data_epoch.json").write_text(json.dumps({"epoch_id": cur["epoch_id"], "started_at_ts": 1.0}))
    assert ct.baseline_identity(paths, {"runtime": {"tile_registry_signature": "8tiles"}}) == cur


def test_archive_drift_ignores_snapshots_before_the_epoch(paths):
    gen = paths.archive / "generations" / "2026-10-04"
    for stamp, n in (("20261004T000000Z", 50), ("20261004T010000Z", 50), ("20261004T020000Z", 0), ("20261004T030000Z", 0)):
        rd = gen / stamp / "reports"
        rd.mkdir(parents=True)
        with gzip.open(rd / "archive.json.gz", "wt", encoding="utf-8") as fh:
            json.dump({"tiles_long_horizon": [{"a": j} for j in range(n)]}, fh)
    assert any(f["kind"] == "REPORT_LIST_COLLAPSE" for f in ct.archive_drift(paths, {})["findings"])
    epoch_start = ct._snapshot_ts("20261004T014018Z")
    out = ct.archive_drift(paths, {}, since_ts=epoch_start)
    assert out["findings"] == [] and out["snapshots"]["generations"] == ["20261004T020000Z", "20261004T030000Z"]


def test_tile_stats_count_only_epoch_closes_and_list_every_roster_lane():
    import pandas as pd

    from self_aware import tiles

    class _Store:
        def frame(self, _sql):
            return pd.DataFrame({"lane": ["FAMILY_XVENUE_LEAD_60S", "FAMILY_DANISH_CF"], "fill_id": ["f1", "f2"],
                                 "close_ts": ["2026-10-04T00:30:00Z", "2026-10-04T02:30:00Z"], "net_usd": [1.0, -0.5],
                                 "exit_reason": ["TP", "SL"], "revision": ["r", "r"]})

    facts = {"runtime": {"active_tile_lanes": ["FAMILY_DANISH_CF", "FAMILY_NOTRADE_FOLLOW_MAKER_60"],
                         "research_lane_enabled": {"FAMILY_DANISH_CF": True, "FAMILY_NOTRADE_FOLLOW_MAKER_60": True}}}
    start = ct._snapshot_ts("20261004T014018Z")
    df = tiles.tile_stats(_Store(), facts, now=start + 7200, epoch_start_ts=start)
    assert set(df["lane"]) == {"FAMILY_DANISH_CF", "FAMILY_NOTRADE_FOLLOW_MAKER_60"}
    quiet = df[df["lane"] == "FAMILY_NOTRADE_FOLLOW_MAKER_60"].iloc[0]
    assert quiet["closes"] == 0 and quiet["in_roster"]
    assert df[(df["lane"] == "FAMILY_DANISH_CF") & (df["window"] == "all")].iloc[0]["closes"] == 1
    legacy = tiles.tile_stats(_Store(), facts, now=start + 7200)
    assert "FAMILY_XVENUE_LEAD_60S" in set(legacy["lane"])


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
    state["jobs"] = {"contracts": {"last_ok": "2099-01-01T00:00:00Z"}}  # a deferred heavy job still records last_ok
    light = ct.run(store, paths, {"runtime": {}}, state, {}, tier="light", registry=reg)
    assert light["evaluated"] == 1 and len(light["contracts"]) == 2
    assert light["heavy_at"] == doc["heavy_at"] == state["contracts_heavy_at"]
    assert ct.run(store, paths, {"runtime": {}}, {}, {}, tier="light", registry=reg)["heavy_at"] is None
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


def test_declared_blocker_downgrades_only_matching_violations_until_expiry():
    blocker = {"id": "PENDING_FIX", "kinds": ["EMPTY_SILENT"], "severity": "AMBER", "reason": "known upstream defect",
               "fix": "deploy X", "eta": "2026-10-04T15:00Z", "expires": "2026-10-06T00:00:00Z",
               "when": {"path": "status", "in": ["BLOCKED"]}}
    spec = _spec(tables=[{"path": "rows", "min_rows": 1}], declared_blockers=[blocker])
    ct.validate_registry({"contracts": [spec]})
    now = ct.parse_ts("2026-10-03T22:00:00Z")
    res = _eval(spec, {"rows": [], "status": "BLOCKED"}, now=now)
    assert res["status"] == "AMBER" and res["declared_blockers"][0]["id"] == "PENDING_FIX"
    hit = [v for v in res["violations"] if v.get("declared_blocker")]
    assert hit and hit[0]["severity_undeclared"] == "RED" and "ETA 2026-10-04T15:00Z" in hit[0]["detail"]
    assert _eval(spec, {"rows": [], "status": "OK"}, now=now)["status"] == "RED"
    late = _eval(spec, {"rows": [], "status": "BLOCKED"}, now=ct.parse_ts("2026-10-06T01:00:00Z"))
    assert late["status"] == "RED" and "DECLARED_BLOCKER_EXPIRED" in _kinds(late)
    with pytest.raises(ValueError):
        ct.validate_registry({"contracts": [_spec(declared_blockers=[dict(blocker, severity="GREEN")])]})
    with pytest.raises(ValueError):
        ct.validate_registry({"contracts": [_spec(declared_blockers=[{"id": "x"}])]})


# ------------------------------------------------------------------ fresh-epoch warmup / export offenders

def test_empty_analyzer_table_is_warmup_info_inside_a_fresh_epoch_and_red_after():
    spec = _spec(tables=[{"path": "rows", "min_rows": 3}], expect=[{"path": "status", "in": ["OK"]}])
    warm = _eval(spec, {"rows": [1], "status": "UNKNOWN"}, epoch_age_sec=3600, epoch_id="ce-x")
    assert warm["status"] == "GREEN" and {v["kind"] for v in warm["violations"]} == {"EMPTY_WARMUP", "EXPECT_WARMUP"}
    assert "ce-x" in warm["violations"][0]["detail"]
    late = _eval(spec, {"rows": [1], "status": "UNKNOWN"}, epoch_age_sec=ct.EPOCH_WARMUP_SEC + 1, epoch_id="ce-x")
    assert late["status"] == "RED" and "EMPTY_SILENT" in _kinds(late)
    assert "EMPTY_SILENT" in _kinds(_eval(spec, {"rows": []}))  # epoch age unknown: no warmup
    # Non-epoch surfaces, opted-out contracts and real wrong statuses keep their severity.
    assert "EMPTY_SILENT" in _kinds(_eval({**spec, "surface": "fly"}, {"rows": []}, epoch_age_sec=60))
    assert "EMPTY_SILENT" in _kinds(_eval({**spec, "warmup_sec": 0}, {"rows": []}, epoch_age_sec=60))
    assert "UNEXPECTED_VALUE" in _kinds(_eval(spec, {"rows": [1, 2, 3], "status": "BROKEN"}, epoch_age_sec=60))


def test_export_offenders_are_listed_under_the_exports_surface(store):
    summ = {"generated_at": ct.iso(time.time()), "heavy_at": ct.iso(time.time()), "tier": "heavy",
            "counts": {}, "surfaces": {"exports": "RED"}, "contracts_total": 1, "registry_hash": "h",
            "offenders": [{"id": "export.summary", "surface": "exports", "status": "RED", "why": "rows: 0 < 1"}],
            "collapse": [], "declared_blockers": [], "coverage": {"sections": 1, "covered": 1}, "uncovered": []}
    found = {f.id: f for f in diagnose.check_contracts({"now": time.time(), "contracts": summ}, {}, store)}
    assert found["contract.exports"].severity == "RED" and "export.summary" in found["contract.exports"].observed
    legacy = {**summ, "offenders": [{"id": "export.summary", "status": "RED", "why": "x"}]}
    found = {f.id: f for f in diagnose.check_contracts({"now": time.time(), "contracts": legacy}, {}, store)}
    assert "export.summary" in found["contract.exports"].observed
    none_listed = {**summ, "offenders": []}
    found = {f.id: f for f in diagnose.check_contracts({"now": time.time(), "contracts": none_listed}, {}, store)}
    assert "GREEN" not in found["contract.exports"].observed


def test_archive_drift_treats_a_defect_list_going_to_zero_as_recovery(paths):
    gen = paths.archive / "generations" / "2026-10-04"
    for i, n in enumerate((6, 5, 6, 0)):
        rd = gen / f"20261004T0{i}0000Z" / "reports"
        rd.mkdir(parents=True)
        with gzip.open(rd / "genome.json.gz", "wt", encoding="utf-8") as fh:
            json.dump({"collection": {"integrity": {"orphan_expected_orders": [{"o": j} for j in range(n)],
                                                    "fills": [{"f": j} for j in range(n)]}}}, fh)
    paths_hit = {f["path"] for f in ct.archive_drift(paths, {})["findings"] if f["kind"] == "REPORT_LIST_COLLAPSE"}
    assert paths_hit == {"collection.integrity.fills"}
