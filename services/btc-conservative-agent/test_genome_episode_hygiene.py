"""Genome grid episode hygiene: one episode per AI decision, cross-venue/duplicate rows excluded, REALISTIC_V1 axes."""
import json

import numpy as np
import pytest

from research import dashboard_sections as ds
from research import genome_grid_study as g

T0 = 1_790_900_000


def _tape(n=20_000):
    mid = np.full(n, 100_000.0)
    return g.Tape(T0, mid - 0.5, mid + 0.5, mid, mid - 1, mid + 1, np.ones(n), [])


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _opp(ep, ts, raw, call=None, price=100_000.0):
    return {"episode_id": ep, "signal_ts": T0 + ts, "signal_price": price, "raw_direction": raw,
            "shared_ai_call_id": call, "feature_snapshot_at_signal": {"atr14_pct_3m": 0.1}}


def _dec(ep, lane, event, ls=None, ss=None):
    return {"episode_id": ep, "research_lane": lane, "event_id": event, "long_score": ls, "short_score": ss}


def _replay(tid, ts, direction, lane):
    iso = __import__("time").strftime("%Y-%m-%dT%H:%M:%S+00:00", __import__("time").gmtime(T0 + ts))
    return {"trade_id": tid, "start_ts": iso, "start_price": 100_000.0, "direction": direction, "lane": lane}


@pytest.fixture
def mirror(tmp_path):
    led = tmp_path / "v3" / "ledgers"
    _write(led / "opportunity.jsonl", [
        _opp("e-commit", 3000, "LONG", "scan-a"),
        _opp("e-twin", 3000.4, "SHORT"),  # reversal-study CONTROL_V1 twin of scan-a, side inverted, no call id
        _opp("e-notrade", 3180, "NO_TRADE", "scan-b"),
        _opp("e-xvp", 3300, "SHORT", "xvp-1790903300"),  # cross-venue trigger that also carries CONTROL_V1
        _opp("e-xvl", 3400, "LONG", "xvl-1790903400"),
        _opp("e-legacy", 3600, "LONG"),  # identity-incomplete legacy row for a call the ledger never keyed
        _opp("e-tie", 3900, "NO_TRADE", "scan-t"),
    ])
    _write(led / "decision.jsonl", [
        _dec("e-commit", "CONTINUOUS", "scan-a", 0.7, 0.2),
        _dec("e-twin", "CONTROL_V1", "rev-scan-a"),
        _dec("e-notrade", "FAMILY_TREND_FADE_60", "scan-b", 0.3, 0.6),
        _dec("e-xvp", "CONTROL_V1", "xvp-f13175c201c9"),
        _dec("e-xvp", "FAMILY_XVENUE_PREMIUM_60S", "lane-decision:FAMILY_XVENUE_PREMIUM_60S:xvp-1790903300"),
        _dec("e-xvl", "FAMILY_XVENUE_LEAD_60S", "xvl-1"),
        _dec("e-legacy", "CONTROL_V1", "scan-z"),
        _dec("e-tie", "CONTINUOUS", "scan-t", 0.5, 0.5),
    ])
    _write(tmp_path / "signal_replay.jsonl", [
        _replay("scan-a", 3000.2, "LONG", "executed"),            # same decision as the v3 episode
        _replay("rev-scan-b", 3180.3, "LONG", "reversal_study"),  # inverted twin of scan-b
        _replay("xvp-2b1155828df8", 3301, "SHORT", "executed"),   # cross-venue paper trade
        _replay("scan-c", 9000, "SHORT", "shadow_blocked"),       # AI call missing from v3: admitted
        _replay("rev-scan-c", 9000.5, "LONG", "reversal_study"),  # its derivative
        _replay("rev-scan-q", 12000, "LONG", "reversal_study"),   # derivative without an original
        _replay("scan-near", 9030, "LONG", "shadow"),             # 30 s from scan-c: same decision window
    ])
    return tmp_path


