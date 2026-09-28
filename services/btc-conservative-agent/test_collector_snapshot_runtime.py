"""Execute the real bot collector callers without importing network/runtime owners."""
import ast
import copy
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from collector_signal_snapshot import FIELDS, load_signal_snapshot
from collector_v22 import build_research_event

SIGNAL = 1_700_000_040.0
SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
NAMES = {"_collector_frozen_signal_ref", "_sync_order_multiverse", "persist_rejected_opportunity"}


def runtime(tmp_path):
    failures, upserts, writes = [], [], []
    def upsert(tid, payload, *, epoch_id, data_dir):
        assert data_dir == str(tmp_path)
        upserts.append(copy.deepcopy(payload))
    def write(record, *, data_dir):
        assert data_dir == str(tmp_path)
        writes.append(copy.deepcopy(record))
        return True, "test"
    def remove(tid, *, data_dir):
        assert data_dir == str(tmp_path)
    ns = dict(
        copy=copy, time=SimpleNamespace(time=lambda: SIGNAL + 10),
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda msg: failures.append(msg)),
        _collector_epoch_serialized=lambda fn: fn,
        _collector_source_in_current_epoch=lambda source: True,
        storage_blocks_new_events=lambda: False, event_already_written=lambda tid, **kw: False,
        paper_multiverse_trade_id=lambda *ids: next((x for x in ids if x), None),
        _execution_trade_is_terminal=lambda tid: False,
        _order_multiverse_written=set(), _order_multiverse_state={},
        _order_multiverse_pending_src={}, _order_multiverse_path_complete={},
        _order_multiverse_post_ttl_done={}, TERMINAL_LIFECYCLES=set(),
        _coerce_invert_on=lambda src: False,
        _collector_feature_snapshot=lambda src, **kw: {"adx": 22},
        _collector_cached_candles_1m=lambda **kw: [[(SIGNAL-120)*1000,100,101,99,100,1]],
        replay_lock=threading.RLock(), replay_buffers={}, state_lock=threading.RLock(),
        state={"last_cycle_3m_universe": {"rsi14": 42, "atr14_pct_3m": .3}},
        COLLECTOR_V22_PATH_REPLAY_1S=False, DETERMINISTIC_ENTRY_OFFSET_PCT=.003,
        BITFINEX_WS_SYMBOL="tBTCUSD", _collector_v22_epoch_id=lambda: "epoch-test",
        _data_sync_runtime_root=lambda: tmp_path,
        build_decision_tree_v22=lambda **kw: kw, build_research_event=build_research_event,
        terminal_observation=lambda obs: False,
        upsert_provisional_event=upsert, remove_provisional_event=remove,
        write_research_event_once=write,
        _safe_append_jsonl=lambda *a, **kw: None,
        ORDER_MULTIVERSE_FILE="unused", OPPORTUNITY_CAPTURE_FILE="unused",
    )
    nodes = [copy.deepcopy(node) for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in NAMES]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot-collector-callers", "exec"), ns)
    return ns, failures, upserts, writes


@pytest.mark.parametrize("rejected", [False, True])
def test_actual_runtime_callers_preserve_snapshot_through_rebuild_and_terminal(tmp_path, rejected):
    ns, failures, upserts, writes = runtime(tmp_path)
    fn = ns["persist_rejected_opportunity" if rejected else "_sync_order_multiverse"]
    source = {"trade_id": "event-test", "signal_price": 100, "created_ts_ts": SIGNAL,
              "final_direction": "LONG", "qty": .001, "status": "PENDING"}
    initial = fn(source)
    assert initial is not None, failures
    ref = initial["research_signal_snapshot_ref"]
    assert upserts[-1]["research_signal_snapshot_ref"] == ref
    frozen = load_signal_snapshot(ref, data_dir=tmp_path, event_id="event-test",
                                  epoch_id="epoch-test", signal_ts=SIGNAL)
    ns["state"]["last_cycle_3m_universe"] = {"rsi14": 99, "atr14_pct_3m": 9}
    ns["_collector_cached_candles_1m"] = lambda **kw: []
    ns["_collector_feature_snapshot"] = lambda *a, **kw: {"future": True}
    rebuilt = fn(source)
    assert rebuilt is not None, failures
    assert upserts[-1]["research_feature_snapshot"] == frozen["evidence"]["feature_snapshot_at_signal"]
    ns["terminal_observation"] = lambda obs: True
    final = fn(source)  # incoming order has no ref; original pending source owns it
    assert final is not None, failures
    assert final["research_signal_snapshot_ref"] == ref
    assert writes[-1]["research_signal_snapshot_ref"] == ref
    for field in FIELDS:
        assert final[field] == frozen["evidence"][field]
    assert len(list((tmp_path / "v3/signal_snapshots_v1").glob("*.json"))) == 1
    assert "research_signal_snapshot_ref" not in source
    assert not failures


def test_runtime_conflicting_ref_never_replaces_pending_owner(tmp_path):
    ns, _, _, _ = runtime(tmp_path)
    saved = {"sha256": "original"}
    ns["_order_multiverse_pending_src"]["event-test"] = {"research_signal_snapshot_ref": saved}
    with pytest.raises(ValueError, match="REFERENCE_CONFLICT"):
        ns["_collector_frozen_signal_ref"]({"research_signal_snapshot_ref": {"sha256": "replacement"}}, "event-test")
    assert ns["_collector_frozen_signal_ref"]({}, "event-test") == saved


def test_epoch_serialization_remains_on_runtime_callers():
    functions = {node.name: node for node in TREE.body if isinstance(node, ast.FunctionDef)}
    for name in ("_sync_order_multiverse", "persist_rejected_opportunity"):
        assert "_collector_epoch_serialized" in [ast.unparse(d) for d in functions[name].decorator_list]
