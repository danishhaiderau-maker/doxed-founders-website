"""Archive scan (design v2 step 1): whole-file streaming scan, rotated parts, counts, guards. No network, no bot."""
import json
import os

import pytest

import monitor_archive_scan as mas

GOOD = {"fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "fill_model_version": 1, "exit_status": "PAPER_FILL",
        "fresh_eligible": True}


def _guard(rev=lambda: "r1", **kw):
    clock = {"t": 0.0}
    return mas.Guard(rev, now=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                     cpu_time=lambda: 0.0, **kw), clock


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_scan_reads_whole_file_not_just_the_tail_and_labels_the_book_partial(tmp_path):
    rows = [{"s": 1792000000 + i, "b": [[1, 1]], "a": [[2, 1]], "pad": "x" * 600} for i in range(6000)]  # > 3.6 MB
    _jsonl(tmp_path / "bfx_book_10l_1s.jsonl", rows)
    assert (tmp_path / "bfx_book_10l_1s.jsonl").stat().st_size > 3 * 1024 * 1024
    guard, _ = _guard()
    out = mas.run_scan(tmp_path, guard)
    f = next(x for x in out["files"] if x["id"] == "bfx_book_10l_1s")
    assert f["rows"] == 6000 and f["min_ts"] == 1792000000 and f["max_ts"] == 1792005999
    assert f["completeness"].startswith("PARTIAL: book stream starts at 2026-10-14T17:46:40")
    assert f["bytes"] == (tmp_path / "bfx_book_10l_1s.jsonl").stat().st_size and f["parse_errors"] == 0


def test_rotated_parts_shadow_and_disloc_label_are_covered(tmp_path):
    _jsonl(tmp_path / "paper_ioc_no_fill_shadow.jsonl", [{"event": "PAPER_SHADOW_NO_FILL", "ts": 1792100000}])
    _jsonl(tmp_path / "paper_ioc_no_fill_shadow.jsonl.1", [{"event": "PAPER_SHADOW_NO_FILL", "ts": 1791900000}] * 2)
    _jsonl(tmp_path / "disloc_trades.jsonl", [{"event": "CLOSE", "ts": 1792100001, "fill_model_version": None}])
    _jsonl(tmp_path / "bfx_trades.jsonl.2", [{"s": 1791000000}])
    out = mas.run_scan(tmp_path, _guard()[0])
    by = {(x["id"], x["part"]): x for x in out["files"]}
    assert by[("paper_ioc_no_fill_shadow", 0)]["rows"] == 1 and by[("paper_ioc_no_fill_shadow", 1)]["rows"] == 2
    assert by[("paper_ioc_no_fill_shadow", 1)]["min_ts"] == 1791900000
    assert by[("disloc_trades", 0)]["label"] == "legacy-pricing" and by[("disloc_trades", 0)]["legacy"] == 1
    assert ("bfx_trades", 2) in by


