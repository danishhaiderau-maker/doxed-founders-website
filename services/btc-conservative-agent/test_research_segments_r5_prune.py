"""R5 segment-store retention (RK 2026-10-23 18:10/18:11): live, no custody/ACK gate.

Below the cap: age prune, keeping un-ACKed segments an open lifecycle / pre-registered
test needs. At the cap: oldest first regardless of ship/ACK state, dropped seqs in the
capped receipt (cap_receipts.jsonl), RED on the shipper status.
"""

from __future__ import annotations

import ast
import json
import os

import pytest

import research_segment_prune as prune_mod
from test_research_segments import _rows
from test_research_segments_volume import VolumeEnv

DAY = 86400.0


@pytest.fixture()
def venv(tmp_path):
    env = VolumeEnv(tmp_path)
    yield env
    env.close()


def _ship_n(venv, n: int) -> None:
    for i in range(n):
        venv.write(f"s{i}.jsonl", _rows(i * 10, 10))
        venv.ship_all()


def _seg_dir(venv):
    return venv.store_root / "v1" / "seg"


def _seqs(venv) -> list[int]:
    return sorted(int(p.name.removesuffix(".tar.gz")) for p in _seg_dir(venv).glob("*.tar.gz"))


def _age(venv, seq: int, seconds: float) -> None:
    path = _seg_dir(venv) / f"{seq:012d}.tar.gz"
    os.utime(path, (venv.clock[0] - seconds, venv.clock[0] - seconds))


def _shipper(venv, **attrs):
    shipper = venv.shipper()
    shipper.sink = "volume"
    for key, value in attrs.items():
        setattr(shipper, key, value)
    return shipper


def _receipts(venv) -> list[dict]:
    path = venv.runtime / "cap_receipts.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def test_r5_pass_needs_no_env_switch_mode_custody_or_ack(venv, monkeypatch):
    monkeypatch.delenv("RESEARCH_SEGMENTS_PRUNE_ENABLED", raising=False)
    _ship_n(venv, 3)
    assert _seqs(venv) == [1, 2, 3]
    _age(venv, 1, 8 * DAY)
    shipper = _shipper(venv)
    assert shipper.prune_mode() == "off"  # custody-gated path is off; R5 still runs
    out = shipper.r5_segment_pass()
    assert out["result"]["age_dropped_seqs"] == [1]
    assert _seqs(venv) == [2, 3]
    # manifests (hash chain) stay
    assert len(list((venv.store_root / "v1" / "man").glob("*.json"))) == 3
    # seq 1 was never ACKed -> RED, since-time recorded
    status = json.loads(shipper.status_path.read_text())
    assert prune_mod.R5_RED_UNACKED in status["r5_segment_red"]
    assert status["r5_segment_red"][prune_mod.R5_RED_UNACKED]["since"] == venv.clock[0]
    assert _receipts(venv)[-1]["age_dropped_seqs"] == [1]


def test_below_cap_keeps_unacked_needed_segment_drops_other_aged(venv):
    _ship_n(venv, 3)
    for seq in (1, 2):
        _age(venv, seq, 8 * DAY)
    out = _shipper(venv).r5_segment_pass(keep_seqs=[2])
    assert out["result"]["age_dropped_seqs"] == [1]
    assert out["plan"]["kept_needed_unacked"] == [2]
    assert _seqs(venv) == [2, 3]


def test_young_segments_stay_below_cap(venv):
    _ship_n(venv, 3)
    out = _shipper(venv).r5_segment_pass()
    assert out["result"]["deleted"] == 0 and _seqs(venv) == [1, 2, 3]
    assert _receipts(venv) == []


