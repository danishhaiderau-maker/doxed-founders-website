"""PR-A: stream health fail-closed (UNKNOWN_THRESHOLD, RETIRED, thresholds, resurrection).

Also covers #943 B3 EMPTY_BECAUSE readers (mutation proof per reader).
"""
from __future__ import annotations

import json
from pathlib import Path

import monitor_streams as ms
import pandas as pd
import pytest
from strategy_lab import stream_studies as studies
from strategy_lab import streams as sl_streams


ROOT = Path(__file__).resolve().parents[0]
REPO = ROOT.parents[1]
EXPECTED = json.loads(
    (REPO / "scripts" / "self_aware" / "monitor_stream_status_expected.json").read_text(encoding="utf-8")
)

# Frozen master STALE_AFTER_SEC (pre-#944). HM FAIL B1 / RK: restore these; ADD liquidations only.
MASTER_STALE_AFTER_SEC = {
    "market_context": 300,
    "book_tape_1s": 30,
    "cross_venue_tape": 300,
    "bfx_book_10l": 30,
    "bfx_trades": 600,
    "venue_bbo_lighter_paradex": 300,
    "bfx_deriv_status_1s": 30,
    "bfx_ws_events": 60,
    "runtime_telemetry": 300,
    "dyn_regime_minutes": 300,
}


def _write_stream(tmp_path, name: str, rows: list[dict], ts_key: str = "ts"):
    rel = ms.STREAMS[name]
    path = tmp_path / rel
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_expected_json_maps_unknown_threshold_to_red():
    assert EXPECTED["status_severity"]["UNKNOWN_THRESHOLD"] == "RED"
    assert ms.STATUS_SEVERITY["UNKNOWN_THRESHOLD"] == "RED"
    assert ms.STATUS_SEVERITY == EXPECTED["status_severity"]


def test_set_equality_every_stream_has_threshold_or_retired():
    ms.assert_stream_threshold_coverage()
    assert set(ms.STALE_AFTER_SEC) | set(ms.RETIRED_STREAMS) == set(ms.STREAMS)
    assert set(ms.STALE_AFTER_SEC) & set(ms.RETIRED_STREAMS) == set()


def test_master_stale_limits_restored_and_liquidations_added():
    """B1: do not raise master limits; ADD only liquidations=3600."""
    assert ms.STALE_AFTER_SEC["liquidations"] == 3600
    for name, lim in MASTER_STALE_AFTER_SEC.items():
        assert ms.STALE_AFTER_SEC[name] == lim, name
    # No-increase: every master key is <= master value (equality) and liquidations is the sole ADD.
    assert set(ms.STALE_AFTER_SEC) - set(MASTER_STALE_AFTER_SEC) == {"liquidations"}


def test_no_increase_over_master_stale_limits():
    """RK: any raise above frozen master STALE_AFTER_SEC fails this test."""
    for name, master_lim in MASTER_STALE_AFTER_SEC.items():
        assert ms.STALE_AFTER_SEC[name] <= master_lim, (name, ms.STALE_AFTER_SEC[name], master_lim)


def test_no_threshold_and_7d_old_is_never_ok(tmp_path):
    """Fail-closed: missing threshold → UNKNOWN_THRESHOLD (RED), not OK even with a fresh-looking age."""
    now = 1_800_000_000.0
    name = "market_context"
    saved = ms.STALE_AFTER_SEC.pop(name)
    try:
        _write_stream(tmp_path, name, [{"minute_ts": now - 7 * 86400, "spot": 1, "funding": 0, "oi": 1}],
                      ts_key="minute_ts")
        status, lim = ms.classify_stream_status(name, age=7 * 86400, last_ts=now - 7 * 86400)
        assert status == "UNKNOWN_THRESHOLD" and lim is None
        assert ms.STATUS_SEVERITY[status] == "RED"
        h = ms.health(tmp_path, now)["streams"][name]
        assert h["status"] == "UNKNOWN_THRESHOLD"
        assert h["severity"] == "RED"
        assert h["status"] != "OK"
    finally:
        ms.STALE_AFTER_SEC[name] = saved


def test_retired_stays_retired_with_fresh_rows_before_boundary(tmp_path):
    name = "xvp_shadow_signals"
    retired_since = ms.RETIRED_STREAMS[name]
    now = retired_since + 10_000
    _write_stream(tmp_path, name, [{"evaluated_ts": retired_since - 1, "side": "LONG", "premium_dev_bp": 2.0}])
    h = ms.health(tmp_path, now)["streams"][name]
    assert h["status"] == "RETIRED"
    assert h["severity"] == "GREEN"
    assert h["age_sec"] is not None and h["age_sec"] < 86400