def test_csv_paper_rows_get_fresh_legacy_forced_and_version_counts(tmp_path):
    import csv
    cols = ["ts", "trade_id", "fill_model", "fill_model_version", "exit_status", "fresh_eligible",
            "fresh_data_excluded", "legacy_pricing"]
    rows = [["2026-10-17T02:00:00", "a", GOOD["fill_model"], "1", "PAPER_FILL", "True", "False", "False"],
            ["2026-10-17T02:01:00", "b", GOOD["fill_model"], "1", "FORCED_NO_BOOK", "False", "True", "False"],
            ["2026-10-17T02:02:00", "c", GOOD["fill_model"], "1", "PAPER_FILL_FORCED", "False", "True", "False"],
            ["2026-10-12T02:03:00", "d", "AI_DIRECT", "", "PAPER_FILL", "False", "False", "True"]]
    with open(tmp_path / "trades_3factor.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    f = next(x for x in mas.run_scan(tmp_path, _guard()[0])["files"] if x["id"] == "trades_3factor")
    assert f["rows"] == 4 and (f["fresh"], f["legacy"], f["forced"]) == (1, 1, 2)
    assert f["fill_model_version_counts"] == {"1": 3, "none": 1}
    assert f["min_ts"] < f["max_ts"] and f["starts_after_scope_start"] is False


def test_snapshot_by_byte_length_ignores_rows_appended_after_the_start(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    _jsonl(p, [{"s": 1792000000 + i} for i in range(10)])
    files, _ = mas.discover(tmp_path)
    with p.open("a") as fh:
        fh.write(json.dumps({"s": 1799999999}) + "\n" + '{"s": 17')  # appended + a torn line after the snapshot
    r = mas.scan_file(tmp_path, files[0], _guard()[0])
    assert r["rows"] == 10 and r["max_ts"] == 1792000009 and r["trailing_partial_bytes"] == 0


def test_credential_looking_files_are_skipped_and_never_opened(tmp_path, monkeypatch):
    _jsonl(tmp_path / "deploy_receipt.json", [{"ok": 1}])
    (tmp_path / "bitfinex_token_deploy_receipt.json").write_text('{"secret": "x"}')
    opened = []
    real = type(tmp_path).open
    monkeypatch.setattr(type(tmp_path), "open", lambda self, *a, **k: (opened.append(self.name), real(self, *a, **k))[1])
    out = mas.run_scan(tmp_path, _guard()[0])
    assert out["excluded_name_matches"] == 1
    assert "bitfinex_token_deploy_receipt.json" not in {f["file"] for f in out["files"]} | set(opened)
    assert mas.excluded("config-7002.json") and mas.excluded("bitfinex_live_state.json") and mas.excluded("x.env")
    assert not mas.excluded("bfx_book_10l_1s.jsonl")


def test_scan_never_writes_or_changes_the_data_dir(tmp_path):
    _jsonl(tmp_path / "bfx_trades.jsonl", [{"s": 1792000000}])
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    mas.run_scan(tmp_path, _guard()[0])
    assert before == {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in tmp_path.iterdir()}


def test_throttle_paces_to_2mb_per_s():
    guard, clock = _guard()
    for _ in range(10):
        guard.after_chunk(1024 * 1024)
    assert clock["t"] == pytest.approx(5.0, abs=0.01) and mas.THROTTLE_BPS == 2 * 1024 * 1024


def test_abort_on_git_rev_change_and_on_three_cpu_samples_over_60_percent(tmp_path):
    rev = {"v": "a"}
    guard, _ = _guard(rev=lambda: rev["v"])
    guard.after_chunk(10)
    rev["v"] = "b"
    with pytest.raises(mas.Aborted, match="git_rev"):
        guard.after_chunk(10)
    t = {"now": 0.0, "cpu": 0.0}

    def sleep(s):
        t["now"] += s
        t["cpu"] += s * 0.9  # 90% busy while "sleeping" in the fake clock
    g = mas.Guard(lambda: "r", now=lambda: t["now"], sleep=sleep, cpu_time=lambda: t["cpu"], rate_bps=1024 * 1024)
    with pytest.raises(mas.Aborted, match="CPU"):
        for _ in range(40):
            g.after_chunk(1024 * 1024)
    ok_t = {"now": 0.0}
    calm = mas.Guard(lambda: "r", now=lambda: ok_t["now"], sleep=lambda s: ok_t.__setitem__("now", ok_t["now"] + s),
                     cpu_time=lambda: 0.1 * ok_t["now"], rate_bps=1024 * 1024)
    for _ in range(40):
        calm.after_chunk(1024 * 1024)  # 10% CPU never aborts


def test_start_is_single_flight_and_reports_done_or_aborted(tmp_path):
    mas.reset_for_tests()
    _jsonl(tmp_path / "bfx_trades.jsonl", [{"s": 1792000000}])
    jobs = []
    assert mas.start(tmp_path, lambda: "r", runner=jobs.append) is True
    assert mas.start(tmp_path, lambda: "r", runner=jobs.append) is False  # still running
    assert mas.state()["status"] == "running"
    jobs[0]()
    st = mas.state()
    assert st["status"] == "done" and st["scan"]["files"][0]["rows"] == 1
    mas.reset_for_tests()
    flip = iter(["a", "a", "b", "b", "b"])
    big = [{"s": 1792000000 + i, "pad": "y" * 900} for i in range(3000)]
    _jsonl(tmp_path / "bfx_trades.jsonl", big)
    mas.start(tmp_path, lambda: next(flip, "b"), runner=lambda j: j())
    assert mas.state()["status"] == "aborted" and "git_rev" in mas.state()["abort_reason"]
    mas.reset_for_tests()
