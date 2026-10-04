"""PR-B data hygiene: pre-epoch archive, handoff compaction, orphan tmp sweep, precise bp, funnel hooks."""
from __future__ import annotations

import ast
import json
import os
import threading
import time
from pathlib import Path

import pytest

import runtime_hygiene as rh

BOT = Path(__file__).with_name("bot.py")
EPOCH_START = 1_800_000_000.0


def _manifest(epoch="ce-test-epoch"):
    return {"epoch_id": epoch, "started_at_ts": EPOCH_START}


def _write(path: Path, text: str, mtime: float) -> Path:
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


# ------------------------------------------------------------------ pre-epoch archive

def test_pre_epoch_ledgers_move_and_mixed_or_protected_files_stay(tmp_path):
    _write(tmp_path / "trades_3factor.csv", "a,b\n1,2\n", EPOCH_START - 3600)
    _write(tmp_path / "trades_3factor.csv.validation.json", "{}", EPOCH_START - 3600)
    _write(tmp_path / "post_exit_replay.jsonl", '{"ts": 1}\n', EPOCH_START + 60)  # written after start
    _write(tmp_path / "paper_lifecycle_v1.json", "{}", EPOCH_START - 3600)
    (tmp_path / "fill_markouts.jsonl").write_bytes(b"")
    doc = rh.archive_pre_epoch_ledgers(tmp_path, _manifest(),
                                       names=rh.PRE_EPOCH_LEDGERS + ("paper_lifecycle_v1.json",))
    assert doc["archived"] == ["trades_3factor.csv"]
    assert doc["deletion_invoked"] is False
    assert not (tmp_path / "trades_3factor.csv").exists()
    archived = tmp_path / doc["files"]["trades_3factor.csv"]["archived_to"]
    assert archived.read_text() == "a,b\n1,2\n"
    assert doc["files"]["trades_3factor.csv"]["sidecars"]
    assert not (tmp_path / "trades_3factor.csv.validation.json").exists()
    assert doc["mixed_left_in_place"] == ["post_exit_replay.jsonl"]
    assert (tmp_path / "post_exit_replay.jsonl").exists()
    assert doc["files"]["paper_lifecycle_v1.json"]["status"] == "REFUSED_PROTECTED"
    assert (tmp_path / "paper_lifecycle_v1.json").exists()
    assert doc["files"]["fill_markouts.jsonl"]["status"] == "EMPTY"
    # archive dir is excluded from segment shipping
    assert rh.ARCHIVE_DIR in __import__("research_segment_selection").EXCLUDED_DIR_NAMES


def test_pre_epoch_archive_is_idempotent_per_epoch(tmp_path):
    _write(tmp_path / "decisions_3factor.csv", "x\n", EPOCH_START - 10)
    first = rh.archive_pre_epoch_ledgers(tmp_path, _manifest())
    _write(tmp_path / "decisions_3factor.csv", "new\n", EPOCH_START - 5)  # receipt already decided
    second = rh.archive_pre_epoch_ledgers(tmp_path, _manifest())
    assert second == first
    assert (tmp_path / "decisions_3factor.csv").read_text() == "new\n"
    assert rh.archive_pre_epoch_ledgers(tmp_path, None) is None


def test_never_move_covers_restart_recovery_state():
    for name in ("paper_lifecycle_v1.json", "research_events_v22.jsonl", "research_events_v22.provisional.json",
                 "cancellation_evidence_handoffs.jsonl", "fill_evidence_handoffs.jsonl", "lane_pnl_ledger.json"):
        assert name in rh.NEVER_MOVE
        assert name not in rh.PRE_EPOCH_LEDGERS


# ------------------------------------------------------------------ handoff compaction

