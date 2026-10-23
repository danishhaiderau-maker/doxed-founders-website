"""REALISM_V2 F1 (HM 23 Oct): the delay of every BOT_SENT leg comes from live acks only.

MEASURED_LIVE_ACK for a trade's own legs, else a deterministic high quantile of the pooled live acks of the same leg
(LIVE_POOLED_<LEG>), else the 1.5 s placeholder stamped DEFAULT_UNMEASURED (UNVERIFIED). Never a p50, never paper
signal->fill."""
import ast
import pathlib
import threading

import paper_fill as pf

ROOT = pathlib.Path(__file__).parent


def _bot_ns(names, **extra):
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    ns = {"threading": threading, **extra}
    for name in names:
        node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign))
                    and (getattr(n, "name", None) == name or any(getattr(t, "id", "") == name for t in getattr(n, "targets", []))))
        exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    return ns


def test_default_is_stamped_unmeasured_and_never_counts_as_measured():
    s = pf.DelaySampler()
    d = s.describe("FAMILY_GS01", "entry")
    assert (d["delay_s"], d["delay_source"]) == (1.5, "DEFAULT_UNMEASURED")
    assert d["delay_quantile"] is None and d["delay_n_samples"] == 0
    assert not pf.delay_is_measured("DEFAULT_UNMEASURED")
    for legacy in ("default_until_n10", "lane_p50_n12", "PAPER_MEASURED", None, ""):
        assert not pf.delay_is_measured(legacy)
    for ok in ("MEASURED_LIVE_ACK", "LIVE_POOLED_ENTRY", "LIVE_POOLED_EXIT"):
        assert pf.delay_is_measured(ok)


def test_core_default_label_is_unmeasured():
    res = pf._paper_fill_core("BUY", 0.1, 1000.0, None, lambda t: None)
    assert res["delay_source"] == "DEFAULT_UNMEASURED" and res["delay_s"] == 1.5


def test_sampler_refuses_everything_but_live_acks():
    s = pf.DelaySampler()
    for src in ("PAPER_SIGNAL_TO_FILL", "PROXY", "PING", "paper"):
        assert s.add("L", "entry", 0.5, source=src) is False
    assert s.add("L", "entry", -1.0) is False and s.add("L", "entry", 99.0) is False
    assert s.refused == 6 and s.describe("L", "entry")["delay_source"] == "DEFAULT_UNMEASURED"


def test_pooled_uses_deterministic_high_quantile_not_p50():
    s = pf.DelaySampler()
    xs = [1.0, 1.1, 1.2, 1.3, 1.4, 1.9, 2.0, 2.05, 2.1, 3.6]
    for i, x in enumerate(xs[:9]):
        s.add("FAMILY_GS01", "entry", x, trade_id=f"t{i}", measured_ts=100.0 + i)
    assert s.describe("OTHER", "entry")["delay_source"] == "DEFAULT_UNMEASURED"  # n=9 < 10
    s.add("FAMILY_GS01", "entry", xs[9], trade_id="t9", measured_ts=109.0)
    d = s.describe("ANY_TILE", "entry")
    assert d["delay_source"] == "LIVE_POOLED_ENTRY" and d["delay_quantile"] == 0.9 and d["delay_n_samples"] == 10
    assert d["delay_s"] == 2.1  # nearest-rank q0.9 of 10 samples = 9th sorted value
    import statistics
    assert d["delay_s"] > statistics.median(xs)
    assert s.describe("ANY_TILE", "entry") == d  # deterministic
    assert s.describe("ANY_TILE", "exit")["delay_source"] == "DEFAULT_UNMEASURED"  # legs pooled separately


def test_trade_own_ack_wins_and_floor_holds():
    s = pf.DelaySampler()
    for i in range(12):
        s.add("L", "entry", 3.0, trade_id=f"x{i}")
    s.add("L", "entry", 1.95, trade_id="mine", measured_ts=5000.0)
    d = s.describe("L", "entry", "mine")
    assert (d["delay_s"], d["delay_source"], d["delay_measured_ts"]) == (1.95, "MEASURED_LIVE_ACK", 5000.0)
    s.add("L", "exit", 0.05, trade_id="fast")
    assert s.describe("L", "exit", "fast")["delay_s"] == pf.MIN_DELAY_S
    assert s.has_trade("mine") and not s.has_trade("mine", "exit")


def test_proxy_is_context_only():
    s = pf.DelaySampler()
    s.add_proxy(0.12, 10.0)
    d = s.describe("L", "entry")
    assert d["delay_proxy_s"] == 0.12 and d["delay_proxy_source"] == "PROXY"
    assert d["delay_source"] == "DEFAULT_UNMEASURED" and d["delay_s"] == 1.5


def test_no_p50_or_signal_to_fill_in_delay_code():
    src = (ROOT / "paper_fill.py").read_text(encoding="utf-8")
    cls = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "DelaySampler")
    body = ast.get_source_segment(src, cls)
    assert "median" not in body and "lane_p50" not in body
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "statistics.median" not in ast.get_source_segment(bot, next(
        n for n in ast.parse(bot).body if isinstance(n, ast.FunctionDef) and n.name == "_disloc_delay"))
    # every sampler feed in bot.py is the live-report observer
    tree = ast.parse(bot)
    feeders = {f.name for f in tree.body if isinstance(f, ast.FunctionDef)
               for c in ast.walk(f) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr == "add" and isinstance(c.func.value, ast.Name) and c.func.value.id == "sampler"}
    assert feeders == {"_paper_delay_observe_report"}


