"""B3c tests: daycheck TSV of a synthetic store; any P1-receipt / row-pass mismatch -> 78; ext2_search-compatible
header; exclusive create; no staleness bound (RK 22ee55488e0c). Synthetic dicts only; outputs under --basetemp."""
import hashlib
import inspect
import ntpath

import pytest

from research_fulln480m import fulln_daycheck as dc
from research_fulln480m.fulln_closure import ClosureViolation

WID = "W02"


def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _entry(feed, day, rows=1000, salt=""):
    e0 = dc._day_epoch_ms(day)
    return {"path": ntpath.join(dc.BFX_DIR, feed, day + ".jsonl.gz"), "bytes": 5000 + rows,
            "sha256": _sha(feed + day + salt), "rows": rows, "first_ts_ms": e0 + 1000,
            "last_ts_ms": e0 + 86_399_000, "hours_covered": 24.0}


def _store(wid=WID):
    days = dc.window_days(wid)
    bfx = []
    for d in days:
        bfx.append({"day_utc": d, "trades": _entry("trades", d), "candles1m": _entry("candles1m", d, rows=1440),
                    "dup_ids": 0, "ts_monotonic": True, "inside_day": True, "max_trade_gap_s": 120.0, "bars": 1440,
                    "bar_minutes_match": True, "status": "PASS"})
    ch = [("binance/futures/um/daily/metrics", days[0],
           {"path": "C:\\DoxxedCrypto\\external\\ext2\\external-data\\binance\\futures\\um\\daily\\metrics\\BTCUSDT\\BTCUSDT-metrics-" + days[0] + ".zip",
            "bytes": 777, "sha256": _sha("m" + days[0]), "rows": 288, "first_ts_ms": 1, "last_ts_ms": 2, "hours_covered": 23.9})]
    files = []
    for r in bfx:
        for feed in ("trades", "candles1m"):
            files.append({"path": r[feed]["path"], "length": r[feed]["bytes"], "sha256": r[feed]["sha256"]})
    files.append({"path": ch[0][2]["path"], "length": 777, "sha256": ch[0][2]["sha256"]})
    receipt = {"schema": dc.P1_RECEIPT_SCHEMA, "files": files, "stops": []}
    return receipt, bfx, ch


def test_b3c_1_synthetic_store_tsv(tmp_path):
    receipt, bfx, ch = _store()
    a, b = str(tmp_path / "daycheck-W02.tsv"), str(tmp_path / "coverage-W02.tsv")
    out = dc.produce(WID, receipt, bfx, ch, a, b)
    assert out["daycheck_rows"] == 59 and out["coverage_rows"] == 2 * 59 + 1
    data = open(a, "rb").read()
    assert hashlib.sha256(data).hexdigest() == out["daycheck_sha256"] and out["daycheck_sha12"] == out["daycheck_sha256"][:12]
    assert b"\r" not in data and data.endswith(b"\n")
    assert data.split(b"\n")[0].decode().split("\t") == list(dc.DAYCHECK_HEADER)
    rows = dc.parse_daycheck(data)
    assert [r["day_utc"] for r in rows] == dc.window_days(WID)
    assert rows[0]["day_utc"] == "2025-01-01" and rows[-1]["day_utc"] == "2025-02-28"
    # ext2_search load_manifests semantics: expected sha12 per day file; Inputs.path checks h.startswith(e)
    for r, src in zip(rows, bfx):
        assert len(r["trades_file_sha256_12"]) == 12 and len(r["candles1m_file_sha256_12"]) == 12
        assert src["trades"]["sha256"].startswith(r["trades_file_sha256_12"])
        assert src["candles1m"]["sha256"].startswith(r["candles1m_file_sha256_12"])
        assert r["status"] == "PASS" and r["max_trade_gap_min"] == "2.0" and r["bar_minutes_match_trade_minutes"] == "yes"
    cov = dc.parse_daycheck(open(b, "rb").read())
    assert cov[0]["window_id"] == WID and cov[-1]["channel"] == "binance/futures/um/daily/metrics"
    assert cov[0]["sha256_12"] == bfx[0]["trades"]["sha256"][:12] and cov[0]["hours_covered"] == "24.0"


