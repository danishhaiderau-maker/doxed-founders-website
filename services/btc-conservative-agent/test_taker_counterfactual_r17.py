"""R17: taker_signal_counterfactuals.jsonl writer is stopped (ticker-BBO priced, modelled latency)."""
import ast
import pathlib
import types

BOT = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
_TREE = ast.parse(BOT)
_WANTED = {
    "_sample_execution_markouts",
    "_capture_taker_signal_counterfactual",
    "_schedule_taker_signal_counterfactual",
    "_r17_retired_view",
    "execution_markout_status_snapshot",
}


def _load(extra):
    nodes = [n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name in _WANTED]
    assert {n.name for n in nodes} == _WANTED
    mod = ast.Module(body=nodes, type_ignores=[])
    ns = {"R17_RETIRED_STATUS": "disabled: R17", **extra}
    exec(compile(mod, "bot_r17_slice", "exec"), ns)
    return ns


class _Book:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.dropped = 0
        self.sampled = 0

    def sample(self, **_kw):
        self.sampled += 1
        return list(self.rows)

    def pending_count(self):
        return len(self.rows)


def _ns(writes, timers):
    em = types.SimpleNamespace(
        FILL_FILE="fill_markouts.jsonl",
        TAKER_FILE="taker_signal_counterfactuals.jsonl",
        TAKER_LATENCIES_SEC=(0.25, 1.0, 2.0),
        MARKOUT_HORIZONS_SEC=(1, 5),
        direction_sign=lambda d: 1 if d == "LONG" else (-1 if d == "SHORT" else 0),
    )

    class _Timer:
        def __init__(self, *a, **k):
            timers.append(a)

        def start(self):
            pass

    counters = {"fill_rows_written": 0, "taker_rows_written": 0, "write_failures": 0,
                "taker_signals_scheduled": 0, "taker_capture_failures": 0, "taker_r17_skipped": 0}
    return _load({
        "TAKER_SIGNAL_COUNTERFACTUAL_ENABLED": False,
        "execution_markouts": em,
        "_execution_markout_counters": counters,
        "_fill_markout_book": _Book([{"schema": "fill"}]),
        "_taker_counterfactual_book": _Book([{"schema": "taker"}]),
        "_safe_append_jsonl": lambda path, row, **_k: writes.append(path) or True,
        "threading": types.SimpleNamespace(Timer=_Timer),
        "time": types.SimpleNamespace(time=lambda: 1000.0),
        "_shared_ai_call_id": lambda **_k: "call-1",
        "active_tile_registry_signature": lambda: "sig",
        "globals": globals,
    })


def test_flag_is_off_and_documented():
    assert "TAKER_SIGNAL_COUNTERFACTUAL_ENABLED = False" in BOT
    assert "R17 (23 Oct): taker_signal_counterfactuals.jsonl is retired" in BOT


def test_schedule_starts_no_timers_and_counts_the_skip():
    writes, timers = [], []
    ns = _ns(writes, timers)
    ns["_schedule_taker_signal_counterfactual"]({}, {"decision": "LONG"}, "LONG")
    ns["_schedule_taker_signal_counterfactual"]({}, {"decision": "SHORT"}, "SHORT")
    assert timers == []
    c = ns["_execution_markout_counters"]
    assert c["taker_r17_skipped"] == 2 and c["taker_signals_scheduled"] == 0
    assert writes == []


def test_capture_never_writes_the_taker_file():
    writes, timers = [], []
    ns = _ns(writes, timers)
    ns["_capture_taker_signal_counterfactual"]("call-1", "LONG", 999.0, 0.25, {})
    assert writes == []
    assert ns["_execution_markout_counters"]["taker_r17_skipped"] == 1


def test_markout_sampling_writes_fill_rows_only():
    writes, timers = [], []
    ns = _ns(writes, timers)
    ns["_sample_execution_markouts"](100.0, 100.5, 100.2, 999.0)
    assert writes == ["fill_markouts.jsonl"]
    assert ns["_taker_counterfactual_book"].sampled == 0
    c = ns["_execution_markout_counters"]
    assert c["fill_rows_written"] == 1 and c["taker_rows_written"] == 0


def test_status_reports_retired_r17():
    ns = _ns([], [])
    view = ns["execution_markout_status_snapshot"]()["taker_signal_counterfactual"]
    assert view["enabled"] is False and view["status"] == "disabled: R17"
    assert view["source"] == "taker_signal_counterfactuals"


def test_ws_entry_call_site_still_goes_through_the_gate():
    # The signal path still calls the scheduler; the gate inside it is the stop.
    assert "_schedule_taker_signal_counterfactual(ctx, ai, final_direction)" in BOT
