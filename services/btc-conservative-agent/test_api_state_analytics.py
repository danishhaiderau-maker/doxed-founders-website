"""Unit tests for api_state_analytics (HM R2 nit): /api/state total_trades is the disk row count."""
import os

import api_state_analytics as asa


def test_counts_data_rows_excluding_header_and_blank_and_quoted_newlines(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text('id,note\na,"x\ny"\n\n,\nb,z\n')
    assert asa.disk_trade_rows(str(p)) == 2


def test_header_only_and_empty_file_are_zero(tmp_path):
    (tmp_path / "h.csv").write_text("id,note\n")
    (tmp_path / "e.csv").write_text("")
    assert asa.disk_trade_rows(str(tmp_path / "h.csv")) == 0
    assert asa.disk_trade_rows(str(tmp_path / "e.csv")) == 0


def test_cache_invalidated_on_change(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("id\na\n")
    assert asa.disk_trade_rows(str(p)) == 1
    p.write_text("id\na\nb\n")
    os.utime(p, ns=(1, 2_000_000_000_000_000_000))
    assert asa.disk_trade_rows(str(p)) == 2


def test_overlay_flags_mismatch_and_does_not_mutate_input(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("id\na\nb\n")
    live = {"total_trades": 5, "win_rate": 0.5}
    a, ts = asa.overlay(live, 123, str(p))
    assert live == {"total_trades": 5, "win_rate": 0.5}
    assert (a["total_trades"], a["total_trades_memory"], a["total_trades_disk"]) == (2, 5, 2)
    assert a["total_trades_mismatch"] is True and a["total_trades_source"] == "DISK_CSV" and ts == 123
    assert asa.overlay({"total_trades": 2}, None, str(p))[0]["total_trades_mismatch"] is False


def test_unreadable_is_none_never_stale(tmp_path):
    a, _ = asa.overlay({"total_trades": 9}, None, str(tmp_path / "missing.csv"))
    assert a["total_trades"] is None and a["total_trades_source"] == "UNAVAILABLE" and a["total_trades_mismatch"]
    a, _ = asa.overlay(None, None, str(tmp_path / "missing.csv"))
    assert a["total_trades_memory"] is None