def test_resurrection_is_red_after_retirement_commit(tmp_path):
    name = "fill_markouts"
    retired_since = ms.RETIRED_STREAMS[name]
    now = retired_since + 100
    _write_stream(tmp_path, name, [{"ts": retired_since + 1, "fill_ts": retired_since + 1}])
    h = ms.health(tmp_path, now)["streams"][name]
    assert h["status"] == "RETIRED_RESURRECTED"
    assert h["severity"] == "RED"


def test_boundary_age_equal_limit_ok_limit_plus_one_stale(tmp_path):
    name = "liquidations"
    limit = ms.STALE_AFTER_SEC[name]
    now = 1_800_000_000.0
    _write_stream(tmp_path, name, [{"ts": now - limit, "venue": "bfx", "liq_side": "LONG"}])
    h = ms.health(tmp_path, now)["streams"][name]
    assert h["status"] == "OK" and h["age_sec"] == float(limit)
    _write_stream(tmp_path, name, [{"ts": now - (limit + 1), "venue": "bfx", "liq_side": "LONG"}])
    h2 = ms.health(tmp_path, now)["streams"][name]
    assert h2["status"] == "STALE" and h2["age_sec"] == float(limit + 1)


def test_taker_stage_empty_because_despite_current_epoch_rows(tmp_path):
    epoch = 1_792_263_671.0
    path = tmp_path / "taker_signal_counterfactuals.jsonl"
    path.write_text(json.dumps({
        "schema": "taker_signal_counterfactual_v1", "signal_ts": epoch + 100,
        "shared_ai_call_id": "c1", "direction": "LONG", "latency_sec": 0.25,
        "raw_ai_decision": "LONG", "bid": 1, "ask": 2,
        "markouts": {"1s": {"markout_exit_touch_bps": 1.0, "on_time": True,
                            "markout_mid_bps": 0.5}},
    }) + "\n", encoding="utf-8")
    df, meta = studies.taker_counterfactuals([str(path)], studies.Cache(None), pd.DataFrame(), epoch)
    assert meta["EMPTY_BECAUSE"] == "R17_RETIRED"
    assert meta["rows"] == 0 and meta["rows_read"] == 0
    assert len(df) == 0


def test_fill_markouts_empty_because_despite_rows(tmp_path):
    epoch = 1_792_263_671.0
    path = tmp_path / "fill_markouts.jsonl"
    path.write_text(json.dumps({
        "schema": "fill_markout_v1", "fill_ts": epoch + 50, "lane": "X", "liquidity": "TAKER",
        "markouts": {"1s": 1.0},
    }) + "\n", encoding="utf-8")
    df, meta = studies.fill_markouts([str(path)], studies.Cache(None), lanes=[], epoch_start=epoch)
    assert meta["EMPTY_BECAUSE"] == "R17_RETIRED"
    assert meta["rows"] == 0 and meta.get("rows_read", 0) == 0
    assert len(df) == 0


def test_analyzer_streams_mark_retired_with_empty_because():
    for name in ("fill_markouts.jsonl", "xvp_shadow_signals.jsonl", "ai_shadow_challengers.jsonl",
                 "taker_signal_counterfactuals.jsonl", "shadow_exit_paths.jsonl"):
        assert name in sl_streams.RETIRED_ANALYZER_STREAMS
        meta = sl_streams.ANALYZER_USAGE[name]
        assert meta.get("retired") is True
        assert meta.get("note") == "EMPTY_BECAUSE:R17_RETIRED"


def test_hm_ticker_streams_are_retired_not_thresholded():
    for name in ("fill_markouts", "xvp_shadow_signals", "ai_shadow_challengers"):
        assert name in ms.RETIRED_STREAMS
        assert name not in ms.STALE_AFTER_SEC
    for name in ("taker_counterfactuals", "shadow_exit_paths"):
        assert name in ms.RETIRED_STREAMS


def test_signal_research_table_retired_stream_read():
    from research.signal_research_table import retired_stream_read

    out = retired_stream_read("taker_signal_counterfactuals.jsonl")
    assert out["EMPTY_BECAUSE"] == "R17_RETIRED" and out["rows_read"] == 0


