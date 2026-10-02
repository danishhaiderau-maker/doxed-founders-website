"""Analyzer decision-model report: snapshot health, regime H8 join, logistic challenger H9."""
import json
import random

import numpy as np

import analyzer_research_engine_v62 as engine
import decision_feature_snapshots as dfs
from research import decision_model_report as dmr

DAY = 86400


def _stream(days, per_day=160, signal=0.0, seed=7):
    rng = random.Random(seed)
    rows = []
    start = 20_000 * DAY
    for d in range(days):
        for i in range(per_day):
            ts = start + d * DAY + i * (DAY // per_day)
            flow = rng.uniform(-1, 1)
            ret = signal * flow * 10 + rng.gauss(0, 5)
            cid = f"c{d}-{i}"
            rows.append({"row_kind": "SNAPSHOT", "shared_ai_call_id": cid, "decision_ts": ts,
                         "feature_set_version": dfs.FEATURE_SET_VERSION,
                         "ai": {"prompt_input_revision": "r2"}, "book_depth_collected": False,
                         "tape": {"flow_5m": flow, "spread_bp": 0.1, "ret_60m_bp": 10.0},
                         "compact_facts": {}, "premium": {}, "leader": {}})
            for m in dfs.LABEL_HORIZONS_MIN:
                rows.append({"row_kind": "LABEL", "shared_ai_call_id": cid, "horizon_min": m,
                             "tape_ok": True, "fwd_ret_bp": ret})
    return rows


def test_logistic_fit_recovers_a_planted_signal():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(2000, 2))
    y = (rng.random(2000) < 1 / (1 + np.exp(-2.0 * x[:, 0]))).astype(float)
    w = dmr.fit_logistic(x, y)
    assert w[1] > 1.5 and abs(w[2]) < 0.3


def test_noise_is_collected_then_killed_and_signal_passes_for_review():
    noise = dmr.build_report(_stream(8), [])
    h9 = noise["logistic_h9"]
    assert h9["oos_days"] == 5 and h9["verdict"]["status"] == "COLLECTING"
    killed = dmr.build_report(_stream(18), [])["logistic_h9"]
    assert killed["oos_days"] == 15 and killed["verdict"]["status"] == "KILL"
    strong = dmr.build_report(_stream(14, signal=1.0), [])["logistic_h9"]
    assert strong["verdict"]["status"] == "PASS_FOR_REVIEW" and strong["verdict"]["never_trades"] is True
    assert strong["ub95_brier_minus_climatology"] < 0 and strong["sign_trade_lb95_net_bp"] > 0


def test_walk_forward_never_trains_on_labels_that_mature_after_the_scored_day(monkeypatch):
    seen = []
    real = dmr.fit_logistic

    def spy(x, y, **kw):
        seen.append(len(y))
        return real(x, y, **kw)

    monkeypatch.setattr(dmr, "fit_logistic", spy)
    dmr.build_report(_stream(6, per_day=160), [])
    cutoff_rows = [sum(1 for i in range(160) if i * (DAY // 160) < DAY - 1800) + 160 * (d - 1)
                   for d in range(3, 6)]
    assert seen == cutoff_rows


def test_stream_health_and_regime_join():
    rows = _stream(1, per_day=4)
    regime_rows = [{"row_kind": "REGIME_PROMPT", "shared_ai_call_id": "c0-0", "call_state": "CALLED",
                    "decision_ts": rows[0]["decision_ts"], "facts": {"rv15_bp": 1.0},
                    "parsed": {"parse_status": "OK", "abstain": False, "trending": False,
                               "exhausted": False, "persistence": 0.6}},
                   {"row_kind": "REGIME_PROMPT", "shared_ai_call_id": "c0-1", "call_state": "SKIPPED_MIN_INTERVAL"}]
    report = dmr.build_report(rows, regime_rows)
    health = report["stream_health"]
    assert health["snapshots"] == 4 and health["labels"]["120"]["tape_ok"] == 4
    assert health["feature_set_versions"] == {dfs.FEATURE_SET_VERSION: 4}
    assert health["book_depth_collected"] == [False]
    h8 = report["regime_h8"]
    assert h8["call_states"] == {"CALLED": 1, "SKIPPED_MIN_INTERVAL": 1}
    assert h8["verdict"]["status"] == "COLLECTING"
    json.dumps(report)


def test_engine_publishes_the_report_and_lists_it(tmp_path, monkeypatch):
    assert engine.DECISION_MODEL_REPORT_FILE in engine.ANALYZER_JSON_REPORT_FILES
    monkeypatch.setattr(engine, "_agent_data_path", lambda name: str(tmp_path / name))
    written = {}
    monkeypatch.setattr(engine, "_write_aux_report", lambda name, payload, session: written.setdefault(name, payload))
    (tmp_path / dfs.SNAPSHOT_FILE).write_text(
        "\n".join(json.dumps(r) for r in _stream(1, per_day=3)) + "\n", encoding="utf-8")
    engine.decision_model_report(session={"collector_v22_epoch_id": ""})
    payload = written[engine.DECISION_MODEL_REPORT_FILE]
    assert payload["schema"] == dmr.SCHEMA and payload["stream_health"]["snapshots"] == 3