def test_at_cap_drops_oldest_first_regardless_logs_ids_and_raises_red(venv):
    _ship_n(venv, 4)
    store = int(venv.shipper().load_state()["store_bytes"])
    shipper = _shipper(venv, max_store_bytes=store)  # exactly at the cap
    out = shipper.r5_segment_pass(keep_seqs=[1, 2, 3, 4])  # keep does not protect at the cap
    dropped = out["result"]["cap_dropped_seqs"]
    assert dropped and dropped == list(range(1, len(dropped) + 1))  # oldest first
    assert _seqs(venv) == [s for s in (1, 2, 3, 4) if s not in dropped]
    status = json.loads(shipper.status_path.read_text())
    assert prune_mod.R5_RED_CAP in status["r5_segment_red"]
    assert status["r5_segment_last_cap_dropped_seqs"] == dropped
    assert int(shipper.load_state()["store_bytes"]) <= int(store * prune_mod.R5_SEGMENT_CAP_TARGET)
    receipt = _receipts(venv)[-1]
    assert receipt["kind"] == "r5_segment_store" and receipt["cap_dropped_seqs"] == dropped
    assert prune_mod.R5_RED_CAP in receipt["red"]


def test_store_cap_reached_clears_after_r5_pass_so_shipping_resumes(venv):
    _ship_n(venv, 3)
    store = int(venv.shipper().load_state()["store_bytes"])
    shipper = _shipper(venv, max_store_bytes=store)
    venv.write("late.jsonl", _rows(500, 5))
    assert shipper.cycle()["store_cap_reached"] is True
    shipper.r5_segment_pass()
    assert json.loads(shipper.status_path.read_text()).get("last_error") is None
    result = shipper.cycle()
    assert not result.get("store_cap_reached") and result["shipped"] is not None


def test_never_touches_in_flight_seq_above_shipped(venv):
    _ship_n(venv, 2)
    stray = _seg_dir(venv) / f"{3:012d}.tar.gz"
    stray.write_bytes(b"x" * 10)
    os.utime(stray, (venv.clock[0] - 30 * DAY, venv.clock[0] - 30 * DAY))
    _shipper(venv, max_store_bytes=1).r5_segment_pass()
    assert stray.exists()


def test_red_hold_expires(venv):
    import research_segment_shipper as shipper_mod
    _ship_n(venv, 2)
    _age(venv, 1, 8 * DAY)
    shipper = _shipper(venv)
    shipper.r5_segment_pass()
    assert json.loads(shipper.status_path.read_text())["r5_segment_red"]
    venv.clock[0] += shipper_mod.R5_RED_HOLD_SECONDS + 1
    shipper.r5_segment_pass()
    assert json.loads(shipper.status_path.read_text())["r5_segment_red"] == {}


def test_receipt_ids_are_capped(tmp_path):
    items = []
    for seq in range(1, prune_mod.R5_RECEIPT_MAX_IDS + 51):
        path = tmp_path / f"{seq:012d}.tar.gz"
        path.write_bytes(b"x")
        items.append({"seq": seq, "path": str(path), "size": 1})
    plan = {"acked_seq": 10 ** 9, "store_bytes": len(items), "max_store_bytes": 1, "shipped_seq": len(items),
            "age_drop": [], "cap_drop": items}
    captured = []
    out = prune_mod.execute_r5_segment_prune(plan, receipt_dir=tmp_path,
                                             receipt_writer=lambda d, row: captured.append(row))
    assert out["deleted"] == len(items) and len(captured) == 1
    assert len(captured[0]["cap_dropped_seqs"]) == prune_mod.R5_RECEIPT_MAX_IDS
    assert captured[0]["ids_truncated"] is True