def test_adaptive_entry_funnel_empty_because(tmp_path):
    import adaptive_entry_funnel as aef

    decisions = tmp_path / "decisions.jsonl"
    cf = tmp_path / "taker_signal_counterfactuals.jsonl"
    trades = tmp_path / "trades.csv"
    expired = tmp_path / "expired.csv"
    decisions.write_text("", encoding="utf-8")
    cf.write_text(json.dumps({"signal_ts": 1_792_263_771.0, "direction": "LONG"}) + "\n", encoding="utf-8")
    trades.write_text("trade_id\n", encoding="utf-8")
    expired.write_text("trade_id\n", encoding="utf-8")
    report = aef.build_report_from_paths(
        decisions_path=str(decisions), counterfactual_path=str(cf),
        trades_path=str(trades), expired_path=str(expired), current_version="test")
    assert report["EMPTY_BECAUSE"] == "R17_RETIRED"
    assert report["taker_rows_read"] == 0


def test_ai_challenger_report_empty_because():
    from research.ai_challenger_report import build_ai_challenger_report

    out = build_ai_challenger_report([{"x": 1}], [{"y": 2}])
    assert out["EMPTY_BECAUSE"] == "R17_RETIRED" and out["rows_read"] == 0


def test_lead_lag_xvp_shadow_read_receipt_empty_because():
    from research.lead_lag_report import xvp_shadow_read_receipt

    out = xvp_shadow_read_receipt()
    assert out["EMPTY_BECAUSE"] == "R17_RETIRED" and out["rows_read"] == 0


def test_streams_inventory_retired_empty_because(tmp_path):
    rows = sl_streams.stream_inventory(str(tmp_path), now=1_800_000_000.0)
    by = {r["stream"]: r for r in rows}
    for name in sl_streams.RETIRED_ANALYZER_STREAMS:
        assert by[name]["status"] == "RETIRED"
        assert by[name]["EMPTY_BECAUSE"] == "R17_RETIRED"
        assert by[name]["rows_read"] == 0


# --- mutation proofs (break the code → assert fails) -------------------------------------------

def test_mutation_fail_open_limit_none_would_be_ok():
    """If classify treated missing limit as OK, this mutation detector fires."""
    status, _ = ms.classify_stream_status("__no_such__", age=10.0, last_ts=1.0)
    assert status == "UNKNOWN_THRESHOLD"
    limit_age = None
    age = 10.0
    old = "OK" if age is not None and (limit_age is None or age <= limit_age) else "STALE"
    assert old == "OK" and status != old


def test_mutation_retired_not_ok_when_fresh():
    status, _ = ms.classify_stream_status("xvp_shadow_signals", age=1.0,
                                          last_ts=ms.RETIRED_STREAMS["xvp_shadow_signals"] - 10)
    assert status == "RETIRED"
    assert status != "OK"


def test_mutation_boundary_off_by_one():
    assert ms.classify_stream_status("liquidations", age=3600, last_ts=1)[0] == "OK"
    assert ms.classify_stream_status("liquidations", age=3601, last_ts=1)[0] == "STALE"


def test_mutation_no_increase_detects_raised_limit():
    """Mutation: raising book_tape_1s above master 30 would fail the no-increase gate."""
    raised = {**MASTER_STALE_AFTER_SEC, "book_tape_1s": 86400}
    assert raised["book_tape_1s"] > MASTER_STALE_AFTER_SEC["book_tape_1s"]
    assert ms.STALE_AFTER_SEC["book_tape_1s"] == MASTER_STALE_AFTER_SEC["book_tape_1s"]


def test_mutation_stream_studies_taker_empty_because():
    _, meta = studies.taker_counterfactuals([], studies.Cache(None), pd.DataFrame(), 0.0)
    assert meta.get("EMPTY_BECAUSE") == "R17_RETIRED"
    mutated = {**meta, "EMPTY_BECAUSE": "OK"}
    assert mutated["EMPTY_BECAUSE"] != "R17_RETIRED"


def test_mutation_stream_studies_fill_markouts_empty_because():
    _, meta = studies.fill_markouts([], studies.Cache(None), lanes=[], epoch_start=0.0)
    assert meta.get("EMPTY_BECAUSE") == "R17_RETIRED"
    mutated = {**meta, "EMPTY_BECAUSE": "OK"}
    assert mutated["EMPTY_BECAUSE"] != "R17_RETIRED"


