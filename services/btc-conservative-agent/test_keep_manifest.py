import os
import time

import integrity_gaps as ig
import keep_manifest as km

PINNED_LITERAL = "4751fd5e3575945c7cf6e54e592ae4fd98c98357810304679ff06307d0bfb172"


def test_manifest_matches_danish_1322_decision():
    assert "13:22" in km.DECISION and "KEEP" in km.DECISION
    assert "runtime/v3/**" in km.FLY and "keep-*.tgz" in km.FLY
    assert {"SignalCycle", "SignalCycleEvent", "SignalCycleParticipant"} == set(km.NEON_TABLES)
    for f in ("live_execution_reports.jsonl", "live_copy_approvals.jsonl", "lane_pnl_ledger.json",
              "execution_settings_history.jsonl"):
        assert km.is_kept(f"runtime/{f}"), f


def test_manifest_hash_is_pinned():
    assert km.MANIFEST_SHA256 == PINNED_LITERAL


def test_scan_reports_keep_separately_and_checks_tgz(tmp_path):
    old = time.time() - 30 * 86400
    (tmp_path / "runtime" / "v3" / "x").mkdir(parents=True)
    (tmp_path / "runtime" / "v3" / "x" / "s.json").write_text("{}")
    (tmp_path / "runtime" / "live_execution_reports.jsonl").write_text('{"ts": 100}\n')
    (tmp_path / "keep-20261013T002513.tgz").write_bytes(b"x")
    (tmp_path / "old-backup.tgz").write_bytes(b"x")
    (tmp_path / "runtime" / "book_tape_1s.jsonl").write_text('{"ts": 100}\n')
    for p in (tmp_path / "keep-20261013T002513.tgz", tmp_path / "old-backup.tgz"):
        os.utime(p, (old, old))
    got = ig.scan_local_oldest(str(tmp_path))
    assert set(got["files"]) == {"old-backup.tgz", "runtime/book_tape_1s.jsonl"}       # unlisted old -> offenders
    assert set(got["kept"]) == {"keep-20261013T002513.tgz", "runtime/live_execution_reports.jsonl"}
    assert got["kept_dirs"] == ["runtime/v3"]