def test_b3c_2_sha_or_bytes_mismatch_stops(tmp_path):
    receipt, bfx, ch = _store()
    bfx[3]["trades"]["sha256"] = _sha("other")
    with pytest.raises(ClosureViolation) as ei:
        dc.build_rows(WID, dc.files_from_p1_receipt(receipt), bfx, ch)
    assert ei.value.exit_code == 78
    receipt, bfx, ch = _store()
    bfx[4]["candles1m"]["bytes"] += 1
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, dc.files_from_p1_receipt(receipt), bfx, ch)
    receipt, bfx, ch = _store()
    receipt["files"] = receipt["files"][1:]
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, dc.files_from_p1_receipt(receipt), bfx, ch)
    receipt, bfx, ch = _store()
    ch[0][2]["sha256"] = _sha("x")
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, dc.files_from_p1_receipt(receipt), bfx, ch)


def test_b3c_3_day_set_must_be_exact():
    receipt, bfx, ch = _store()
    p1 = dc.files_from_p1_receipt(receipt)
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, p1, bfx[:-1], ch)
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, p1, bfx + [bfx[0]], ch)
    extra = dict(bfx[0])
    extra["day_utc"] = "2025-03-01"
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, p1, bfx + [extra], ch)
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, p1, bfx, [("x", "2025-03-01", ch[0][2])])


def test_b3c_4_status_recomputed_with_pinned_gap_rule():
    receipt, bfx, ch = _store()
    p1 = dc.files_from_p1_receipt(receipt)
    bfx[2]["max_trade_gap_s"] = 3600.0
    rows, _ = dc.build_rows(WID, p1, bfx, ch)
    assert rows[2][-1] == "PASS"
    bfx[2]["max_trade_gap_s"] = 3600.5
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, p1, bfx, ch)  # loader says PASS, pinned rule says FAIL
    bfx[2]["status"] = "FAIL"
    rows, _ = dc.build_rows(WID, p1, bfx, ch)
    assert rows[2][-1] == "FAIL"
    receipt, bfx, ch = _store()
    bfx[5]["dup_ids"] = 1
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, dc.files_from_p1_receipt(receipt), bfx, ch)


def test_b3c_5_no_overwrite(tmp_path):
    receipt, bfx, ch = _store()
    a, b = str(tmp_path / "d.tsv"), str(tmp_path / "c.tsv")
    dc.produce(WID, receipt, bfx, ch, a, b)
    with pytest.raises(ClosureViolation):
        dc.produce(WID, receipt, bfx, ch, a, str(tmp_path / "c2.tsv"))


def test_b3c_6_never_under_external_root():
    with pytest.raises(ClosureViolation):
        dc.assert_output_path("C:\\DoxxedCrypto\\external\\ext2\\reports\\EXT2-DAYCHECK-x.tsv")
    with pytest.raises(ClosureViolation):
        dc.assert_output_path("c:/doxxedcrypto/EXTERNAL/x.tsv")
    dc.assert_output_path("C:\\DoxxedCrypto\\runs\\btc-fulln-480m-20261114\\results\\daycheck-W02.tsv")
    assert "assert_output_path(path)" in inspect.getsource(dc.write_new)


def test_b3c_7_p1_receipt_must_be_clean():
    receipt, bfx, ch = _store()
    receipt["stops"] = ["L106 LISTEN"]
    with pytest.raises(ClosureViolation):
        dc.files_from_p1_receipt(receipt)
    with pytest.raises(ClosureViolation):
        dc.files_from_p1_receipt({"schema": "other", "files": []})
    receipt, bfx, ch = _store()
    receipt["files"].append(dict(receipt["files"][0]))
    with pytest.raises(ClosureViolation):
        dc.files_from_p1_receipt(receipt)


def test_b3c_8_wrong_paths_refused():
    receipt, bfx, ch = _store()
    bfx[0]["trades"]["path"] = bfx[0]["trades"]["path"].replace("tBTCF0USTF0", "tBTCUSD")
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, dc.files_from_p1_receipt(receipt), bfx, ch)
    receipt, bfx, ch = _store()
    bfx[1]["trades"]["hours_covered"] = 25.0
    with pytest.raises(ClosureViolation):
        dc.build_rows(WID, dc.files_from_p1_receipt(receipt), bfx, ch)


def test_b3c_9_no_staleness_bound():
    import ast
    tree = ast.parse(inspect.getsource(dc))
    doc = ast.get_docstring(tree, clean=False)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != doc:
            names.append(node.value)
    assert not [n for n in names if "stale" in n.lower()]
    for fn in (dc.produce, dc.build_rows):
        assert not any("stale" in p for p in inspect.signature(fn).parameters)


def test_b3c_10_tsv_field_guard():
    with pytest.raises(ClosureViolation):
        dc.tsv_bytes(("a", "b"), [("x\ty", "z")])
    with pytest.raises(ClosureViolation):
        dc.tsv_bytes(("a", "b"), [("x",)])
