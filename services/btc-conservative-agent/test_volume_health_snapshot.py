import ast
import json
import os
from collections import deque, namedtuple
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
_NAMES = {
    "VOLUME_HEALTH_CACHE_SEC", "VOLUME_GROWTH_SAMPLE_SEC", "VOLUME_GROWTH_WINDOW_SEC",
    "VOLUME_GROWTH_MIN_SPAN_SEC", "_SEGMENT_STATUS_MAX_BYTES", "_SEGMENT_STATUS_FIELDS",
    "_volume_growth_samples", "_volume_health_cached",
}
Usage = namedtuple("Usage", "total used free")


def _load(volume_root, usage_fn):
    tree = ast.parse(BOT)
    body = [
        node for node in tree.body
        if (isinstance(node, ast.Assign) and any(getattr(t, "id", None) in _NAMES for t in node.targets))
        or (isinstance(node, ast.FunctionDef)
            and node.name in {"_volume_transfer_snapshot", "_volume_health_snapshot"})
    ]
    fake_shutil = type("S", (), {"disk_usage": staticmethod(usage_fn)})
    namespace = {
        "deque": deque, "json": json, "os": os, "Path": Path, "shutil": fake_shutil,
        "_data_sync_volume_root": lambda: volume_root,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def test_health_route_embeds_volume_block_without_locks():
    start = BOT.index("def health():")
    body = BOT[start:BOT.index("\n@app.route", start)]
    assert '"volume": _volume_health_snapshot(now)' in body
    helper = BOT[BOT.index("def _volume_health_snapshot("):start]
    assert "state_lock" not in helper and "trade_lock" not in helper


def test_disk_fields_percent_and_cache(tmp_path):
    calls = []

    def usage(path):
        calls.append(path)
        return Usage(20 * 2**30, 3 * 2**30, 17 * 2**30)

    ns = _load(tmp_path, usage)
    snap = ns["_volume_health_snapshot"](1000.0)
    assert (snap["total_bytes"], snap["used_bytes"], snap["free_bytes"]) == (20 * 2**30, 3 * 2**30, 17 * 2**30)
    assert snap["used_pct"] == 15.0
    assert snap["growth_bytes_per_hour"] is None and snap["hours_to_full"] is None
    ns["_volume_health_snapshot"](1010.0)
    assert len(calls) == 1
    ns["_volume_health_snapshot"](1000.0 + ns["VOLUME_HEALTH_CACHE_SEC"])
    assert len(calls) == 2


def test_growth_rate_and_hours_to_full_after_min_span(tmp_path):
    used = {"v": 3 * 2**30}

    def usage(_path):
        return Usage(20 * 2**30, used["v"], 20 * 2**30 - used["v"])

    ns = _load(tmp_path, usage)
    ns["_volume_health_snapshot"](0.0)
    used["v"] += 215 * 2**20
    snap = ns["_volume_health_snapshot"](3600.0)
    assert snap["growth_bytes_per_hour"] == 215 * 2**20
    assert snap["growth_window_sec"] == 3600
    assert snap["hours_to_full"] == round((20 * 2**30 - used["v"]) / (215 * 2**20), 1)


def test_disk_error_is_reported_not_raised(tmp_path):
    def usage(_path):
        raise OSError("gone")

    snap = _load(tmp_path, usage)["_volume_health_snapshot"](0.0)
    assert snap["error"] == "DISK_USAGE_UNAVAILABLE" and snap["used_pct"] is None


def test_transfer_reads_segment_status_and_legacy_ack(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_SEGMENTS_ENABLED", "1")
    monkeypatch.delenv("RESEARCH_SEGMENTS_STATE_DIR", raising=False)
    (tmp_path / "segment-shipper").mkdir()
    (tmp_path / "segment-shipper" / "status.json").write_text(json.dumps({
        "shipped_seq": 12, "laptop_acked_seq": 9, "unshipped_bytes": 0,
        "updated_at": 900.0, "last_error": "x" * 500, "secret_ish": "never-copied",
    }))
    (tmp_path / "sync_ack.json").write_text("{}")
    os.utime(tmp_path / "sync_ack.json", (400.0, 400.0))
    ns = _load(tmp_path, lambda _p: Usage(1, 0, 1))
    transfer = ns["_volume_transfer_snapshot"](tmp_path, 1000.0)
    assert transfer["segments_enabled"] is True and transfer["segment_status_present"] is True
    assert (transfer["shipped_seq"], transfer["laptop_acked_seq"]) == (12, 9)
    assert transfer["segment_status_age_sec"] == 100.0
    assert transfer["legacy_ack_age_sec"] == 600.0
    assert len(transfer["last_error"]) == 200
    assert "secret_ish" not in transfer


def test_transfer_without_files_is_absent_not_error(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCH_SEGMENTS_ENABLED", raising=False)
    transfer = _load(tmp_path, lambda _p: Usage(1, 0, 1))["_volume_transfer_snapshot"](tmp_path, 0.0)
    assert transfer == {"segments_enabled": False, "segment_status_present": False, "legacy_ack_age_sec": None}