def test_one_episode_per_ai_decision_with_classes(mirror):
    episodes, stats = g.load_episodes(mirror, _tape())
    by_id = {e["decision_id"]: e for e in episodes}
    assert sorted(by_id) == ["scan-a", "scan-b", "scan-c"]
    assert by_id["scan-a"]["episode_class"] == "AI_COMMITTED" and by_id["scan-a"]["direction"] == "LONG"
    assert by_id["scan-b"]["episode_class"] == "AI_NO_TRADE_SCORE_LED" and by_id["scan-b"]["direction"] == "SHORT"
    assert by_id["scan-c"]["episode_class"] == "AI_SIGNAL_REPLAY" and by_id["scan-c"]["direction"] == "SHORT"
    assert {e["cohort"] for e in episodes} == {g.GENOME_COHORT}
    assert stats["excluded_v3_by_reason"] == {"DUPLICATE_OF_AI_DECISION": 1, "XVENUE_PREMIUM": 1, "XVENUE_LEAD": 1,
                                              "IDENTITY_INCOMPLETE_NO_SHARED_AI_CALL_ID": 1, "NO_SIDE": 1}
    assert stats["excluded_signal_replay_by_reason"] == {"DUPLICATE_OF_AI_DECISION": 4, "XVENUE_PREMIUM": 1,
                                                         "REVERSAL_STUDY_ORPHAN": 1}
    assert stats["xvenue_unique_triggers"] == {"XVENUE_LEAD": 1, "XVENUE_PREMIUM": 2}
    assert g.episode_integrity(episodes)["status"] == "PASS"


def test_score_conflict_is_its_own_class(tmp_path):
    led = tmp_path / "v3" / "ledgers"
    _write(led / "opportunity.jsonl", [_opp("e1", 3000, "LONG", "scan-x")])
    _write(led / "decision.jsonl", [_dec("e1", "CONTINUOUS", "scan-x", 0.2, 0.8)])
    episodes, _ = g.load_episodes(tmp_path, _tape())
    assert [e["episode_class"] for e in episodes] == ["AI_COMMITTED_SCORE_CONFLICT"]


def test_decision_identity_and_xvenue_class():
    assert g.decision_identity("rev-scan-80561bd239ae") == ("scan-80561bd239ae", True)
    assert g.decision_identity("lane-decision:FAMILY_XVENUE_PREMIUM_60S:xvp-17") == ("xvp-17", False)
    assert g.xvenue_class("xvl-1") == "XVENUE_LEAD" and g.xvenue_class("scan-1") is None
    assert g.xvenue_class("scan-1", ["CONTROL_V1", "FAMILY_XVENUE_PREMIUM_60S"]) == "XVENUE_PREMIUM"


def test_integrity_fails_on_duplicates_mixed_classes_and_twins():
    ok = [{"episode_id": "a", "decision_id": "scan-a", "episode_class": "AI_COMMITTED", "cohort": "AI_DECISION",
           "signal_ts": 1000.0},
          {"episode_id": "b", "decision_id": "scan-b", "episode_class": "AI_NO_TRADE_SCORE_LED",
           "cohort": "AI_DECISION", "signal_ts": 1180.0}]
    assert g.episode_integrity(ok)["status"] == "PASS"
    dup = ok + [dict(ok[0], episode_id="c", signal_ts=2000.0)]
    assert any(v.startswith("DUPLICATE_DECISION_ID") for v in g.episode_integrity(dup)["violations"])
    mixed = ok + [dict(ok[0], episode_id="x", decision_id="xvp-1", episode_class="XVENUE_PREMIUM", signal_ts=3000.0)]
    rep = g.episode_integrity(mixed)
    assert rep["status"] == "FAIL" and rep["foreign_classes"] == {"XVENUE_PREMIUM": 1}
    twin = ok + [dict(ok[0], episode_id="t", decision_id="scan-t", signal_ts=1001.0)]
    assert g.episode_integrity(twin)["twin_signals"] == 1


