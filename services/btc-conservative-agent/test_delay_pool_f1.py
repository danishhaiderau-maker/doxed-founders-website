"""REALISM_V2 F1 / HM B1+C2: the live-ack delay pool (persisted per DATA epoch, capped, cleared at reset, pool_ts
stamped) and the closed-row consequences (pool stamps carried, default-delay rows non_countable)."""
import ast
import json
import pathlib
import threading

import paper_fill as pf

ROOT = pathlib.Path(__file__).resolve().parent
EPOCH = "ce-20261004-v31-freeze21b"


class _LC:
    def __init__(self, d):
        self.d = pathlib.Path(d)

    def data_path(self, name):
        return self.d / name

    def _atomic_write_json(self, path, payload):
        pathlib.Path(path).write_text(json.dumps(payload), encoding="utf-8")
        return True


class _Log:
    def warning(self, *a, **k):
        pass


_TREE = []


def _tree():
    if not _TREE:
        _TREE.append(ast.parse((ROOT / "bot.py").read_text(encoding="utf-8")))
    return _TREE[0]


def _defines(n, name):
    if isinstance(n, ast.FunctionDef):
        return n.name == name
    if isinstance(n, ast.Assign):
        return any(getattr(t, "id", "") == name for t in n.targets)
    if isinstance(n, ast.AnnAssign):
        return getattr(n.target, "id", "") == name
    return False


def _ns(tmp, names, **extra):
    tree = _tree()
    ns = {"json": json, "threading": threading, "_live_copy": _LC(tmp), "logger": _Log(), "DATA_EPOCH_ID": EPOCH,
          "paper_fill": pf, **extra}
    for name in names:
        node = next(n for n in tree.body if _defines(n, name))
        exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    return ns


POOL_NAMES = ["_DISLOC_DELAYS", "PAPER_DELAY_POOL_FILE", "_PAPER_DELAY_POOL_LOCK", "_paper_delay_pool_path",
              "_paper_delay_sampler", "_paper_delay_pool_persist", "_paper_delay_pool_reset", "_disloc_delay",
              "_paper_delay_meta", "_DELAY_STAMP_KEYS", "_paper_stamp_pool", "_PAPER_EXIT_TRIGGERS",
              "_paper_note_exit_trigger", "_paper_delay_observe_report"]


def _fresh(tmp, monkeypatch):
    monkeypatch.setattr(pf, "DELAYS", None, raising=False)
    return _ns(tmp, POOL_NAMES)


def _report(i, lane="GS-01", sig=1000.0, ack=1000.4, fill=1000.5, stop=1000.9, exit_ack=None):
    tl = {"fly_signal_at_ts": sig, "exchange_ack_at_ts": ack, "fill_at_ts": fill, "stop_confirmed_at_ts": stop}
    if exit_ack is not None:
        tl["exit_ack_at_ts"] = exit_ack
    return {"correlation_id": f"t{i}", "lane": lane, "timeline": tl}


def test_sampler_rule_and_pool_ts():
    s = pf.DelaySampler(maxlen=200)
    assert s.describe("GS-01")["delay_source"] == "default_until_n10"
    assert s.describe("GS-01")["delay_unverified"] is True and s.describe("GS-01")["pool_ts"] is None
    for i in range(12):
        s.add("gs-01", "entry", 0.1 * (i + 1), measured_ts=2000.0 + i, trade_id=f"t{i}")
    d = s.describe("GS-01", "entry")
    assert d["delay_source"] == "lane_max_n12" and abs(d["delay_s"] - 1.2) < 1e-9
    assert d["pool_n"] == 12 and d["pool_ts"] == 2011.0 and d["quantile_method"] and d["delay_unverified"] is False
    for i in range(12, 25):
        s.add("GS-01", "entry", 0.1 * (i + 1), measured_ts=2000.0 + i, trade_id=f"t{i}")
    assert s.describe("GS-01", "entry")["delay_source"] == "lane_q90_n25"


def test_sampler_dedupe_cap_and_bounds():
    s = pf.DelaySampler(maxlen=5)
    assert s.add("A", "entry", 0.5, trade_id="x") == 0.5
    assert s.add("A", "entry", 0.6, trade_id="x") is None  # one sample per trade and leg
    assert s.add("A", "entry", -1) is None and s.add("A", "entry", 31) is None
    for i in range(10):
        s.add("A", "entry", 0.1, trade_id=f"y{i}")
    assert len(s._xs("A", "entry")) == 5  # capped