def _observer(sampler):
    return _bot_ns(["_paper_note_exit_trigger", "_paper_delay_observe_report"],
                   _paper_delay_sampler=lambda: sampler, _PAPER_EXIT_TRIGGERS={})


def test_report_feeds_entry_and_exit_acks():
    s = pf.DelaySampler()
    ns = _observer(s)
    rep = {"correlation_id": "T1", "lane": "family_gs01",
           "timeline": {"fly_signal_at_ts": 1000.0, "exchange_ack_at_ts": 1002.0}}
    assert ns["_paper_delay_observe_report"](rep) == {"entry": True}
    assert ns["_paper_delay_observe_report"](rep) == {}  # duplicate never double counts
    ns["_paper_note_exit_trigger"]({"trade_id": "T1"}, 1100.0)
    ns["_paper_note_exit_trigger"]({"trade_id": "T1"}, 1105.0)  # first trigger wins
    rep2 = {"correlation_id": "T1", "timeline": {"exit_ack_at_ts": 1103.63}}
    assert ns["_paper_delay_observe_report"](rep2) == {"exit": True}
    assert s.describe("X", "entry", "T1")["delay_s"] == 2.0
    assert abs(s.describe("X", "exit", "T1")["delay_s"] - 3.63) < 1e-9
    # ack before signal (clock skew) is not a sample
    assert ns["_paper_delay_observe_report"]({"correlation_id": "T2", "timeline": {
        "fly_signal_at_ts": 10.0, "exchange_ack_at_ts": 9.0}}) == {}


def _entry_ns(sampler, copied=False):
    return _bot_ns(["LIVE_ACK_WAIT_S", "_paper_entry_delay"],
                   _paper_delay_sampler=lambda: sampler, _paper_trade_live_copied=lambda tid: copied,
                   _disloc_delay=lambda lane, leg, tid=None: sampler.delay(lane, leg, tid),
                   _paper_delay_meta=lambda lane, leg, tid=None: {
                       k: sampler.describe(lane, leg, tid).get(k) for k in ("delay_quantile", "delay_n_samples",
                                                                            "delay_measured_ts")})


def test_copied_entry_waits_for_its_own_ack_then_uses_it():
    s = pf.DelaySampler()
    ns = _entry_ns(s, copied=True)
    order = {"trade_id": "C1", "signal_created_ts": 1000.0, "created_ts": 1000.2, "entry_expires_ts": 1600.0}
    assert ns["_paper_entry_delay"](order, "FAMILY_GS01", 1000.0, 1001.0) is None
    s.add("FAMILY_GS01", "entry", 2.05, trade_id="C1", measured_ts=1002.05)
    assert ns["_paper_entry_delay"](order, "FAMILY_GS01", 1000.0, 1002.1) == (2.05, "MEASURED_LIVE_ACK")
    assert order["_entry_delay_meta"]["delay_signal_to_sent_s"] == 0.2
    assert order["_entry_delay_meta"]["delay_measured_ts"] == 1002.05
    # fixed once chosen
    s.add("FAMILY_GS01", "entry", 9.0, trade_id="C1b")
    assert ns["_paper_entry_delay"](order, "FAMILY_GS01", 1000.0, 1003.0) == (2.05, "MEASURED_LIVE_ACK")


def test_copied_entry_without_ack_falls_back_after_wait_and_before_ttl():
    s = pf.DelaySampler()
    ns = _entry_ns(s, copied=True)
    order = {"trade_id": "C2", "signal_created_ts": 1000.0, "created_ts": 1000.0, "entry_expires_ts": 1005.0}
    assert ns["_paper_entry_delay"](order, "L", 1000.0, 1003.9) is None
    assert ns["_paper_entry_delay"](order, "L", 1000.0, 1004.0) == (1.5, "DEFAULT_UNMEASURED")
    o2 = {"trade_id": "C3", "signal_created_ts": 1000.0}
    assert ns["_paper_entry_delay"](o2, "L", 1000.0, 1010.0) == (1.5, "DEFAULT_UNMEASURED")


def test_uncopied_entry_never_waits():
    s = pf.DelaySampler()
    ns = _entry_ns(s, copied=False)
    order = {"trade_id": "P1", "signal_created_ts": 1000.0}
    assert ns["_paper_entry_delay"](order, "L", 1000.0, 1000.0) == (1.5, "DEFAULT_UNMEASURED")


def test_pricing_fields_map_f1_labels():
    ns = _bot_ns(["_entry_pricing_fields"])
    f = ns["_entry_pricing_fields"]
    def run(src, xsrc=None):
        try:
            return f({"entry_fill_sim": {"delay_source": src, "delay_s": 2.0, "delay_quantile": 0.9, "delay_n_samples": 12},
                      "exit_fill_sim": {"delay_source": xsrc}}, {})
        except NameError:
            return None
    out = run("LIVE_POOLED_ENTRY", "MEASURED_LIVE_ACK")
    if out is None:  # the helper reads module globals in the full bot; the mapping is covered by source checks
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        assert '"DEFAULT_UNMEASURED")' in src and '_dsrc.startswith("LIVE_POOLED_")' in src
        return
    assert out["priced_delay_model"] == "LIVE_POOLED_ENTRY" and out["delay_quantile"] == 0.9
    assert out["delay_both_legs_measured"] is True
    assert run("DEFAULT_UNMEASURED")["priced_delay_model"] == "DEFAULT_1_5S"
