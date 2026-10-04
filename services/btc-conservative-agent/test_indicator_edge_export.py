"""Indicator Edge Strategist export: scoreboard + coverage + compact per-bar labels (analyzer mirror supplemental)."""

import json
import zipfile
from pathlib import Path

import indicator_edge_spec as spec
from research import indicator_edge_cycle as cycle
from research import indicator_edge_export as export
from research import indicator_edge_labels as labels
from research import indicator_edge_prereg as prereg
from test_indicator_edge_labels import T0, _line_tape, _row

REPO = Path(__file__).resolve().parents[2]


def _cycle(tmp_path, n=20):
    root = tmp_path / "ft"
    prereg.freeze_feature_set(root, now=T0 - 60, code_revision="test")
    rows = [_row(T0 + 600 + k * spec.BAR_SEC, score=1 if k % 2 else -1) for k in range(n)]
    rows[3]["f"]["CMO@F:TREND"] = [12.0, 0, 40.0, "A"]
    out = tmp_path / "o"
    res = cycle.run(str(tmp_path), str(out), root, tmp_path / "diag", now=T0 + 9 * 3600, tape=_line_tape(hours=8),
                    rows=rows)
    return res, out


def test_cycle_writes_one_fly_publishable_export_with_scoreboard_coverage_and_labels(tmp_path):
    res, out = _cycle(tmp_path)
    assert res["export"] == str(out / export.EXPORT_FILE) and res["export_label_rows"] == 20
    doc = json.loads((out / export.EXPORT_FILE).read_text())
    assert doc["schema"] == export.SCHEMA and doc["observation_only"] is True
    sb = doc["scoreboard"]
    assert sb["status"] == "OK" and sb["feature_set_sha"] == spec.feature_set_sha()
    assert sb["prereg"]["prereg_id"] and sb["label_counts"] and "top5" in sb
    assert sb["features"] and set(sb["features"][0]) == set(export.FEATURE_KEYS)
    assert "windows" not in sb["features"][0] and "status" in sb["combinations"]
    assert doc["coverage"]["schema"] == labels.COVERAGE_SCHEMA and len(doc["coverage"]["indicators"]) == 52
    lab = doc["labels"]
    assert lab["rows_total"] == lab["rows_exported"] == 20 and lab["truncated"] is False and lab["matured"] == 20
    # Fly's analyzer bundle validator accepts .json members only (no .jsonl) of <= 50 MB.
    assert export.EXPORT_FILE.endswith(".json") and (out / export.EXPORT_FILE).stat().st_size < 50 * 1024 * 1024


def test_expand_rebuilds_the_labels_jsonl_byte_for_byte(tmp_path):
    _, out = _cycle(tmp_path)
    original = (out / labels.LABEL_FILE).read_text()
    doc = json.loads((out / export.EXPORT_FILE).read_text())
    assert export.expand(doc) == [json.loads(line) for line in original.splitlines()]
    assert export.main(["--expand", str(out / export.EXPORT_FILE), "--out", str(tmp_path / "x.jsonl")]) == 0
    assert (tmp_path / "x.jsonl").read_text() == original


def test_compact_labels_keep_the_newest_rows_and_null_outcomes():
    rows = [{"schema": labels.SCHEMA, "bar_ts": T0 + k, "bar_close_ts": T0 + k + 180, "decision_ts": T0 + k + 195.0,
             "data_epoch_id": "ce-x", "feature_set_sha": "abc", "health_ok": True, "late": False,
             "prereg_eligible": None, "regime": {k2: None for k2 in labels.REGIME_KEYS},
             "sides": {"RSI@F:TREND": -1, "EMA_200@F:STATE": 0},
             "fwd": {"mid_bp": {"2": {"3": 1.5, "15": None, "60": None, "120": None},
                                "9": {"3": 1.0, "15": None, "60": None, "120": None}},
                     "exec_long_bp": {"3": -2.0, "15": None, "60": None, "120": None},
                     "exec_short_bp": {"3": -4.0, "15": None, "60": None, "120": None},
                     "mfe_bp": {"3": 2.0, "15": None, "60": None, "120": None},
                     "mae_bp": {"3": -1.0, "15": None, "60": None, "120": None}},
             "dir": {"3": 1, "15": None, "60": None, "120": None}, "matured": False} for k in range(5)]
    lab = export.compact_labels(rows, max_rows=3)
    assert lab["rows_total"] == 5 and lab["rows_exported"] == 3 and lab["truncated"] is True
    assert lab["dicts"]["data_epoch_id"] == ["ce-x"]
    back = export.expand({"labels": lab})
    assert back == rows[-3:]
    json.dumps(lab, allow_nan=False)


def test_segment_cycle_publishes_the_export_as_an_analyzer_mirror_supplemental():
    ps1 = (REPO / "scripts" / "run-segment-analyzer-cycle.ps1").read_text(encoding="utf-8")
    assert "indicator-edge\\indicator_edge_export.json" in ps1
    assert "$publishArgs += @('--supplemental', $IndicatorEdgeExport)" in ps1