def test_persist_reload_epoch_reject_and_reset(tmp_path, monkeypatch):
    ns = _fresh(tmp_path, monkeypatch)
    for i in range(10):
        assert ns["_paper_delay_observe_report"](_report(i, ack=1000.0 + 0.1 * (i + 1)))["entry"] > 0
    path = tmp_path / "paper_delay_pool.json"
    payload = json.loads(path.read_text())
    assert payload["epoch_id"] == EPOCH and payload["schema"] == pf.POOL_SCHEMA
    # a restart restores the pool (pool_ts set)
    ns2 = _fresh(tmp_path, monkeypatch)
    meta = ns2["_paper_delay_meta"]("GS-01", "entry")
    assert meta["pool_n"] == 10 and meta["pool_ts"] and meta["delay_source"] == "lane_max_n10"
    # another DATA epoch ignores the file
    monkeypatch.setattr(pf, "DELAYS", None, raising=False)
    ns3 = _ns(tmp_path, POOL_NAMES)
    ns3["DATA_EPOCH_ID"] = "other-epoch"
    assert ns3["_paper_delay_meta"]("GS-01", "entry")["delay_source"] == "default_until_n10"
    # reset clears and persists empty
    ns2["_paper_delay_pool_reset"]()
    assert json.loads(path.read_text())["pools"] == {}
    assert ns2["_disloc_delay"]("GS-01", "entry")[1] == "default_until_n10"


def test_observe_three_legs_and_duplicates(tmp_path, monkeypatch):
    ns = _fresh(tmp_path, monkeypatch)
    ns["_paper_note_exit_trigger"]({"trade_id": "t1"}, 1100.0)
    out = ns["_paper_delay_observe_report"](_report(1, exit_ack=1100.7))
    assert abs(out["entry"] - 0.4) < 1e-9 and abs(out["exit"] - 0.7) < 1e-9 and abs(out["stop"] - 0.4) < 1e-9
    assert ns["_paper_delay_observe_report"](_report(1, exit_ack=1100.7)) == {}  # same trade: no second sample
    assert ns["_paper_delay_observe_report"]({"lane": "GS-01", "timeline": {}}) == {}  # no id -> nothing
    # an exit ack with no paper exit trigger is not a sample (never a paper signal->fill)
    assert "exit" not in ns["_paper_delay_observe_report"](_report(2, exit_ack=1100.7))


def test_stamp_only_when_source_matches(tmp_path, monkeypatch):
    ns = _fresh(tmp_path, monkeypatch)
    res = ns["_paper_stamp_pool"]({"delay_source": "default_until_n10"}, "GS-01", "entry")
    assert res["delay_unverified"] is True and res["pool_n"] == 0 and res["delay_flag"] == pf.UNVERIFIED_DELAY
    other = ns["_paper_stamp_pool"]({"delay_source": "EXCHANGE_RESIDENT_NO_DELAY"}, "GS-01", "entry")
    assert "pool_n" not in other


def _row_ns():
    import tile_fill as tf
    return _ns("/tmp", ["_entry_fill_sim", "_entry_priced_by_paper_fill", "_entry_pricing_fields", "_is_paper_book_lane",
                        "_delay_leg_unverified", "_non_countable_reasons"],
               tile_fill=tf, STATS_EXCLUDED_EXIT_REASONS={"FORCE_FLATTENED"})


def _pos(entry_src, exit_src):
    import tile_fill as tf
    lane = sorted(tf.MIGRATED_LANES)[0]
    efs = {"delay_source": entry_src, "fill_px": 100.0, **pf.delay_stamp(entry_src, 123.0)}
    xfs = {"delay_source": exit_src, **pf.delay_stamp(exit_src, 456.0)} if exit_src else None
    return {"research_lane": lane, "fill_sim": efs, "entry_fill_sim": efs, "exit_fill_sim": xfs}


def test_closed_row_carries_pool_stamps():
    ns = _row_ns()
    f = ns["_entry_pricing_fields"](_pos("lane_max_n12", "lane_q90_n25"), {})
    assert f["pool_n"] == 12 and f["pool_ts"] == 123.0 and f["quantile_method"] and f["delay_unverified"] is False
    assert f["exit_pool_n"] == 25 and f["exit_pool_ts"] == 456.0 and f["exit_delay_unverified"] is False
    assert "delay_flag" in f and "exit_delay_flag" in f


def test_default_delay_rows_are_non_countable():
    ns = _row_ns()
    r = ns["_non_countable_reasons"]
    assert r(_pos("lane_max_n12", "lane_q90_n25"), "TP") == []
    assert r(_pos("default_until_n10", "lane_q90_n25"), "TP") == ["DEFAULT_DELAY_ENTRY"]
    assert r(_pos("lane_max_n12", "default_until_n10"), "TIME") == ["DEFAULT_DELAY_EXIT"]
    # an exchange-resident exit (stop/TP rests on the book) carries no delay and stays countable
    assert r(_pos("lane_max_n12", "EXCHANGE_RESIDENT_NO_DELAY"), "SL") == []
    assert r(_pos("lane_max_n12", None), "FORCE_FLATTENED") == ["FORCE_FLATTENED"]
    # live rows (an exchange order id) are never re-judged on the paper delay pool
    live = {**_pos("default_until_n10", None), "bitfinex_order_id": 7}
    assert r(live, "TP") == []


def test_row_wiring():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert '"non_countable": bool(_non_countable_reasons(pos, exit_reason))' in src
    assert "_paper_delay_observe_report(report)" in src and "_paper_delay_pool_reset()" in src