def test_study_refuses_to_publish_on_integrity_failure(monkeypatch, tmp_path, capsys):
    def boom(*_a, **_k):
        raise g.EpisodeIntegrityError('["MIXED_EPISODE_CLASSES"]')
    monkeypatch.setattr(g, "run", boom)
    monkeypatch.setattr(g, "_below_normal", lambda: None)
    assert g.main(["--ignore-cycle", "--out-dir", str(tmp_path)]) == 2
    assert "EPISODE_INTEGRITY_FAILED" in capsys.readouterr().out
    assert not (tmp_path / "genome_grid_report.json").exists()


def _row(pid, world, offset, train_ev, oos_ev=0.01, verdict="CONFIRMED", rule="FADE"):
    return {"policy_id": pid, "fill_world": world, "direction_rule": rule, "holdout_verdict": verdict,
            "policy_family": "TIME", "entry": {"offset_pct": offset, "chase_id": "no_chase", "ttl_sec": 900},
            "exit": {k: None for k in ("mode", "atr_stop_k", "atr_tp_k", "hard_stop_margin_pct", "ladder",
                                       "thesis_cut_margin_pct", "time_stop_min", "break_even_arm_mfe_pct",
                                       "mfe_giveback_fraction", "mfe_giveback_abs_pct", "atr_trail_k",
                                       "chandelier_atr_k", "partial_take_profits")},
            "all": {"fills": 40}, "train": {"ev_per_fill_usd": train_ev, "fills": 30},
            "oos": {"ev_per_fill_usd": oos_ev, "win_rate_pct": 55.0, "fills": 10}}


def test_axes_select_per_value_within_realistic_only():
    rows = [_row("A", "REALISTIC_V1", 0.10, 0.004), _row("B", "REALISTIC_V1", 0.20, 0.002),
            _row("C", "REALISTIC_V1", 0.30, -0.001, verdict="NEGATIVE_TRAIN"),
            _row("A", "OPTIMISTIC_TOUCH_SHADOW", 0.10, 0.009), _row("B", "OPTIMISTIC_TOUCH_SHADOW", 0.20, 0.008)]
    summary = g.dimension_summary(rows)
    assert "fill_world" not in summary
    off = summary["entry_offset_pct"]
    assert off["headline_fill_world"] == "REALISTIC_V1" and off["distinct_best_policies"] == 3
    assert [v["best_policy_id"] for v in off["values"]] == ["A", "B", "C"]
    assert {v["best_fill_world"] for v in off["values"]} == {"REALISTIC_V1"}
    assert off["values"][0]["best_train_ev_per_fill_usd"] == 0.004  # never the optimistic 0.009
    assert off["values"][0]["shadow_best_train_ev_per_fill_usd"] == 0.009
    checks = {c["id"]: c["severity"] for c in ds.genome_content_checks(
        {"headline_fill_world": "REALISTIC_V1", "dimension_summary": summary,
         "episode_integrity": {"status": "PASS", "episodes": 3, "unique_decision_ids": 3, "duplicate_decision_ids": 0,
                               "classes": {"AI_COMMITTED": 3}}})}
    assert checks["genome_axes_headline_world"] == ds.GREEN and checks["genome_axes_distinct"] == ds.GREEN
    assert checks["genome_episode_integrity"] == ds.GREEN


def test_content_checks_catch_the_contaminated_report_shape():
    same = {"best_policy_id": "FADE|X", "best_train_ev_per_fill_usd": 0.0147}
    legacy = {"headline_fill_world": "REALISTIC_V1",
              "coverage": {"evaluated_by_source": {"V3_OPPORTUNITY": 10}},
              "dimension_summary": {
                  "fill_world": {"distinct_values": 2, "values": [dict(same, value="OPTIMISTIC_TOUCH_SHADOW"),
                                                                  dict(same, value="REALISTIC_V1")]},
                  "direction_rule": {"distinct_values": 2, "values": [dict(same, value="FADE")]}}}
    checks = {c["id"]: c["severity"] for c in ds.genome_content_checks(legacy)}
    assert checks["genome_episode_integrity"] == ds.RED  # no integrity block -> may hold xvenue/duplicate rows
    assert checks["genome_axes_headline_world"] == ds.RED  # fill_world axis can crown the optimistic world
    assert checks["genome_axes_distinct"] == ds.RED  # every value row shows the same policy
    mixed = {"episode_integrity": {"status": "PASS", "duplicate_decision_ids": 0},
             "coverage": {"evaluated_by_class": {"AI_COMMITTED": 5, "XVENUE_PREMIUM": 2}}}
    assert {c["id"]: c["severity"] for c in ds.genome_content_checks(mixed)}["genome_episode_integrity"] == ds.RED