def test_shipper_main_loop_runs_r5_between_cycles_not_inside():
    source = open(os.path.join(os.path.dirname(__file__), "research_segment_shipper.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    cycle = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "cycle")
    assert "r5_segment_pass" not in ast.unparse(cycle)
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.unparse(main)
    assert "shipper.r5_segment_pass(keep_seqs=shipper.r5_keep_seqs())" in body


def test_c3_r5_pass_runs_before_the_low_disk_check():
    """HM C3: under LOW_DISK the loop `continue`s, so the R5 pass must run first or it never frees space."""
    source = open(os.path.join(os.path.dirname(__file__), "research_segment_shipper.py"), encoding="utf-8").read()
    main = source[source.index("def main("):]
    loop = main[main.index("while "):]
    assert loop.index("r5_pass()") < loop.index("LOW_DISK")


# ---------------------------------------------------------------- HM C3-R (re-review 23 Oct 21:10)
class _StopLoop(Exception):
    pass


def _crash_mid_ship_intent(shipper, seq: int, *, corrupt: bool = False) -> None:
    """Spool an intent like write_intent does, then 'crash' before complete_intent."""
    state = dict(shipper.load_state())
    state["seq"] = seq
    shipper.write_intent(b"segment-bytes", b"{}", state)
    if corrupt:
        (shipper.intent_dir / "segment.tar.gz").write_bytes(b"torn write")


def _run_one_low_disk_loop(monkeypatch, shipper) -> None:
    import research_segment_shipper as mod
    monkeypatch.setenv("RESEARCH_SEGMENTS_ENABLED", "1")
    monkeypatch.setattr(mod, "shipper_from_env", lambda environ=None: shipper)
    monkeypatch.setattr(mod, "_acquire_single_instance", lambda state_dir: object())
    monkeypatch.setattr(mod, "_set_priority", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "_free_bytes", lambda path: 0)  # LOW_DISK: the ship cycle is skipped

    def stop(_seconds):
        raise _StopLoop

    monkeypatch.setattr(mod.time, "sleep", stop)
    with pytest.raises(_StopLoop):
        mod.main()


@pytest.mark.parametrize("kind", ["pending", "stale", "corrupt", "garbage"])
def test_c3r_crash_mid_ship_plus_low_disk_still_prunes(venv, monkeypatch, kind):
    """Behavioural: a crash mid-ship leaves intent.json, then LOW_DISK skips the cycle that
    would complete it. The real main loop must still run R5 and free space; only the
    intent's own seq is protected and the spool is left untouched."""
    _ship_n(venv, 4)
    assert _seqs(venv) == [1, 2, 3, 4]
    for seq in (1, 2, 3, 4):
        _age(venv, seq, 8 * DAY)
    shipper = _shipper(venv)
    if kind == "pending":
        _crash_mid_ship_intent(shipper, 5)            # committed + 1 (not yet in the store)
        protected = set()
    elif kind == "stale":
        _crash_mid_ship_intent(shipper, 2)            # complete_intent would raise "stale intent"
        protected = {2}
    elif kind == "corrupt":
        _crash_mid_ship_intent(shipper, 3, corrupt=True)  # complete_intent would raise "corrupt"
        protected = {3}
    else:
        shipper.intent_dir.mkdir(parents=True, exist_ok=True)
        (shipper.intent_dir / "intent.json").write_text("{not json")
        protected = set()
    spool_before = sorted(p.name for p in shipper.intent_dir.iterdir())
    _run_one_low_disk_loop(monkeypatch, shipper)
    assert set(_seqs(venv)) == protected
    status = json.loads(shipper.status_path.read_text())
    assert status["last_error"] == "LOW_DISK_SKIPPED"
    assert status["r5_segment_intent"]["state"] == {"pending": "PENDING", "stale": "STALE",
                                                    "corrupt": "CORRUPT", "garbage": "CORRUPT"}[kind]
    assert status["r5_segment_last_deleted"] == 4 - len(protected)
    assert sorted(p.name for p in shipper.intent_dir.iterdir()) == spool_before  # nothing discarded


def test_c3r_intent_seq_is_excluded_from_the_cap_pass_too(venv):
    _ship_n(venv, 4)
    shipper = _shipper(venv, max_store_bytes=1)  # far over cap: everything would go oldest-first
    _crash_mid_ship_intent(shipper, 1)            # stale: committed is 4
    out = shipper.r5_segment_pass()
    assert out["plan"]["excluded_seqs"] == [1]
    assert _seqs(venv) == [1]
    assert 1 not in out["result"]["cap_dropped_seqs"]