def _handoff_rows():
    return [
        {"schema": "fill_evidence_handoff_pending_v1", "receipt_id": "a" * 64, "collector_epoch_id": "e"},
        {"schema": "fill_evidence_handoff_result_v1", "receipt_id": "a" * 64, "status": "APPLIED"},
        {"schema": "fill_evidence_handoff_pending_v1", "receipt_id": "b" * 64, "collector_epoch_id": "e"},
        {"schema": "fill_evidence_handoff_result_v1", "receipt_id": "b" * 64, "status": "RETRY"},
        {"schema": "fill_evidence_handoff_pending_v1", "receipt_id": "c" * 64, "collector_epoch_id": "e"},
        {"schema": "fill_evidence_handoff_result_v1", "receipt_id": "c" * 64, "status": "EPOCH_MISMATCH_PRESERVED"},
    ]


def _compact(path, root, **kw):
    return rh.compact_handoff_journal(
        path, lock=threading.Lock(), pending_schema="fill_evidence_handoff_pending_v1",
        result_schema="fill_evidence_handoff_result_v1",
        terminal_statuses=("APPLIED", "EPOCH_MISMATCH_PRESERVED"), runtime_root=root, **kw)


def test_handoff_compaction_keeps_only_unresolved_pending_and_archives_everything(tmp_path):
    journal = tmp_path / "fill_evidence_handoffs.jsonl"
    raw = "".join(json.dumps(r) + "\n" for r in _handoff_rows()) + '{"torn'
    journal.write_text(raw, encoding="utf-8")
    out = _compact(journal, tmp_path, force=True)
    assert out["status"] == "COMPACTED" and out["deletion_invoked"] is False
    kept = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [r["receipt_id"] for r in kept] == ["b" * 64]
    assert kept[0]["schema"] == "fill_evidence_handoff_pending_v1"
    assert (tmp_path / out["archived_to"]).read_text() == raw
    assert oct(journal.stat().st_mode & 0o777) == oct(0o600)


def test_handoff_compaction_not_due_below_threshold(tmp_path):
    journal = tmp_path / "fill_evidence_handoffs.jsonl"
    journal.write_text(json.dumps(_handoff_rows()[0]) + "\n", encoding="utf-8")
    assert _compact(journal, tmp_path)["status"] == "NOT_DUE"
    assert _compact(tmp_path / "missing.jsonl", tmp_path)["status"] == "ABSENT"
    assert _compact(journal, tmp_path, min_bytes=1)["status"] == "COMPACTED"


# ------------------------------------------------------------------ orphan tmp sweep

def test_orphan_tmp_sweep_moves_dead_old_and_skips_live_young_or_locked(tmp_path):
    old = time.time() - 7200
    dead = _write(tmp_path / ".research_events_v22.provisional.json.999991.tmp", "x", old)
    live = _write(tmp_path / "paper_lifecycle_v1.json.4242.7.tmp", "y", old)
    young = _write(tmp_path / "signal_snapshot.jsonl.999992.3.tmp", "z", time.time())
    unrelated = _write(tmp_path / "other.tmp", "k", old)
    out = rh.sweep_orphan_tmp(tmp_path, pid_alive=lambda pid: pid == 4242, current_pid=1)
    assert [m["name"] for m in out["moved"]] == [dead.name]
    assert out["moved_count"] == 1 and out["deletion_invoked"] is False
    assert not dead.exists() and live.exists() and young.exists() and unrelated.exists()
    assert list((tmp_path / rh.ARCHIVE_DIR / rh.ORPHAN_TMP_SUBDIR).rglob(dead.name))
    lock = threading.Lock()
    holder = threading.Thread(target=lock.acquire)
    holder.start(); holder.join()
    assert rh.sweep_orphan_tmp(tmp_path, writer_lock=lock)["status"] == "WRITER_LOCK_HELD"


# ------------------------------------------------------------------ precise per-trade bp

def test_lane_stats_mean_bp_uses_precise_margin_pct_not_cent_rounded_usd():
    import monitor_api
    # True net +0.004 USD on $25 notional = +1.6 bp; booked net_pnl_usd rounds to 0.00.
    rows = [{"close_ts": 1, "net_pnl_usd": 0.0, "notional_usd": 25.0, "pnl_margin_pct": 1.6, "leverage": 100}]
    out = monitor_api.lane_stats(rows)
    assert out["mean_bp"] == 1.6 and out["mean_bp_basis"] == "pnl_margin_pct"
    legacy = monitor_api.lane_stats([{"close_ts": 1, "net_pnl_usd": -0.25, "notional_usd": 25.0}])
    assert legacy["mean_bp"] == -100.0 and legacy["mean_bp_basis"] == "net_pnl_usd"
    assert monitor_api.trade_bp({"pnl_margin_pct": "2.5", "leverage": "50"})[0] == pytest.approx(5.0)


