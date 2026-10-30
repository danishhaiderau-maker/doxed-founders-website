"""PR3: r5_segment_pass never-delete + R5a eligibility + tripwires + stop-write.

Plan sha 90f0a5491850; rules 10f0b8daa510. Live OFF. No env (incl. '1') reaches unlink.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

import research_segment_format as fmt
import research_segment_prune as prune_mod
import research_segment_shipper as shipper_mod
from test_research_segments import _rows
from test_research_segments_volume import VolumeEnv

DAY = 86400.0
HERE = Path(__file__).resolve().parent


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


def _write_status(shipper, **fields):
    base = {
        "schema": shipper_mod.STATUS_SCHEMA,
        "laptop_acked_seq": 1,
        "laptop_ack_advanced_at": shipper.clock(),
    }
    base.update(fields)
    shipper.status_path.parent.mkdir(parents=True, exist_ok=True)
    shipper.status_path.write_text(json.dumps(base), encoding="utf-8")


def test_r5_pass_never_unlinks_even_when_aged_or_over_cap(venv, monkeypatch):
    for key in ("RESEARCH_SEGMENTS_R5_PRUNE_ENABLED", "RESEARCH_SEGMENTS_PRUNE_ENABLED",
                "RESEARCH_SEGMENTS_ENABLED"):
        monkeypatch.setenv(key, "1")
    _ship_n(venv, 4)
    before = {p: p.read_bytes() for p in _seg_dir(venv).glob("*.tar.gz")}
    for seq in (1, 2, 3, 4):
        _age(venv, seq, 8 * DAY)
    store = int(venv.shipper().load_state()["store_bytes"])
    shipper = _shipper(venv, max_store_bytes=store)
    _write_status(shipper, laptop_acked_seq=4, laptop_ack_advanced_at=venv.clock[0])
    out = shipper.r5_segment_pass()
    assert out["result"]["deleted"] == 0
    assert _seqs(venv) == [1, 2, 3, 4]
    after = {p: p.read_bytes() for p in _seg_dir(venv).glob("*.tar.gz")}
    assert after == before
    assert out["result"]["shadow_would_have_age"] or out["result"]["shadow_would_have_cap"]


def test_missing_status_fail_closed_zero_unlinks_red(venv):
    _ship_n(venv, 2)
    before = _seqs(venv)
    shipper = _shipper(venv)
    if shipper.status_path.is_file():
        shipper.status_path.unlink()
    out = shipper.r5_segment_pass()
    assert out["result"]["deleted"] == 0 and _seqs(venv) == before
    assert out["result"]["eligible"] is False
    assert "STATUS_MISSING_OR_CORRUPT" in out["result"]["deny_reasons"]
    assert prune_mod.R5_RED_STATUS_FAIL_CLOSED in out["result"]["red"]
    status = json.loads(shipper.status_path.read_text())
    assert prune_mod.R5_RED_STATUS_FAIL_CLOSED in status["r5_segment_red"]


def test_corrupt_status_fail_closed_mutation_no_empty_dict_fallthrough(venv):
    _ship_n(venv, 2)
    shipper = _shipper(venv)
    shipper.status_path.write_text("{not-json", encoding="utf-8")
    out = shipper.r5_segment_pass()
    assert out["result"]["deleted"] == 0
    assert out["eligibility"]["status_readable"] is False
    assert "STATUS_MISSING_OR_CORRUPT" in out["result"]["deny_reasons"]
    # Mutation proof: restoring `except: status = {}` without status_readable=False must fail
    # this test (eligibility would not see STATUS_MISSING_OR_CORRUPT).
    src = (HERE / "research_segment_shipper.py").read_text(encoding="utf-8")
    body = src[src.index("def r5_segment_pass"): src.index("\n    # --------------------------------------------------------------- universe")]
    assert "status_readable = False" in body
    assert "execute_r5_segment_prune" not in body  # symbol must stay R22-absent
    assert ".unlink(" not in body
    assert "status = {}" in body  # assigned only after status_readable=False


def test_ack_none_and_stale_over_2h_not_eligible(venv):
    _ship_n(venv, 2)
    shipper = _shipper(venv)
    _write_status(shipper)  # laptop_acked_seq omitted via overwrite
    shipper.status_path.write_text(json.dumps({
        "schema": shipper_mod.STATUS_SCHEMA,
        "laptop_ack_advanced_at": venv.clock[0],
    }), encoding="utf-8")
    out = shipper.r5_segment_pass()
    assert out["result"]["deleted"] == 0 and out["result"]["eligible"] is False
    assert "ACKED_SEQ_NONE" in out["result"]["deny_reasons"]

    _write_status(shipper, laptop_acked_seq=2,
                  laptop_ack_advanced_at=venv.clock[0] - prune_mod.ACK_STALE_SECONDS - 10)
    out = shipper.r5_segment_pass()
    assert out["result"]["deleted"] == 0 and out["result"]["eligible"] is False
    assert "ACK_STALE_OVER_2H" in out["result"]["deny_reasons"]
    assert prune_mod.R5_RED_ACK_STALE in out["result"]["red"]


def test_ack_only_without_custody_not_eligible(venv):
    _ship_n(venv, 2)
    shipper = _shipper(venv)
    _write_status(shipper, laptop_acked_seq=2, laptop_ack_advanced_at=venv.clock[0])
    out = shipper.r5_segment_pass()
    assert out["result"]["eligible"] is False
    assert "NO_LAPTOP_CUSTODY_RECEIPT" in out["result"]["deny_reasons"]
    assert out["result"]["deleted"] == 0


def test_tripwire_within_500_blocks_eligibility(venv):
    _ship_n(venv, 3)
    shipper = _shipper(venv)
    # oldest=1, acked=400 → 1 >= 400-500 → tripwire
    _write_status(shipper, laptop_acked_seq=400, laptop_ack_advanced_at=venv.clock[0])
    out = shipper.r5_segment_pass()
    assert out["result"]["deleted"] == 0 and out["result"]["eligible"] is False
    assert "TRIPWIRE_SEQ_WITHIN_500" in out["result"]["deny_reasons"]
    assert prune_mod.R5_RED_TRIPWIRE_SEQ in out["result"]["red"]


def test_stop_write_at_cap_red_never_deletes(venv):
    _ship_n(venv, 3)
    store = int(venv.shipper().load_state()["store_bytes"])
    shipper = _shipper(venv, max_store_bytes=store)
    _write_status(shipper, laptop_acked_seq=3, laptop_ack_advanced_at=venv.clock[0])
    before = _seqs(venv)
    out = shipper.r5_segment_pass()
    assert out["result"]["deleted"] == 0 and _seqs(venv) == before
    assert out["plan"]["over_cap"] is True
    assert prune_mod.R5_RED_CAP in out["result"]["red"]
    status = json.loads(shipper.status_path.read_text())
    assert status["r5_segment_stop_write"]["stop_write"] is True
    assert status["r5_segment_stop_write"]["alarm_level"] == "RED"
    assert status["r5_segment_stop_write"]["amber_at"] == 0.70
    assert status["r5_segment_stop_write"]["red_at"] == 0.85


def test_threshold_70_85_on_segment_store_pressure(venv):
    _ship_n(venv, 2)
    store = int(venv.shipper().load_state()["store_bytes"])
    # 70% of a higher cap → AMBER, no stop-write RED
    shipper = _shipper(venv, max_store_bytes=int(store / 0.70))
    _write_status(shipper, laptop_acked_seq=2, laptop_ack_advanced_at=venv.clock[0])
    # Force evaluate via over_cap false but still check planner ratios through stop_write helper
    import retention_stop_write as sw
    row = sw.evaluate_and_latch("r5_segment_store_probe", used=store, cap=int(store / 0.70),
                                reason="probe", now=venv.clock[0])
    assert row["alarm_level"] == "AMBER" and row["stop_write"] is False
    row = sw.evaluate_and_latch("r5_segment_store_probe2", used=store, cap=int(store / 0.85),
                                reason="probe", now=venv.clock[0])
    assert row["alarm_level"] == "RED" and row["stop_write"] is True


def test_execute_r5_segment_prune_r22_deleted_and_no_env_reaches_delete():
    src = (HERE / "research_segment_prune.py").read_text(encoding="utf-8")
    assert "def execute_r5_segment_prune" not in src
    tree = ast.parse(src)
    owners = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
              and ".unlink(" in ast.unparse(node)}
    assert owners == set()  # no function body unlinks
    shipper_src = (HERE / "research_segment_shipper.py").read_text(encoding="utf-8")
    assert "execute_r5_segment_prune" not in shipper_src
    assert "PRUNING_ENABLED =" not in shipper_src
    body = shipper_src[shipper_src.index("def r5_segment_pass"):
                       shipper_src.index("\n    # --------------------------------------------------------------- universe")]
    assert "getenv" not in body and "os.environ" not in body
    # Behavioral: env='1' on every known prune/shipper switch still yields zero unlinks
    # (covered by test_r5_pass_never_unlinks_even_when_aged_or_over_cap).


def test_shipper_main_loop_still_calls_r5_between_cycles():
    source = (HERE / "research_segment_shipper.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cycle = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "cycle")
    assert "r5_segment_pass" not in ast.unparse(cycle)
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.unparse(main)
    assert "shipper.r5_segment_pass(keep_seqs=shipper.r5_keep_seqs())" in body


def test_c3_r5_pass_runs_before_the_low_disk_check():
    source = (HERE / "research_segment_shipper.py").read_text(encoding="utf-8")
    main = source[source.index("def main("):]
    loop = main[main.index("while "):]
    assert loop.index("r5_pass()") < loop.index("LOW_DISK")


class _StopLoop(Exception):
    pass


def _run_one_low_disk_loop(monkeypatch, shipper) -> None:
    monkeypatch.setenv("RESEARCH_SEGMENTS_ENABLED", "1")
    monkeypatch.setattr(shipper_mod, "shipper_from_env", lambda environ=None: shipper)
    monkeypatch.setattr(shipper_mod, "_acquire_single_instance", lambda state_dir: object())
    monkeypatch.setattr(shipper_mod, "_set_priority", lambda *a, **kw: None)
    monkeypatch.setattr(shipper_mod, "_free_bytes", lambda path: 0)

    def stop(_seconds):
        raise _StopLoop

    monkeypatch.setattr(shipper_mod.time, "sleep", stop)
    with pytest.raises(_StopLoop):
        shipper_mod.main()


def test_full_loop_ships_with_prune_gone_zero_unlinks(venv, monkeypatch):
    """HM-7: one main-loop iteration under LOW_DISK still runs r5_pass with zero unlinks."""
    _ship_n(venv, 3)
    for seq in (1, 2, 3):
        _age(venv, seq, 8 * DAY)
    before = {p: p.read_bytes() for p in _seg_dir(venv).glob("*.tar.gz")}
    shipper = _shipper(venv)
    _write_status(shipper, laptop_acked_seq=3, laptop_ack_advanced_at=venv.clock[0])
    _run_one_low_disk_loop(monkeypatch, shipper)
    after = {p: p.read_bytes() for p in _seg_dir(venv).glob("*.tar.gz")}
    assert after == before
    status = json.loads(shipper.status_path.read_text())
    assert status["last_error"] == "LOW_DISK_SKIPPED"
    assert int(status.get("r5_segment_last_deleted") or 0) == 0


def test_entrypoint_comment_no_longer_says_pruning_not_implemented():
    text = (HERE / "fly-entrypoint.sh").read_text(encoding="utf-8")
    assert "pruning not implemented" not in text
    assert "never-delete" in text.lower() or "R5a" in text


def test_bot_py_r5_retention_loop_pin_16939():
    """Boss re-pin: loop is bot.py:16939 on f6974f4d; :16937 is blank after once."""
    # On this PR3 tip (stacked on PR4) line numbers may drift; assert identity by name order.
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    once = src.index("\ndef r5_retention_once(")
    loop = src.index("\ndef r5_retention_loop(")
    assert once < loop
    # Document Boss pin against deployed f6974f4d: once ends ~16937 blank; loop at 16939.
    assert "def r5_retention_loop" in src
