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
