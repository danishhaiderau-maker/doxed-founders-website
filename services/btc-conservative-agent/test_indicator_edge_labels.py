"""Indicator Edge per-bar forward labels and the per-indicator coverage audit (offline, read-only)."""

import json
import math

import numpy as np
import pytest

import indicator_edge_spec as spec
from research import indicator_edge_cycle as cycle
from research import indicator_edge_labels as labels
from research import indicator_edge_prereg as prereg
from research import indicator_forward_scorer as sc
from strategy_lab import tape as tape_mod

T0 = 1_791_072_000          # 2026-10-04T00:00:00Z
FID = "RSI@F:TREND"


def _line_tape(hours=6, bp_per_min=1.0, spread_bp=0.0):
    n = hours * 3600
    mid = 100_000.0 * (1 + 1e-4 * bp_per_min * np.arange(n) / 60.0)
    half = mid * spread_bp / 2e4
    return tape_mod.build_tape(T0 + np.arange(n), mid - half, mid + half)


def _row(bar_ts, score=1, status="A", raw=55.0, ok=True, epoch="ce-test"):
    return {"schema": spec.BAR_SCHEMA, "feature_set_sha": spec.feature_set_sha(), "bar_ts": bar_ts,
            "bar_close_ts": bar_ts + spec.BAR_SEC, "ts": bar_ts + spec.BAR_SEC + 9, "late": False,
            "data_epoch_id": epoch, "health": {"ok": ok, "reasons": []},
            "regime": {"session": "ASIA", "vol_tercile": "MID", "trend_state": "RANGE", "ai_side": "LONG"},
            "f": {FID: [raw, score, 60.0, status], "ATR@F:STATE": [0.04, None, 50.0, "A"]}}


def test_label_rows_carry_forward_returns_direction_mfe_and_sides():
    tape = _line_tape()
    rows = [_row(T0 + 600), _row(T0 + 780, score=-1)]
    dts = np.array([sc.decision_ts(r) for r in rows])
    out = labels.label_rows(rows, sc.outcomes(tape, dts), dts)
    assert [r["schema"] for r in out] == [labels.SCHEMA] * 2
    first = out[0]
    for w in sc.WINDOWS:
        assert first["fwd"]["mid_bp"]["2"][str(w)] == pytest.approx(w, rel=1e-2)        # +1 bp per minute
        assert first["fwd"]["mid_bp"]["9"][str(w)] == pytest.approx(w, rel=1e-2)
        assert first["fwd"]["mfe_bp"][str(w)] == pytest.approx(w, rel=1e-2)
        assert first["fwd"]["mae_bp"][str(w)] == pytest.approx(0.0, abs=1e-6)
        assert first["dir"][str(w)] == 1
    assert first["matured"] is True and first["prereg_eligible"] is None
    assert first["sides"] == {FID: 1} and out[1]["sides"] == {FID: -1}                 # unscored ATR excluded
    assert first["regime"]["session"] == "ASIA" and first["data_epoch_id"] == "ce-test"
    assert first["decision_ts"] == pytest.approx(T0 + 600 + spec.BAR_SEC + 15)
    # hit and net move as documented: side * dir, side * mid - cost
    assert out[1]["sides"][FID] * out[1]["dir"]["15"] == -1


def test_unmatured_windows_are_null_and_never_nan():
    tape = _line_tape(hours=1)
    rows = [_row(T0 + 3600 - 30 * 60 - spec.BAR_SEC)]           # ~30 min of tape after the decision
    dts = np.array([sc.decision_ts(r) for r in rows])
    out = labels.label_rows(rows, sc.outcomes(tape, dts), dts)[0]
    assert out["fwd"]["mid_bp"]["2"]["15"] is not None
    assert out["fwd"]["mid_bp"]["2"]["60"] is None and out["fwd"]["mid_bp"]["2"]["120"] is None
    assert out["dir"]["120"] is None and out["matured"] is False
    json.dumps(out, allow_nan=False)                              # strict JSON


def test_build_labels_every_row_and_marks_prereg_eligibility(tmp_path):
    tape = _line_tape()
    rows = [_row(T0 + 600), _row(T0 + 780, ok=False)]
    out = labels.build(str(tmp_path), tape=tape, rows=rows)
    assert len(out) == 2 and all(r["prereg_eligible"] is None for r in out)
    root = tmp_path / "ft"
    assert prereg.freeze_feature_set(root, now=T0, code_revision="test")["status"] == "FROZEN"
    out = labels.build(str(tmp_path), tape=tape, rows=rows, prereg_root=root)
    assert [r["prereg_eligible"] for r in out] == [True, False]          # unhealthy row is labelled, not eligible
    assert [r["health_ok"] for r in out] == [True, False]
    assert labels.build(str(tmp_path), tape=None, rows=[]) == []


def test_write_jsonl_is_atomic_and_strict(tmp_path):
    path = labels.write_jsonl([{"a": 1}, {"b": None}], tmp_path / "x" / labels.LABEL_FILE)
    assert [json.loads(l) for l in open(path)] == [{"a": 1}, {"b": None}]
    assert not list((tmp_path / "x").glob("*.tmp"))
    with pytest.raises(ValueError):
        labels.write_jsonl([{"a": math.nan}], tmp_path / "y.jsonl")


