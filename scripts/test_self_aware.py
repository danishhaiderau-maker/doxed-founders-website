"""Tests for scripts/self_aware: store + guarded query, self-diagnosis, alarms, repair policy, stats and API."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import alarms, data_awareness, diagnose, edges, repair  # noqa: E402
from self_aware import tape as tp  # noqa: E402
from self_aware.ai_scorecard import json_safe, scorecard  # noqa: E402
from self_aware.config import Paths  # noqa: E402
from self_aware.store import QueryRejected, Store  # noqa: E402

NOW = 1_790_930_000.0


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture()
def paths(tmp_path: Path) -> Paths:
    p = Paths(home=tmp_path / "home", chain=tmp_path / "chain", mirror=tmp_path / "mirror",
              mirror_archive=tmp_path / "archive-mirror", puller=tmp_path / "puller", exports=tmp_path / "exports",
              archive=tmp_path / "analysis-archive", diagnostics=tmp_path / "diag", analyzer_repo=tmp_path / "v2c",
              retention=tmp_path / "retention", segment_manifests=tmp_path / "segment-manifests",
              laptop_root=tmp_path)
    (p.chain / "health").mkdir(parents=True)
    return p


@pytest.fixture()
def store(paths: Paths):
    s = Store(paths, path=paths.home / "t.duckdb", threads=1, memory_limit="256MB")
    yield s
    s.close()


# ------------------------------------------------------------------ store

def test_publish_records_provenance(store):
    store.publish("demo", pd.DataFrame({"a": [1, 2]}), sources=["raw_x"], window=("2026-10-01T00:00:00Z", None), note="n")
    prov = store.provenance("demo")
    assert prov["row_count"] == 2 and prov["source_datasets"] == ["raw_x"] and prov["schema_version"] == "self_aware_v1"
    assert prov["code_revision"] and prov["computed_at"]


def test_upsert_replaces_keys_and_adds_columns(store):
    store.upsert("calls", pd.DataFrame({"id": ["a", "b"], "x": [1, 2]}), "id", sources=["s"])
    store.upsert("calls", pd.DataFrame({"id": ["b", "c"], "x": [20, 3], "y": ["new", "new"]}), "id", sources=["s"])
    rows = {r["id"]: r for r in store.read("SELECT * FROM res_calls")}
    assert set(rows) == {"a", "b", "c"} and rows["b"]["x"] == 20 and rows["a"]["y"] is None
    assert store.provenance("calls")["row_count"] == 3


def test_history_tables_round_trip(store):
    store.append("events", [{"at": "2026-10-02T00:00:00Z", "kind": "K", "id": "1", "v": 1},
                            {"at": "2026-10-02T01:00:00Z", "kind": "K", "id": "2", "v": 2}], sources=["s"])
    assert [r["v"] for r in store.history("events", since="2026-10-02T00:30:00Z")] == [2]
    store.prune_history("events", keep_days=0)
    assert store.history("events") == []


def test_raw_views_read_files_in_place(paths, store):
    _jsonl(paths.chain / "health" / "alarms.jsonl", [{"at": "2026-10-02T00:00:00Z", "event": "OPEN", "check": "fly.process"}])
    res = {r["view"]: r for r in store.refresh_views()}
    assert res["raw_alarms"]["status"] == "OK" and res["raw_tape_1s"]["status"] == "MISSING"
    assert store.read('SELECT "check" FROM raw_alarms')[0]["check"] == "fly.process"
    _jsonl(paths.chain / "health" / "alarms.jsonl", [{"at": "x", "event": "OPEN", "check": "a"}, {"at": "y", "event": "OPEN", "check": "b"}])
    store.refresh_views()  # same file set: view kept, new rows visible without a rebuild
    assert store.read("SELECT count(*) AS n FROM raw_alarms")[0]["n"] == 2


def test_csv_views_read_utf8_labels(paths, store):
    paths.mirror.mkdir(parents=True, exist_ok=True)
    body = "".join(f"2026-10-02T00:0{i}:00Z,AI_DECISION,Chandelier \u00b7 1.5 ATR \u2014 \u2713\n" for i in range(5))
    (paths.mirror / "ai_tranche_log.csv").write_text("ts,event,comment\n" + body, encoding="utf-8")
    res = {r["view"]: r for r in store.refresh_views()}
    assert res["raw_ai_tranche"]["status"] == "OK"
    rows = store.read("SELECT count(*) AS n, max(comment) AS c FROM raw_ai_tranche")
    assert rows[0]["n"] == 5 and "\u00b7" in rows[0]["c"]


# ---------------------------------------------------------- guarded query

def test_query_allows_single_select_with_cap(store):
    store.publish("nums", pd.DataFrame({"n": range(50)}), sources=["s"])
    res = store.query("SELECT n FROM res_nums ORDER BY n", max_rows=10)
    assert res["row_count"] == 10 and res["truncated"] and res["rows"][0] == [0]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_text('C:/secrets/.env')",
    "SELECT * FROM read_csv('x')",
    "SELECT * FROM 'C:/Users/x/file.csv'",
    "SELECT getenv('DEEPSEEK_API_KEY')",
    "DROP TABLE provenance",
    "SELECT 1; SELECT 2",
    "COPY provenance TO 'out.csv'",
    "ATTACH 'other.db'",
    "PRAGMA database_list",
    "INSERT INTO provenance VALUES (1)",
    "",
])
def test_query_rejects_file_access_and_writes(store, sql):
    with pytest.raises(QueryRejected):
        store.query(sql)


# ------------------------------------------------------------- diagnosis

def _facts(**over) -> dict:
    f = {"now": NOW, "errors": {}, "runtime": {"observedAt": NOW - 30, "git_rev": "abc123", "strategy_progress": {}},
         "watcher": {"checks": [{"id": "fly.process", "status": "GREEN", "observed": "alive"}], "source_errors": {}},
         "deploys": {"runs": []}, "segment_head": {}, "pull": {}, "analyzer_run": {}, "cycle": {}, "relay": {},
         "autoff": [], "manual": [], "mirror": {}}
    f.update(over)
    return f


def test_fill_close_open_positions_green_overdue_amber(paths, store):
    _jsonl(paths.mirror / "v3" / "ledgers" / "execution.jsonl", [
        {"fill_id": "f1", "research_lane": "L", "fill_ts": str(NOW - 120)},
        {"fill_id": "f2", "research_lane": "L", "fill_ts": str(NOW - 4000), "close_ts": str(NOW - 100), "net_pnl_usd": 0.1},
    ])
    store.refresh_views()
    f = _facts()
    assert diagnose.check_fill_close(f, diagnose.signals(f), store).severity == "GREEN"
    f = _facts(now=NOW + 9 * 3600)
    fd = diagnose.check_fill_close(f, diagnose.signals(f), store)
    assert fd.severity == "AMBER" and "1 overdue" in fd.observed


def test_expired_and_filled_is_red(paths, store):
    _jsonl(paths.mirror / "v3" / "ledgers" / "lifecycle.jsonl", [
        {"record_id": "r1", "fill_id": "f1", "terminal_ttl_expired": True},
        {"record_id": "r2", "fill_id": None, "terminal_ttl_expired": True},
    ])
    store.refresh_views()
    f = _facts()
    fd = diagnose.check_expired_filled(f, diagnose.signals(f), store)
    assert fd.severity == "RED" and fd.causes[0]["cause"] == "fill_expiry_race"


def test_expired_and_traded_in_legacy_ledgers(paths, store):
    _jsonl(paths.mirror / "v3" / "ledgers" / "lifecycle.jsonl", [{"record_id": "r1", "fill_id": None}])
    (paths.mirror / "expired_orders_3factor.csv").write_text(
        f"trade_id,expired_ts,research_lane\nold-1,{NOW - 20 * 3600},L\nnew-1,{NOW - 600},L\nonly-exp,{NOW - 60},L\n")
    (paths.mirror / "trades_3factor.csv").write_text(
        "trade_id,ts,research_lane,exit_reason\nold-1,2026-10-01T18:00:00+00:00,L,X\nok-1,2026-10-02T00:00:00+00:00,L,X\n")
    store.refresh_views()
    f = _facts()
    fd = diagnose.check_expired_filled(f, diagnose.signals(f), store)
    assert fd.severity == "AMBER" and "1 in 24h" in fd.observed and "old-1" in fd.observed
    (paths.mirror / "trades_3factor.csv").write_text(
        "trade_id,ts,research_lane,exit_reason\nold-1,2026-10-01T18:00:00+00:00,L,X\nnew-1,2026-10-02T00:00:00+00:00,L,X\n")
    assert diagnose.check_expired_filled(f, diagnose.signals(f), store).severity == "RED"


def test_ai_cadence_sees_a_stall_when_fly_reports_no_age(paths, store):
    from datetime import datetime, timezone
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    rows = "".join(f"{iso(NOW - 7200 - i * 180)},AI_DECISION\n" for i in range(40))
    paths.mirror.mkdir(parents=True, exist_ok=True)
    (paths.mirror / "ai_tranche_log.csv").write_text("ts,event\n" + rows)
    store.refresh_views()
    f = _facts()
    fd = diagnose.check_ai_cadence(f, diagnose.signals(f), store)
    assert fd.severity == "RED" and "newest mirror AI row" in fd.observed
    rt = {"observedAt": NOW - 30, "git_rev": "abc", "execution_paused": True, "pause_owner": "DEPLOY_MAINTENANCE",
          "strategy_progress": {"ai_age_sec": None, "process_startup_age_sec": 600}}
    f = _facts(runtime=rt)
    assert diagnose.check_ai_cadence(f, diagnose.signals(f), store).severity == "GREEN"
    rt["strategy_progress"]["process_startup_age_sec"] = 3 * 3600
    fd = diagnose.check_ai_cadence(f, diagnose.signals(f), store)
    assert fd.severity == "AMBER" and "DEPLOY_MAINTENANCE" in fd.observed


def test_view_load_errors_degrade_the_engine_check(store):
    f = _facts()
    assert diagnose.check_engine(f, diagnose.signals(f), store, {}).severity == "GREEN"
    fd = diagnose.check_engine(f, diagnose.signals(f), store, {"view_errors": {"raw_ai_tranche": "ERROR: bad"}})
    assert fd.severity == "AMBER" and "raw_ai_tranche" in fd.observed


def test_custody_ack_without_copy_is_red(store):
    f = _facts(segment_head={"shipped_seq": 100}, pull={"ackedSeq": 100, "appliedSeq": 98, "finishedAt": NOW - 30})
    assert diagnose.check_custody(f, diagnose.signals(f), store).severity == "RED"
    f = _facts(segment_head={"shipped_seq": 101}, pull={"ackedSeq": 100, "appliedSeq": 100, "finishedAt": NOW - 30,
                                                        "lastParityAt": NOW - 60})
    assert diagnose.check_custody(f, diagnose.signals(f), store).severity == "GREEN"


def test_custody_one_lock_busy_pull_is_a_deferral_not_a_failure(store):
    base = {"ackedSeq": 100, "appliedSeq": 100, "finishedAt": NOW - 30, "lastParityAt": NOW - 60,
            "error": "LockBusyError: another puller run holds the shadow-root lock", "lastAttemptResult": "LOCK_BUSY"}
    f = _facts(segment_head={"shipped_seq": 101}, pull={**base, "consecutiveFailures": 1})
    fd = diagnose.check_custody(f, diagnose.signals(f), store)
    assert fd.severity == "GREEN" and "deferred" in fd.observed
    f = _facts(segment_head={"shipped_seq": 101}, pull={**base, "consecutiveFailures": 4, "lockHolder": {"pid": 7}})
    fd = diagnose.check_custody(f, diagnose.signals(f), store)
    assert fd.severity == "AMBER" and "4 consecutive" in fd.observed


def test_http_429_is_amber_until_persistent(store):
    state: dict = {}
    w = {"checks": [{"id": "fly.process", "status": "RED", "observed": "HTTP 429 Too Many Requests"}],
         "source_errors": {"fly": "HTTP Error 429"}}
    stale = {"observedAt": NOW - 3600, "git_rev": "abc"}
    f = _facts(watcher=w, runtime=stale)
    assert diagnose.check_fly_reachability(f, diagnose.signals(f), store, state).severity == "AMBER"
    f = _facts(now=NOW + 20 * 60, watcher=w, runtime=stale)
    assert diagnose.check_fly_reachability(f, diagnose.signals(f), store, state).severity == "RED"


def test_revision_parity_flags_stale_analyzer(store):
    f = _facts(analyzer_head="abc123", analyzer_run={"revision": "abc123", "state": "SUCCEEDED"})
    assert diagnose.check_revision_parity(f, diagnose.signals(f), store).severity == "GREEN"
    f = _facts(analyzer_head="def456", analyzer_run={"revision": "def456"})
    assert diagnose.check_revision_parity(f, diagnose.signals(f), store).severity == "AMBER"
    mid_run = _facts(analyzer_head="abc123", analyzer_run={"revision": "abc123", "state": "RUNNING"}, analyzer_dashboard_rev="0ld")
    assert diagnose.check_revision_parity(mid_run, diagnose.signals(mid_run), store).severity == "GREEN"
    after = _facts(analyzer_head="abc123", analyzer_run={"revision": "abc123", "state": "SUCCEEDED"}, analyzer_dashboard_rev="0ld")
    assert diagnose.check_revision_parity(after, diagnose.signals(after), store).severity == "AMBER"
    ahead = _facts(analyzer_head="def456", analyzer_run={"revision": "def456", "state": "RUNNING"}, analyzer_dashboard_rev="abc123",
                   contains_fly={"head": True, "run": True, "dash": True})
    assert diagnose.check_revision_parity(ahead, diagnose.signals(ahead), store).severity == "GREEN"


def test_git_contains_uses_ancestry(tmp_path):
    import subprocess
    from self_aware.facts import git_contains
    repo = tmp_path / "r"
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()  # noqa: E731
    repo.mkdir()
    run("init", "-q")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "a")
    a = run("rev-parse", "HEAD")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "b")
    b = run("rev-parse", "HEAD")
    assert git_contains(repo, a, b) is True and git_contains(repo, b, a) is False and git_contains(repo, a, None) is None


def test_a_crashing_check_becomes_a_finding(paths, store, monkeypatch):
    monkeypatch.setattr(diagnose, "check_feeds", lambda *a: 1 / 0)
    found = diagnose.run(paths, store, _facts(), {})
    assert any(f.id.startswith("self.check_error") and f.severity == "AMBER" for f in found)


def test_transitions_and_alarm_events():
    state: dict = {}
    mk = lambda sev: [diagnose.Finding("inv.x", "t", "invariant", sev, "obs", "exp")]  # noqa: E731
    assert diagnose.transitions(mk("GREEN"), state, NOW) == []
    opened = diagnose.transitions(mk("RED"), state, NOW + 1)
    assert opened[0]["kind"] == "OPENED"
    ev = alarms.events_for(opened, NOW + 1)
    assert [e["event"] for e in ev] == ["OPEN"] and ev[0]["check"] == "selfaware.inv.x"
    assert ev[0]["observed"].startswith("[self-diagnosis] ") and ev[0]["runbook"].endswith("#inv-x")
    cleared = alarms.events_for(diagnose.transitions(mk("GREEN"), state, NOW + 2), NOW + 2)
    assert [e["event"] for e in cleared] == ["RECOVERED"]
    assert diagnose.transitions(mk("SKIP"), state, NOW + 3) == []


def test_alarm_flush_queues_while_watcher_holds_lock(paths):
    health = paths.chain / "health"
    state: dict = {}
    ev = {"check": "selfaware.inv.x", "event": "OPEN", "at": "t"}
    with alarms.TickLock(health / "tick.lock") as owned:
        assert owned
        held = threading.Event()
        out = {}

        def other():
            out.update(alarms.flush(health, state, [ev, {"check": "fly.process", "event": "OPEN"}], wait_sec=0.5))
            held.set()
        t = threading.Thread(target=other)
        t.start()
        t.join(5)
    assert out == {"written": 0, "pending": 2}
    res = alarms.flush(health, state, [], wait_sec=2)
    lines = [json.loads(x) for x in (health / "alarms.jsonl").read_text().splitlines()]
    assert res["pending"] == 0 and [x["check"] for x in lines] == ["selfaware.inv.x"]


def test_digest_alarm_amber_then_clear():
    assert alarms.digest_event({"headline": "h", "attention": True}, False, NOW)["event"] == "AMBER"
    assert alarms.digest_event({"headline": "h", "attention": False}, True, NOW)["event"] == "AMBER_CLEAR"
    assert alarms.digest_event({"headline": "h", "attention": False}, False, NOW) is None


def test_first_alarm_enabled_pass_publishes_open_findings(paths, store, monkeypatch):
    from self_aware import engine as eng_mod

    found = [diagnose.Finding("prog.analyzer", "t", "progress", "AMBER", "slow", "fast"),
             diagnose.Finding("inv.custody", "t", "invariant", "GREEN", "ok", "ok")]
    monkeypatch.setattr(eng_mod, "collect", lambda *a, **k: _facts())
    monkeypatch.setattr(eng_mod.diagnose, "run", lambda *a, **k: found)
    quiet = eng_mod.Engine(paths, store=store, probe_local=False, repair_enabled=False, emit_alarms=False)
    quiet.job_diagnose()
    loud = eng_mod.Engine(paths, store=store, probe_local=False, repair_enabled=False, emit_alarms=True)
    loud.job_diagnose()
    loud.job_diagnose()
    lines = [json.loads(x) for x in (paths.chain / "health" / "alarms.jsonl").read_text().splitlines()]
    assert [(x["check"], x["event"]) for x in lines] == [("selfaware.prog.analyzer", "AMBER")]


# ----------------------------------------------------------------- repair

def test_repair_flags_trading_and_never_executes(paths):
    calls = []
    f = _facts(relay={"relayExecutionMode": "ARMED", "relayArmedAt": "x"},
               watcher={"generated_ts": NOW - 60, "checks": []})
    out = repair.run(paths, f, [], {}, runner=lambda task: calls.append(task) or (True, ""))
    assert calls == [] and [(o["mode"], o["outcome"]) for o in out] == [("FLAG", "FLAGGED_NOT_EXECUTED")]


def test_repair_nudges_owner_with_cooldown(paths):
    calls = []
    f = _facts(watcher={"generated_at": "2026-01-01T00:00:00Z", "checks": []})
    state: dict = {}
    runner = lambda task: calls.append(task) or (True, "SUCCESS")  # noqa: E731
    out = repair.run(paths, f, [], state, runner=runner)
    assert calls == ["DoxxedSystemHealthWatcher"] and out[0]["outcome"] == "EXECUTED"
    assert repair.run(paths, _facts(now=NOW + 60, watcher=f["watcher"]), [], state, runner=runner) == []
    assert repair.run(paths, f, [], {}, enabled=False, runner=runner)[0]["outcome"] == "SKIPPED_REPAIR_DISABLED"
    journal = [json.loads(x) for x in paths.journal.read_text().splitlines()]
    assert len(journal) == 2 and journal[0]["action"] == "nudge_watcher"


# ------------------------------------------------------------- statistics

def test_stats_helpers():
    lo, hi = tp.wilson(55, 100)
    assert lo < 0.55 < hi
    q = tp.bh_q(np.array([0.001, 0.02, 0.04, 0.5]))
    assert np.all(np.diff(q[np.argsort([0.001, 0.02, 0.04, 0.5])]) >= 0) and q[0] <= 0.004 + 1e-12
    assert np.all(tp.holm(np.array([0.01, 0.04])) >= np.array([0.01, 0.04]))
    v = np.random.default_rng(1).normal(2, 1, 400)
    blo, bhi = tp.cluster_bootstrap(v, np.arange(400) // 10)
    assert blo < v.mean() < bhi
    assert tp.cluster_t_pvalue(v, np.arange(400) // 10) < 0.001


def _synthetic_tape(n: int = 6 * 86400, seed: int = 7) -> tp.Tape:
    rng = np.random.default_rng(seed)
    mid = 60000 * np.exp(np.cumsum(rng.normal(0, 0.5e-4, n)))
    return tp.Tape(t0=1_790_000_000, mid=mid, spr_bp=np.full(n, 0.2), present=np.ones(n, bool),
                   next_bad=np.full(n, n + 10), bid_qty=np.ones(n), ask_qty=np.ones(n),
                   cbuy=np.zeros(n + 1), csell=np.zeros(n + 1))


def test_tape_forward_and_hole_masking():
    t = _synthetic_tape(5000)
    ret, cost = t.forward(np.array([10]), 60)
    assert np.isfinite(ret[0]) and abs(cost[0] - 0.2) < 1e-9
    t.next_bad[:] = 40
    assert np.isnan(t.forward(np.array([10]), 60)[0][0])


def test_edges_noise_is_never_a_candidate_and_planted_edge_is():
    reg = edges.load_registry()
    events = edges.grid_events(_synthetic_tape(), int(reg["clock"]["step_sec"]))
    res, _ = edges.evaluate(events, reg)
    assert len(res) == sum(len(s["horizons"]) for s in reg["specs"])
    assert "CANDIDATE" not in set(res["status"])
    planted = events.copy()
    planted["ts"] = 1_790_000_000 + np.arange(len(planted)) * 1800.0  # spread over ~35 days so min-days can pass
    planted["r5m"] = np.where(np.arange(len(planted)) % 2, 5.0, -5.0)
    planted["fwd_15m_bp"] = np.sign(planted["r5m"]) * 6.0 + np.random.default_rng(3).normal(0, 2, len(planted))
    planted["cost_15m_bp"] = 0.2
    res2, _ = edges.evaluate(planted, reg)
    mom = res2[(res2.spec_id == "MOM_R5M") & (res2.horizon == "15m")].iloc[0]
    assert mom.status == "CANDIDATE", mom.reasons
    fade = res2[(res2.spec_id == "FADE_R5M") & (res2.horizon == "15m")].iloc[0]
    assert fade.status == "REJECTED"


def test_scorecard_comparators_and_ci():
    n = 400
    rng = np.random.default_rng(5)
    df = pd.DataFrame({
        "ts": NOW - 3600 * 5 + np.arange(n) * 30.0, "model_served": "m", "prompt_id": "p", "session": "EU",
        "rv15_bp": rng.uniform(1, 5, n), "side_score_led": np.where(np.arange(n) % 2, 1, -1),
        "side_abstain": 0, "side_rule_vote": 1, "side_random": 1, "side_compact": 0,
    })
    for lab in tp.HLABEL.values():
        df[f"fwd_{lab}_bp"] = rng.normal(1.0, 0.1, n)
        df[f"cost_{lab}_bp"] = 0.2
    card = scorecard(df, NOW, bootstrap=50)
    row = lambda s: card[(card.window == "all") & (card.horizon == "5m") & (card.slice_dim == "overall") & (card.strategy == s)].iloc[0]  # noqa: E731
    assert row("ALWAYS_LONG").hit_rate == 1.0 and abs(row("ALWAYS_LONG").net_bp - 0.8) < 0.05
    assert abs(row("AI_SCORE_LED").hit_rate - 0.5) < 0.01 and row("AI_SCORE_LED").vs_always_long_bp < -0.5
    assert "SHADOW_COMPACT" not in set(card.strategy) and "AI_ABSTAIN_RESPECTING" not in set(card.strategy)
    assert json.dumps(json_safe({"x": float("nan"), "y": np.int64(3)}), allow_nan=False) == '{"x": null, "y": 3}'


# -------------------------------------------------------------------- API

def test_server_endpoints_and_query_guard(paths, store):
    from self_aware.server import make_server

    class FakeEngine:
        def __init__(self):
            self.store, self.paths = store, paths
            self.docs = {"health": {"verdict": "GREEN", "findings": []}}

        def engine_status(self):
            return {"ok": True}

    srv = make_server(FakeEngine(), 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        get = lambda p: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=10).read())  # noqa: E731
        assert get("/api/ping")["ok"] is True
        assert get("/api/selfaware/health")["verdict"] == "GREEN"
        assert get("/api/selfaware/query?sql=SELECT%201%20AS%20x")["rows"] == [[1]]
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/selfaware/query?sql=SELECT%20getenv('X')", timeout=10)
        assert err.value.code == 400
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------ data awareness

def test_field_liveness_classifies_dead_constant_and_missing():
    spec = data_awareness.StreamSpec("s", "s.jsonl", "", "", "event", ("ts",),
                                     watch=("context.ret_1m", "price", "label", "absent", "epoch_id"))
    rows = [{"ts": NOW + i, "context": {"ret_1m": 0.0, "price": 100 + i}, "price": 100 + i, "label": "WARMUP",
             "epoch_id": None, "free_text": "x"} for i in range(60)]
    f = {r["field"]: r for r in data_awareness.profile_fields(rows, spec)}
    assert f["context.ret_1m"]["status"] == "DEAD_ZERO" and f["context.ret_1m"]["alarm"]
    assert f["price"]["status"] == "OK" and not f["price"]["alarm"]
    assert f["label"]["status"] == "CONSTANT" and f["label"]["alarm"]
    assert f["epoch_id"]["status"] == "DEAD_NULL" and f["absent"]["status"] == "MISSING"
    assert f["free_text"]["status"] == "CONSTANT" and not f["free_text"]["alarm"]  # unwatched constants never alarm
    few = {r["field"]: r for r in data_awareness.profile_fields(rows[:3], spec)}
    assert few["absent"]["status"] == "MISSING_FEW_ROWS" and not few["absent"]["alarm"]
    assert f["absent"]["alarm"]


def _data_mirror(paths) -> float:
    now = float(int(NOW) // 3600 * 3600 + 1800)
    t0 = int(now - 3 * 3600)
    secs = [t for t in range(t0, int(now) - 5) if not (t0 + 1000 <= t < t0 + 1600) and not (t0 + 5000 <= t < t0 + 5120)]
    _jsonl(paths.mirror / "market_microstructure_1s.jsonl",
           [{"bucket_ts": t, "bid": 100.0 + (t % 7), "ask": 100.5 + (t % 7), "bid_qty": 1.0 + t % 3, "ask_qty": 1.0 + t % 2,
             "buy_qty": float(t % 5), "sell_qty": float(t % 4), "valid_bbo": True, "fresh": True} for t in secs])
    _jsonl(paths.mirror / "cross_venue_tape_1m.jsonl",
           [{"minute_ts": m, "n": 60, "basis_bp_mean": {"binance": (m // 60 % 17) / 3, "bybit": 1 + m // 60 % 5,
                                                        "okx": 2.0 + m // 60 % 3}, "bfx": {"m0": 1000 + m // 60 % 11}}
            for m in range(t0 - t0 % 60, int(now) - 60, 60)])
    _jsonl(paths.mirror / "ai_input_log.jsonl",
           [{"ts_epoch": now - 3600 + i * 60, "context": {"price": 100 + i, "ret_1m": 0.0, "ret_5m": 0.0,
                                                          "delta_change": 0.0, "ema_slope": i / 100}} for i in range(55)])
    _jsonl(paths.mirror / "brand_new_stream.jsonl", [{"ts": now - i, "v": i} for i in range(30)])
    for i in range(3):
        _jsonl(paths.mirror / "v3" / "receipts" / "x" / f"r{i}.json", [{"i": i}])
    paths.retention.mkdir(parents=True, exist_ok=True)
    (paths.retention / "status.json").write_text(json.dumps({
        "bytes_after": 26e9, "cap_bytes": 120e9, "mode": "enforce", "finished_at": "2026-10-02T09:27:41Z",
        "sizes_after": {"mirror_tree": 5.3e9}, "tier_a_schema": [{"dataset": "ai_calls", "bytes": 1, "partitions": 1,
                                                                  "status": "COMPATIBLE"}]}), encoding="utf-8")
    return now


def test_data_awareness_catalog_completeness_capacity_and_sufficiency(paths, store):
    now = _data_mirror(paths)
    store.refresh_views()
    t0 = int(now - 3 * 3600)
    facts = _facts(watcher={"checks": [{"id": "disk.space", "status": "GREEN", "observed":
                                        "laptop free 306.6GB; Fly volume free 36.4GB (493.0h to full); segment store 17.5% of cap"}]})
    res = data_awareness.run(store, paths, facts, {}, [(t0 + 4990, t0 + 5130, "fly.paused")], now=now)
    by = {s["stream"]: s for s in res["streams"]}
    assert not by["brand_new_stream.jsonl"]["catalogued"] and by["bitfinex_tape_1s"]["catalogued"]
    assert by["v3_execution"]["status"] == "MISSING"
    assert by["v3/receipts/"]["kind"] == "dir" and by["v3/receipts/"]["files"] == 3 and by["v3/receipts/"]["bytes_per_day"] > 0
    assert by["bitfinex_tape_1s"]["fly_location"] == "/app/data/market_microstructure_1s.jsonl"
    assert "context.ret_1m=DEAD_ZERO (0.0)" in res["summary"]["watch_alarms"]["ai_calls"]
    assert "cross_venue_tape_1m" not in res["summary"]["watch_alarms"]
    tape = by["bitfinex_tape_1s"]["exact"]
    gaps = {g["sec"]: g for g in tape["gaps"]}
    assert gaps[601]["explained_by"] is None and gaps[121]["explained_by"] == "fly.paused"
    assert [g["sec"] for g in tape["unexplained_gaps_24h"]] == [601]
    assert tape["fill_pct_24h"] < tape["fill_pct_24h_excl_interruptions"] < 100
    cap = res["capacity"]
    assert cap["laptop"]["bot_data_gb"] == 26.0 and cap["fly"]["hours_to_full"] == 493.0
    assert cap["laptop"]["days_to_90pct_cap"] and cap["laptop"]["days_to_90pct_cap"] > 0
    q = {x["id"]: x for x in res["sufficiency"]}
    assert q["Q_VOL_GATE"]["status"] == "ACCUMULATING" and not q["Q_VOL_GATE"]["blockers"]
    assert q["Q_VOL_GATE"]["eta_ready"] and not q["Q_VOL_GATE"]["screens_released"]
    assert q["Q_XVENUE"]["status"] == "BLOCKED" and any("xvl_shadow_signals" in b for b in q["Q_XVENUE"]["missing_for_full_answer"])
    assert q["Q_XVENUE"]["eta_ready"] is None  # a blocked question has no ETA until the field exists
    assert cap["laptop"]["mirror_copies_factor"] > 1
    assert store.read("SELECT count(*) AS n FROM res_data_sufficiency WHERE screens_released")[0]["n"] == 0

    found = {f.id: f for f in diagnose.check_data({**facts, "now": now, "data_awareness": res["summary"]}, diagnose.signals(
        {**facts, "now": now}), store)}
    assert found["data.dead_fields"].severity == "AMBER" and "context.ret_1m" in found["data.dead_fields"].observed
    assert found["data.completeness"].severity == "AMBER" and "601s" in found["data.completeness"].observed
    assert found["data.capacity"].severity == "GREEN"
    assert found["data.freshness"].severity == "AMBER" and "market_context_1m" in found["data.freshness"].observed
    assert not found["data.sufficiency"].emit_alarm
    stale = diagnose.check_data({**facts, "now": now + 3 * 3600, "data_awareness": res["summary"]}, {}, store)
    assert {f.severity for f in stale} == {"SKIP"}


def test_gated_screens_stay_queued_until_their_question_is_ready():
    reg = edges.load_registry()
    events = edges.grid_events(_synthetic_tape(), int(reg["clock"]["step_sec"]))
    queued, _ = edges.evaluate(events, reg)
    gated = queued[queued.spec_id.str.startswith(("VOLHI", "VOLLO", "TREND_MOM", "CHOP_"))]
    assert len(gated) and set(gated.status) == {"QUEUED_DATA"} and gated.bh_q.isna().all()
    released, _ = edges.evaluate(events, reg, {"Q_VOL_GATE"})
    vol = released[released.spec_id.str.startswith(("VOLHI", "VOLLO"))]
    assert "QUEUED_DATA" not in set(vol.status) and vol.holdout_n.max() > 0
    assert set(released[released.spec_id.str.startswith("CHOP_")].status) == {"QUEUED_DATA"}
    hi = released[(released.spec_id == "VOLHI_MOM_R15M") & (released.horizon == "15m")].iloc[0]
    base = released[(released.spec_id == "MOM_R15M") & (released.horizon == "15m")].iloc[0]
    assert 0 < hi.holdout_n < base.holdout_n  # the gate keeps roughly the top tercile only
    assert "CANDIDATE" not in set(released.status)


def test_data_endpoints_and_view(paths, store):
    from self_aware.server import make_server
    now = _data_mirror(paths)
    store.refresh_views()
    res = json_safe(data_awareness.run(store, paths, _facts(), {}, [], now=now))

    class FakeEngine:
        def __init__(self):
            self.store, self.paths = store, paths
            self.docs = {"health": {"verdict": "AMBER", "findings": []}, "data": res}

        def engine_status(self):
            return {"ok": True}

    srv = make_server(FakeEngine(), 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        raw = lambda p: urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=10).read()  # noqa: E731
        get = lambda p: json.loads(raw(p))  # noqa: E731
        assert get("/api/selfaware/data")["schema"] == "self_aware_data_v1"
        assert {q["id"] for q in get("/api/selfaware/data/sufficiency")["questions"]} >= {"Q_VOL_GATE", "Q_LIMIT_VS_TAKER"}
        cat = get("/api/selfaware/data/catalog")["streams"]
        assert any(s["stream"] == "brand_new_stream.jsonl" for s in cat) and all("hourly" not in s for s in cat)
        assert get("/api/selfaware/data/catalog?stream=bitfinex_tape_1s")["streams"][0]["exact"]["gaps"]
        dead = get("/api/selfaware/data/fields?status=DEAD_ZERO&watched=1")["fields"]
        assert {f["field"] for f in dead} == {"context.ret_1m", "context.ret_5m", "context.delta_change"}
        assert get("/api/selfaware/data/completeness")["streams"]
        assert get("/api/selfaware/data/capacity")["laptop"]["cap_gb"] == 120.0
        assert b"Research sufficiency" in raw("/data")
    finally:
        srv.shutdown()
        srv.server_close()

def test_capacity_uses_manifest_ingest_and_restarts_slope_after_reclaim(paths):
    man = paths.segment_manifests
    man.mkdir(parents=True)
    for i in range(13):
        end = NOW - 12 * 3600 + i * 3600
        (man / f"{i:06d}.json").write_text(json.dumps({"window_end": end, "members": [
            {"kind": "APPEND", "size": 100_000_000}, {"kind": "SNAPSHOT", "size": 50_000_000}]}), encoding="utf-8")
    fly = data_awareness.fly_ingest_from_manifests(man, NOW)
    assert fly["manifests"] == 13 and fly["span_h"] == 12.0
    assert fly["append_gb_per_day"] == pytest.approx(1.3 * 2, rel=1e-3)
    assert fly["snapshot_churn_gb_per_day"] == pytest.approx(0.65 * 2, rel=1e-3)
    # A one-off 20 GB purge followed by steady growth: only the growth after the purge counts.
    hist = [[NOW - 20 * 3600 + h * 3600, 40e9 + h * 1e8] for h in range(8)]
    hist += [[NOW - 12 * 3600 + h * 3600, 20e9 + h * 1e8] for h in range(13)]
    assert data_awareness._slope_per_day(hist, NOW) == pytest.approx(2.4e9, rel=1e-6)
    (paths.retention).mkdir(parents=True, exist_ok=True)
    (paths.retention / "status.json").write_text(json.dumps({
        "bytes_after": 20e9, "cap_bytes": 120e9, "sizes_basis": "physical_v1",
        "sizes_after": {"mirror_tree": 10e9, "canonical": 10e9},
        "storage_dedupe": {"duplicate_physical_gb": 2.5, "hardlink_saved_gb": 6.0, "link_fallback_alarms_24h": 0}}),
        encoding="utf-8")
    state = {"capacity_basis": "logical", "capacity_history": [[NOW - 7200, 30e9]]}
    cap = data_awareness.capacity(paths, {}, [{"bytes_per_day": 1e8}], state, NOW)
    assert state["capacity_history"] == [[NOW, 20e9]]  # logical history dropped on the basis change
    assert cap["fly"]["ingest_gb_per_day"] == fly["append_gb_per_day"]
    assert cap["fly"]["stream_sample_ingest_gb_per_day"] == 0.1
    assert cap["laptop"]["growth_gb_per_day"] == pytest.approx(fly["append_gb_per_day"] * 2, rel=1e-3)
    assert cap["laptop"]["duplicate_physical_gb"] == 2.5 and cap["laptop"]["disk_free_gb"] > 0


def test_capacity_defaults_to_120gb_and_refused_cap_is_red(paths, store):
    now = _data_mirror(paths)
    store.refresh_views()
    (paths.retention / "status.json").write_text(json.dumps({
        "bytes_after": 26e9, "mode": "enforce", "finished_at": "2026-10-02T09:27:41Z",
        "sizes_after": {"mirror_tree": 5.3e9}}), encoding="utf-8")
    res = data_awareness.run(store, paths, _facts(), {}, [], now=now)
    assert res["capacity"]["laptop"]["cap_gb"] == 120.0
    assert res["capacity"]["laptop"]["retention_cap_status"] is None
    (paths.retention / "status.json").write_text(json.dumps({
        "bytes_after": 26e9, "cap_bytes": 120e9, "mode": "enforce", "finished_at": "2026-10-02T09:27:41Z",
        "sizes_after": {"mirror_tree": 5.3e9}, "alarm": "CAP_EXCEEDED_PROTECTED_FLOOR: refused 3 protected file(s)",
        "cap": {"status": "CAP_EXCEEDED_PROTECTED_FLOOR", "refused_count": 3}}), encoding="utf-8")
    res = data_awareness.run(store, paths, _facts(), {}, [], now=now)
    lap = res["capacity"]["laptop"]
    assert lap["retention_cap_status"] == "CAP_EXCEEDED_PROTECTED_FLOOR" and lap["retention_cap_refused"] == 3
    found = {f.id: f for f in diagnose.check_data({**_facts(), "now": now, "data_awareness": res["summary"]}, {}, store)}
    assert found["data.capacity"].severity == "RED"
    assert "RETENTION ALARM CAP_EXCEEDED_PROTECTED_FLOOR" in found["data.capacity"].observed


def test_still_red_refreshes_observed_text_and_mirror_empty_is_warmup(tmp_path):
    from self_aware import alarms, facts

    state: dict = {}
    red = {"id": "prog.analyzer", "severity": "RED", "observed": "last successful analyzer run 61m ago",
           "expected": "x", "causes": []}
    first = alarms.still_red_events([red], [], state, 1000.0)
    assert [e["event"] for e in first] == ["STILL_RED"]
    assert "61m" in first[0]["observed"] and first[0]["check"].endswith("prog.analyzer")
    assert alarms.still_red_events([red], [], state, 1100.0) == []
    assert len(alarms.still_red_events([red], [], state, 1000.0 + alarms.STILL_RED_EVERY_SEC)) == 1
    assert alarms.still_red_events([{**red, "severity": "GREEN"}], [], state, 99999.0) == []
    assert "prog.analyzer" not in state["still_red_at"]

    class _Paths:
        mirror = tmp_path

    class _Store:
        paths = _Paths()

        def read(self, sql):
            raise RuntimeError('IO Error: No files found that match the pattern "ai_tranche_log.csv"')

    row, err = facts._mirror_max(_Store(), "SELECT max(ts) AS max_ts FROM raw_ai_tranche")
    assert err is None and row["status"] == "EMPTY_WARMUP"
    (tmp_path / "ai_tranche_log.csv").write_text("ts\n1\n")
    row, err = facts._mirror_max(_Store(), "SELECT max(ts) AS max_ts FROM raw_ai_tranche")
    assert err and "IO Error" in err


def _feeds_rt(observed_at: float = NOW - 30, **sp) -> dict:
    progress = {"ws_age_sec": 3.0, "ws_progressing": True, **sp}
    return {"observedAt": observed_at, "git_rev": "abc", "execution_paused": False, "ws_transport_connected": True,
            "strategy_progress": progress, "ws_connection": {"reconnect_count": 0, "generation": 1},
            "cross_venue_health": {"status": "OK", "stale_venues": []}, "xvl_evaluator_health": {"status": "OK"}}


def test_feeds_quiet_tape_tick_age_is_not_an_alarm_when_the_heartbeat_is_fresh(store):
    # 2026-10-04 22:23 AEDT: "Bitfinex WS age 30s" AMBER with heartbeat 7s and 0 reconnects.
    for tick in (30.0, 46.0, 51.0):
        f = _facts(runtime=_feeds_rt(ws_age_sec=tick, ws_heartbeat_age_sec=7.0))
        fd = diagnose.check_feeds(f, diagnose.signals(f), store, {})
        assert fd.severity == "GREEN", fd.observed
        assert not diagnose.signals(f)["cpu_saturation"]["on"]


def test_feeds_heartbeat_age_ambers_over_60s_and_reds_over_venue_stale(store):
    f = _facts(runtime=_feeds_rt(ws_age_sec=70.0, ws_heartbeat_age_sec=65.0))
    fd = diagnose.check_feeds(f, diagnose.signals(f), store, {})
    assert fd.severity == "AMBER" and "heartbeat age" in fd.observed
    f = _facts(runtime=_feeds_rt(ws_age_sec=200.0, ws_heartbeat_age_sec=150.0))
    assert diagnose.check_feeds(f, diagnose.signals(f), store, {}).severity == "RED"


def test_feeds_old_snapshot_without_heartbeat_falls_back_to_tick_age_at_60s(store):
    f = _facts(runtime=_feeds_rt(ws_age_sec=45.0))
    assert diagnose.check_feeds(f, diagnose.signals(f), store, {}).severity == "GREEN"
    f = _facts(runtime=_feeds_rt(ws_age_sec=75.0))
    fd = diagnose.check_feeds(f, diagnose.signals(f), store, {})
    assert fd.severity == "AMBER" and "tick age" in fd.observed


def test_feeds_reconnect_storm_ambers_and_a_restart_resets_the_counter(store):
    state: dict = {}
    for i, count in enumerate((0, 1, 2)):
        rt = _feeds_rt(ws_heartbeat_age_sec=3.0)
        rt["ws_connection"]["reconnect_count"] = count
        f = _facts(runtime=rt, now=NOW + i * 60)
        assert diagnose.check_feeds(f, diagnose.signals(f), store, state).severity == "GREEN"
    rt = _feeds_rt(ws_heartbeat_age_sec=3.0)
    rt["ws_connection"]["reconnect_count"] = 3
    f = _facts(runtime=rt, now=NOW + 180)
    fd = diagnose.check_feeds(f, diagnose.signals(f), store, state)
    assert fd.severity == "AMBER" and "reconnect storm" in fd.observed
    # Fly restart: the per-boot counter drops back to 0, which is not a storm.
    rt = _feeds_rt(ws_heartbeat_age_sec=3.0)
    f = _facts(runtime=rt, now=NOW + 240)
    assert diagnose.check_feeds(f, diagnose.signals(f), store, state).severity == "GREEN"
    # Reconnects spread beyond the 15-minute window are not a storm either.
    for i, count in enumerate((1, 2, 3, 4)):
        rt = _feeds_rt(observed_at=NOW + 300 + i * 600 - 30, ws_heartbeat_age_sec=3.0)
        rt["ws_connection"]["reconnect_count"] = count
        f = _facts(runtime=rt, now=NOW + 300 + i * 600)
        assert diagnose.check_feeds(f, diagnose.signals(f), store, state).severity == "GREEN", count


def test_feeds_disconnected_transport_ambers(store):
    rt = _feeds_rt(ws_heartbeat_age_sec=3.0)
    rt["ws_transport_connected"] = False
    f = _facts(runtime=rt)
    assert diagnose.check_feeds(f, diagnose.signals(f), store, {}).severity == "AMBER"


def test_tile_orders_quiet_window_follows_each_tiles_own_rate(paths, store):
    busy, rare, never = "FAMILY_PREMIUM_REVERSION_60M", "FAMILY_GS03_CVD_DIV_TAKER", "FAMILY_GSB2_REGIME_SWITCHER"
    rows = [{"event_id": f"b{i}", "decision_ts": NOW - 7 * 3600 - i * 3600, "research_lane": busy,
             "execution_disposition": "ORDER_ELIGIBLE"} for i in range(24)]
    rows += [{"event_id": "r1", "decision_ts": NOW - 5 * 3600, "research_lane": rare,
              "execution_disposition": "ORDER_ELIGIBLE"}]
    _jsonl(paths.mirror / "v3" / "ledgers" / "decision.jsonl", rows)
    store.refresh_views()
    rt = {"observedAt": NOW - 30, "git_rev": "abc", "research_lane_enabled": {busy: True, rare: True, never: True},
          "strategy_progress": {}}
    f = _facts(runtime=rt)
    fd = diagnose.check_orders_on_tiles(f, diagnose.signals(f), store)
    # busy: 24 in 48 h (2 h gap) -> 6 h limit, quiet 7 h -> AMBER; rare CVD tile 5 h quiet -> fine;
    # never-ordered tile -> listed without alarming.
    assert fd.severity == "AMBER" and fd.evidence["quiet"] == [busy], fd.observed
    assert fd.evidence["no_baseline"] == [never]
    assert fd.evidence["limits_sec"][rare] == 48 * 3600
    rows = [dict(r, decision_ts=r["decision_ts"] + 3 * 3600) for r in rows]
    _jsonl(paths.mirror / "v3" / "ledgers" / "decision.jsonl", rows)
    store.refresh_views()
    fd = diagnose.check_orders_on_tiles(f, diagnose.signals(f), store)
    assert fd.severity == "GREEN", fd.observed
