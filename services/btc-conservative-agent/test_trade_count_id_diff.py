import api_state_analytics as a


def _csv(tmp_path, ids):
    p = tmp_path / "t.csv"
    p.write_text("trade_id,x\n" + "".join(f"{i},1\n" for i in ids))
    return str(p)


def test_memory_only_ids_and_persistence(tmp_path):
    p = _csv(tmp_path, ["a"])
    mem = [{"trade_id": "a"}, {"trade_id": "gb1-x"}]
    d = a.id_diff(mem, p, 1000.0)
    assert d["memory_only_ids"] == ["gb1-x"] and d["disk_only_ids"] == [] and not d["persistent"]
    d = a.id_diff(mem, p, 1000.0 + a.MISMATCH_PERSIST_S)
    assert d["persistent"] and d["mismatch_age_s"] == a.MISMATCH_PERSIST_S


def test_in_sync_resets(tmp_path):
    p = _csv(tmp_path, ["a", "b"])
    a.id_diff([{"trade_id": "a"}], p, 1.0)
    d = a.id_diff([{"trade_id": "a"}, {"trade_id": "b"}], p, 500.0)
    assert d["memory_only_count"] == d["disk_only_count"] == 0 and d["mismatch_since_ts"] is None


def test_timer_restarts_after_resync(tmp_path):  # kills P3 (mismatch timer never reset)
    p = _csv(tmp_path, ["a"])
    bad = [{"trade_id": "a"}, {"trade_id": "x"}]
    a.id_diff(bad, p, 1.0)
    assert a.id_diff([{"trade_id": "a"}], p, 2.0)["mismatch_since_ts"] is None
    d = a.id_diff(bad, p, 1.0 + 10 * a.MISMATCH_PERSIST_S)
    assert d["mismatch_since_ts"] == 1.0 + 10 * a.MISMATCH_PERSIST_S and d["mismatch_age_s"] == 0 and not d["persistent"]


def test_unreadable_csv_is_labelled_not_listed(tmp_path):
    d = a.id_diff([{"trade_id": "a"}, {"trade_id": "b"}], str(tmp_path / "missing.csv"), 1.0)
    assert d["disk_status"] == "UNREADABLE" and d["memory_only_ids"] == [] and d["memory_only_count"] == 0
    assert d["mismatch_since_ts"] == 1.0
    assert a.id_diff([{"trade_id": "a"}], _csv(tmp_path, ["a"]), 1.0)["disk_status"] == "OK"


def test_cache_rereads_on_replace_and_not_when_unchanged(tmp_path, monkeypatch):
    p = _csv(tmp_path, ["a", "b"])
    assert a.disk_trade_ids(p) == {"a", "b"}
    calls = []
    real = a.csv.DictReader
    monkeypatch.setattr(a.csv, "DictReader", lambda *x, **k: calls.append(1) or real(*x, **k))
    a.disk_trade_ids(p)
    assert calls == []  # unchanged -> no re-read
    st = __import__("os").stat(p)
    q = tmp_path / "new.csv"
    q.write_text("trade_id,x\na,1\nc,1\n")  # same size, replaced (new inode), same mtime
    __import__("os").utime(q, ns=(st.st_atime_ns, st.st_mtime_ns))
    __import__("os").replace(q, p)
    assert a.disk_trade_ids(p) == {"a", "c"} and calls == [1]


def _parity_check(now):
    import bot
    return next(c for c in bot._system_health_fly_self_checks(now) if c["id"] == "fly.trade_count_parity")


def test_system_health_parity_amber_then_red_then_green(tmp_path, monkeypatch):  # kills P5 (RED -> AMBER)
    import bot
    p = _csv(tmp_path, ["a"])
    monkeypatch.setattr(bot, "CSV_TRADES", p)
    monkeypatch.setattr(bot, "trades", [{"trade_id": "a"}, {"trade_id": "gone"}])
    assert _parity_check(1000.0)["status"] == "AMBER"
    red = _parity_check(1000.0 + a.MISMATCH_PERSIST_S)
    assert red["status"] == "RED" and "gone" in red["observed"]
    monkeypatch.setattr(bot, "trades", [{"trade_id": "a"}])
    assert _parity_check(2000.0)["status"] == "GREEN"
    monkeypatch.setattr(bot, "CSV_TRADES", str(tmp_path / "missing.csv"))
    assert "UNREADABLE" in _parity_check(3000.0)["observed"]