def test_cycle_writes_labels_from_the_scorer_outcomes(tmp_path):
    root = tmp_path / "ft"
    prereg.freeze_feature_set(root, now=T0 - 60, code_revision="test")
    tape = _line_tape(hours=8)
    rows = [_row(T0 + 600 + k * spec.BAR_SEC, score=1 if k % 2 else -1) for k in range(20)]
    out = tmp_path / "o"
    res = cycle.run(str(tmp_path), str(out), root, tmp_path / "diag", now=T0 + 9 * 3600, tape=tape, rows=rows)
    assert res["status"] == "OK" and res["label_rows"] == 20 and res["label_rows_matured"] == 20
    got = [json.loads(l) for l in open(out / labels.LABEL_FILE)]
    assert len(got) == 20 and all(r["prereg_eligible"] is True for r in got)
    assert got[0]["fwd"]["mid_bp"]["2"]["60"] == pytest.approx(60, rel=1e-2)
    assert (out / sc.REPORT_FILE).exists()                                  # scoreboard unchanged


def test_cycle_without_eligible_rows_writes_no_labels(tmp_path):
    res = cycle.run(str(tmp_path), str(tmp_path / "o"), tmp_path / "none", tmp_path / "diag", now=T0, tape=None,
                    rows=[])
    assert res["status"] == "NOT_PREREGISTERED" and res["labels"] is None and res["label_rows"] == 0


# ------------------------------------------------------------- coverage audit

def test_coverage_lists_all_52_and_classifies_each_status():
    rows = []
    for k in range(10):
        r = _row(T0 + k * spec.BAR_SEC, score=1 if k % 3 else 0, raw=50.0 + k)
        f = r["f"]
        f["EMA_200@F:STATE"] = [10.0, 1, 50.0, "A"]                               # CONSTANT raw
        f["ZIGZAG_MSB@F:BREAKOUT"] = [None, 0, None, "A"]                         # DEAD: AVAILABLE, raw null
        f["PITCHFORK_12H@F:STATE"] = [None, 0, None, "A"]                         # legacy quiet regime -> WARMUP
        f["EMA_288@F:STATE"] = [None, None, None, "W"]                            # WARMUP
        f["FUNDING@F:REVERSION"] = [None, None, None, "U"]                        # UNAVAILABLE
        f["HV@F:REVERSION"] = [3.0 + k, 0, 50.0, "A"]                             # OK but silent
        rows.append(r)
    cov = labels.coverage(rows)
    by = {i["id"]: i for i in cov["indicators"]}
    assert cov["schema"] == labels.COVERAGE_SCHEMA and len(cov["indicators"]) == 52 and cov["rows"] == 10
    assert [i["num"] for i in cov["indicators"]] == [i["num"] for i in spec.INDICATORS]
    assert by["RSI"]["status"] == "OK" and by["RSI"]["distinct"] == 10 and by["RSI"]["pct_non_null"] == 100.0
    assert by["RSI"]["min"] == 50.0 and by["RSI"]["max"] == 59.0 and by["RSI"]["signals"] == 6
    assert by["EMA_200"]["status"] == "CONSTANT"
    assert by["ZIGZAG_MSB"]["status"] == "DEAD" and by["ZIGZAG_MSB"]["reason"] is None
    assert by["PITCHFORK_12H"]["status"] == "WARMUP" and by["PITCHFORK_12H"]["reason"] == "INSUFFICIENT_SWINGS"
    assert by["EMA_288"]["status"] == "WARMUP"
    assert by["FUNDING"]["status"] == "UNAVAILABLE"
    assert by["HV"]["status"] == "OK" and by["HV"]["silent"] is True
    assert by["ATR"]["signals"] is None and by["ATR"]["status"] == "CONSTANT"             # unscored label, same raw
    assert by["CVD"]["status"] == "MISSING" and by["CVD"]["present_rows"] == 0
    assert sum(cov["counts"].values()) == 52


def test_coverage_reads_stamped_insufficient_swings_as_warmup_not_dead():
    rows = []
    for k in range(10):
        r = _row(T0 + k * spec.BAR_SEC)
        if k < 8:   # current engine: WARMING_UP + status_reasons
            r["f"]["PITCHFORK_12H@F:STATE"] = [None, None, None, "W"]
            r["status_reasons"] = {"PITCHFORK_12H@F:STATE": "INSUFFICIENT_SWINGS"}
        else:       # legacy rows from before indicator_engine_v1_20261004b
            r["f"]["PITCHFORK_12H@F:STATE"] = [None, 0, None, "A"]
        rows.append(r)
    pf = {i["id"]: i for i in labels.coverage(rows)["indicators"]}["PITCHFORK_12H"]
    assert pf["status"] == "WARMUP" and pf["reason"] == "INSUFFICIENT_SWINGS"
    assert pf["reason_counts"] == {"INSUFFICIENT_SWINGS": 10} and pf["status_counts"] == {"WARMING_UP": 10}
    # Once swings exist the computed cells are AVAILABLE with a raw value and the indicator is OK again.
    rows[-1]["f"]["PITCHFORK_12H@F:STATE"] = [12.5, 1, None, "A"]
    pf = {i["id"]: i for i in labels.coverage(rows)["indicators"]}["PITCHFORK_12H"]
    assert pf["status"] == "OK" and pf["reason"] is None and pf["signals"] == 1


def test_coverage_cli_writes_the_audit(tmp_path, capsys):
    rows = [_row(T0 + k * spec.BAR_SEC, raw=40.0 + k) for k in range(3)]
    with open(tmp_path / spec.BAR_FILE, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    assert labels.main(["--data-dir", str(tmp_path), "--out-dir", str(tmp_path / "o"), "--coverage-only",
                        "--epoch", "ce-test"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["rows"] == 3 and "labels" not in res
    cov = json.loads((tmp_path / "o" / labels.COVERAGE_FILE).read_text())
    assert cov["data_epoch_ids"] == ["ce-test"] and len(cov["indicators"]) == 52