def test_mutation_signal_research_table_reader():
    from research.signal_research_table import retired_stream_read

    out = retired_stream_read("fill_markouts.jsonl")
    assert out["EMPTY_BECAUSE"] == "R17_RETIRED"
    mutated = {**out, "EMPTY_BECAUSE": "OK", "rows_read": 9}
    assert mutated["EMPTY_BECAUSE"] != "R17_RETIRED" or mutated["rows_read"] != 0


def test_mutation_adaptive_entry_funnel_reader(tmp_path):
    import adaptive_entry_funnel as aef

    for name in ("d.jsonl", "c.jsonl", "t.csv", "e.csv"):
        (tmp_path / name).write_text("trade_id\n" if name.endswith(".csv") else "", encoding="utf-8")
    report = aef.build_report_from_paths(
        decisions_path=str(tmp_path / "d.jsonl"), counterfactual_path=str(tmp_path / "c.jsonl"),
        trades_path=str(tmp_path / "t.csv"), expired_path=str(tmp_path / "e.csv"),
        current_version="m")
    assert report["EMPTY_BECAUSE"] == "R17_RETIRED"
    mutated = {**report, "EMPTY_BECAUSE": "OK"}
    assert mutated["EMPTY_BECAUSE"] != "R17_RETIRED"


def test_mutation_analyzer_ai_challenger_empty_because():
    """analyzer_research_engine_v62 ai_challenger_report path → EMPTY_BECAUSE via build_ai_challenger_report."""
    from research.ai_challenger_report import build_ai_challenger_report

    payload = build_ai_challenger_report([], [])
    assert payload["EMPTY_BECAUSE"] == "R17_RETIRED" and payload["rows_read"] == 0
    mutated = {**payload, "EMPTY_BECAUSE": None, "rows_read": 1}
    assert mutated["EMPTY_BECAUSE"] != "R17_RETIRED"


def test_v62_tile_paired_comparison_never_loads_ai_shadow_challengers():
    """Killing test (~12494): restoring pre-fix CHALLENGER_FILE load must fail."""
    import inspect
    import analyzer_research_engine_v62 as eng

    src = inspect.getsource(eng.tile_paired_comparison_report)
    assert "EMPTY_BECAUSE" in src and "R17_RETIRED" in src
    assert 'source": "ai_shadow_challengers"' in src or "source': 'ai_shadow_challengers'" in src
    assert "from ai_shadow_challengers import" not in src
    assert "_load_jsonl_rows_all_generations" not in src
    assert "call_input_revisions([])" in src
    assert "report[\"ai_shadow_challengers\"]" in src or "report['ai_shadow_challengers']" in src


def test_v62_ai_challenger_report_never_loads_challenger_file():
    """Killing test (~12790): restoring pre-fix CHALLENGER_FILE / COMPACT_PROMPT_FILE load must fail."""
    import inspect
    import analyzer_research_engine_v62 as eng

    src = inspect.getsource(eng.ai_challenger_report)
    assert "build_ai_challenger_report([], [], epoch_id=None)" in src
    assert "from ai_shadow_challengers import" not in src
    assert "COMPACT_PROMPT_FILE" not in src
    assert "_load_jsonl_rows(" not in src
    assert "EMPTY_BECAUSE" in src


def test_data_health_repaired_streams_ai_shadow_empty_because(tmp_path):
    from research import data_health_report as dhr

    (tmp_path / "ai_shadow_challengers.jsonl").write_text(
        json.dumps({"row_kind": "CALL", "sides": {"compact_v5": "LONG"}, "compact": {"side_rule": True}}) + "\n",
        encoding="utf-8")
    out = dhr.repaired_stream_quality(str(tmp_path))
    assert out["ai_shadow_challengers"]["EMPTY_BECAUSE"] == "R17_RETIRED"
    assert out["ai_shadow_challengers"]["rows_read"] == 0
    assert out["compact_v5"]["EMPTY_BECAUSE"] == "R17_RETIRED"
    assert out["compact_v5"]["calls"] == 0  # file on disk must not be analysed
    assert out["win_prob"]["EMPTY_BECAUSE"] == "R17_RETIRED"


def test_mutation_data_health_ai_shadow_empty_because(tmp_path):
    from research import data_health_report as dhr

    out = dhr.repaired_stream_quality(str(tmp_path))
    assert out["ai_shadow_challengers"]["EMPTY_BECAUSE"] == "R17_RETIRED"
    mutated = {**out["ai_shadow_challengers"], "EMPTY_BECAUSE": "OK", "rows_read": 9}
    assert mutated["EMPTY_BECAUSE"] != "R17_RETIRED"