def test_cluster_bootstrap_widens_for_hour_correlated_fills():
    rng = np.random.default_rng(1)
    ts = np.repeat(np.arange(20) * 3600.0, 10) + np.tile(np.arange(10) * 60.0, 20)
    clustered = np.repeat(rng.normal(0, 0.02, 20), 10) + rng.normal(0, 0.002, 200)
    iid = rng.normal(0, 0.02, 200)
    c, i = g.cluster_bootstrap(clustered, ts), g.cluster_bootstrap(iid, ts)
    assert c["clusters"] == 20 and c["fills"] == 200
    assert c["n_eff"] < 40 < i["n_eff"]
    lo, hi = c["ev_ci95_usd"]
    assert lo < clustered.mean() < hi
    assert g.cluster_bootstrap([0.01], [0.0])["ev_ci95_usd"] == [None, None]


def test_walk_forward_selects_on_prior_days_only():
    day = 86400.0
    ts = np.concatenate([np.arange(40) * 600.0 + d * day for d in range(3)])
    n = len(ts)
    values = np.zeros((n, 2, 7))
    codes = np.zeros((n, 2), dtype=np.int8)
    d = np.floor(ts / day)
    values[:, 0, 0] = np.where(d == 0, 0.01, -0.01)  # A: great on day 0, loses afterwards
    values[:, 1, 0] = np.where(d == 0, 0.005, 0.02)  # B: decent on day 0, great afterwards
    matrix = {"values": values, "codes": codes, "ts": ts, "classes": np.array(["AI_COMMITTED"] * n)}
    wf = g.walk_forward_by_day(matrix, ["A", "B"], np.array([True, True]))
    folds = wf["folds"]
    assert [f["selected_policy_id"] for f in folds] == ["A", "B"]  # day 1 can only use day 0
    assert folds[0]["test_ev_per_fill_usd"] == pytest.approx(-0.01)
    assert wf["pooled_oos"]["fills"] == 80 and wf["pooled_oos"]["cluster_1h"]["fills"] == 80


def test_aggregate_breaks_rows_down_by_episode_class():
    entries = [e for e in g.entry_specs() if e["offset_pct"] == 0.0][:1]
    protections = g.protection_specs()
    keys = g.row_keys(entries, protections)
    n = len(keys)
    episodes = [{"episode_id": f"e{i}", "signal_ts": 1000.0 + 600 * i,
                 "episode_class": "AI_COMMITTED" if i % 2 else "AI_NO_TRADE_SCORE_LED"} for i in range(4)]
    results = []
    for i in range(4):
        vals = np.zeros((n, len(g.VALUE_COLUMNS)))
        vals[:, 0] = 0.01 if i % 2 else -0.02
        vals[:, 4] = 1.0
        results.append({"idx": i, "status": "EVALUATED", "values": vals, "code": np.zeros(n, dtype=np.int8)})
    matrix = {}
    rows = g.aggregate(results, episodes, entries, protections, cut_ts=2800.0, matrix_out=matrix)
    split = rows[0]["by_episode_class"]
    assert split["AI_COMMITTED"] == {"signals": 2, "fills": 2, "net_pnl_usd": 0.02, "ev_per_fill_usd": 0.01}
    assert split["AI_NO_TRADE_SCORE_LED"]["ev_per_fill_usd"] == -0.02
    assert matrix["values"].shape[:2] == (4, n) and list(matrix["classes"][:2]) == ["AI_NO_TRADE_SCORE_LED", "AI_COMMITTED"]
    stats = g.row_cluster_stats(matrix, 0, 2800.0)
    assert stats["all"]["fills"] == 4 and stats["oos"]["fills"] == 1