def test_bot_monitor_rows_carry_precise_pnl():
    src = BOT.read_text(encoding="utf-8")
    assert '"pnl_margin_pct": pnl_margin_pct, "leverage": leverage' in src


# ------------------------------------------------------------------ funnel hooks

def test_missing_funnel_hooks_now_exist_and_record_stages(tmp_path, monkeypatch):
    import execution_funnel as ef
    monkeypatch.chdir(tmp_path)
    ef.funnel_on_limit_chase({"trade_id": "T1", "research_lane": "L"}, 100.0, 100.5, 2.0, 0.1, 1)
    ef.funnel_on_signal_expire({"trade_id": "T2", "created_ts": time.time() - 30}, "SIGNAL_EXPIRED")
    rows = [json.loads(line) for line in (tmp_path / ef.FUNNEL_FILE).read_text().splitlines()]
    assert [(r["trade_id"], r["stage"]) for r in rows] == [("T1", "LIMIT_CHASED"), ("T2", "SIGNAL_EXPIRED")]
    summary = ef.build_funnel_summary(str(tmp_path))
    assert summary["limit_chase_count"] == 1 and summary["signal_expired_count"] == 1


# ------------------------------------------------------------------ bot wiring + canonical epoch

def _bot_functions(names, ns):
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT), "exec"), ns)
    return ns


def test_bot_wiring_order():
    src = BOT.read_text(encoding="utf-8")
    assert ("    _open_data_epoch()\n    _wipe_research_on_startup_if_needed()\n"
            "    _sweep_orphan_runtime_tmp()\n    _validate_research_ledgers_on_startup()") in src
    assert '("_archive_pre_epoch_ledgers_on_open", "_bind_epoch_aliases")' in src
    assert src.count('globals().get("_compact_evidence_handoff_journals")') == 2
    assert '"canonical_epoch": _canonical_epoch_public(),' in src
    assert '"runtime_hygiene": _runtime_hygiene_public(),' in src


def test_canonical_epoch_prefers_data_epoch_and_binds_aliases(tmp_path):
    import data_epoch
    ns = _bot_functions(("_epoch_alias_ids", "_canonical_epoch_public", "_bind_epoch_aliases"), {
        "_DATA_EPOCH_MANIFEST": {"epoch_id": "ce-x", "started_at_utc": "2026-10-04T00:00:00Z"},
        "_bound_collection_epoch_id": lambda: "epoch-abc",
        "_fresh_epoch_identity_from_session": lambda: ("epoch-def", None, None),
        "_data_sync_runtime_root": lambda: tmp_path, "_data_epoch": data_epoch,
        "Path": Path, "json": json, "time": time, "BOT_INSTANCE_ID": "boot-1",
        "logger": type("L", (), {"warning": print})(),
    })
    pub = ns["_canonical_epoch_public"]()
    assert pub["canonical_epoch_id"] == "ce-x" and pub["source"] == "DATA_EPOCH_ID"
    assert pub["aliases"] == {"collector_v22_epoch_id": "epoch-abc", "fresh_epoch_id": "epoch-def"}
    doc = ns["_bind_epoch_aliases"](ns["_DATA_EPOCH_MANIFEST"])
    assert doc["bindings"][-1]["aliases"]["collector_v22_epoch_id"] == "epoch-abc"
    ns["_bind_epoch_aliases"](ns["_DATA_EPOCH_MANIFEST"])
    stored = json.loads((tmp_path / "data_epoch_boundary" / "ce-x.epoch_aliases.json").read_text())
    assert len(stored["bindings"]) == 1  # unchanged aliases are not re-appended
    ns["_DATA_EPOCH_MANIFEST"] = None
    assert ns["_canonical_epoch_public"]()["canonical_epoch_id"] == "epoch-abc"