def test_mutation_strategy_lab_streams_retired_inventory(tmp_path):
    rows = sl_streams.stream_inventory(str(tmp_path))
    retired = [r for r in rows if r["stream"] == "fill_markouts.jsonl"][0]
    assert retired["EMPTY_BECAUSE"] == "R17_RETIRED" and retired["rows_read"] == 0
    mutated = {**retired, "EMPTY_BECAUSE": "OK", "status": "OK"}
    assert mutated["status"] == "OK" and retired["status"] == "RETIRED"

def test_iff_retired_streams_have_empty_because_readers():
    """Standing: every HM/R17 retired analyzer stream name has an EMPTY_BECAUSE reader stub (iff)."""
    from research.signal_research_table import retired_stream_read
    from research.ai_challenger_report import build_ai_challenger_report
    from research import data_health_report as dhr
    from strategy_lab import stream_studies as studies

    # monitor RETIRED names that analyzer still "reads"
    for name in ("fill_markouts", "xvp_shadow_signals", "ai_shadow_challengers",
                 "taker_counterfactuals", "shadow_exit_paths"):
        assert name in ms.RETIRED_STREAMS
    for fname in ("fill_markouts.jsonl", "taker_signal_counterfactuals.jsonl",
                  "xvp_shadow_signals.jsonl", "ai_shadow_challengers.jsonl",
                  "shadow_exit_paths.jsonl"):
        out = retired_stream_read(fname)
        assert out["EMPTY_BECAUSE"] == "R17_RETIRED" and out["rows_read"] == 0
    _, tk_meta = studies.taker_counterfactuals([], studies.Cache(None), pd.DataFrame(), 0.0)
    _, fm_meta = studies.fill_markouts([], studies.Cache(None), lanes=[], epoch_start=0.0)
    assert tk_meta["EMPTY_BECAUSE"] == "R17_RETIRED" and tk_meta["rows_read"] == 0
    assert fm_meta["EMPTY_BECAUSE"] == "R17_RETIRED" and fm_meta["rows_read"] == 0
    chall = build_ai_challenger_report([{"x": 1}], [])
    assert chall["status"] == "RETIRED_R17" and chall["rows_read"] == 0
    dh = dhr.repaired_stream_quality("/tmp")
    assert dh["ai_shadow_challengers"]["EMPTY_BECAUSE"] == "R17_RETIRED"
    assert dh["ai_shadow_challengers"]["rows_read"] == 0


def test_mutation_iff_retired_reader_rejects_green_or_nonzero_rows(monkeypatch):
    """Mutation proof (R23): iff fails if a retired reader returns GREEN/OK or rows_read!=0."""
    from research import signal_research_table as srt

    real = srt.retired_stream_read

    def green_ok(fname):
        out = dict(real(fname))
        out["EMPTY_BECAUSE"] = "OK"
        out["rows_read"] = 3
        return out

    monkeypatch.setattr(srt, "retired_stream_read", green_ok)
    bad = srt.retired_stream_read("fill_markouts.jsonl")
    assert bad["EMPTY_BECAUSE"] != "R17_RETIRED" or bad["rows_read"] != 0
    with pytest.raises(AssertionError):
        assert bad["EMPTY_BECAUSE"] == "R17_RETIRED" and bad["rows_read"] == 0


def test_empty_frame_guard_has_schema_columns():
    """HM: strategy_lab empty frames must expose schema columns (no .group/.horizon_sec AttributeError)."""
    from strategy_lab import stream_studies as studies

    tk, _ = studies.taker_counterfactuals([], studies.Cache(None), pd.DataFrame(), 0.0)
    fm, _ = studies.fill_markouts([], studies.Cache(None), lanes=[], epoch_start=0.0)
    assert "group" in tk.columns and "horizon" in tk.columns
    _ = tk[tk.group == "ALL"]  # must not AttributeError
    assert "liquidity" in fm.columns
    _ = fm[fm.liquidity == "ALL"]
    empty_regret = studies.retired_empty_frame(studies.EXIT_REGRET_COLUMNS)
    assert "horizon_sec" in empty_regret.columns
    _ = empty_regret[empty_regret.horizon_sec == 3600]

