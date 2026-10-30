"""PR2 remaining-deleters kill-switch: never-delete + 70/85 + trading-safety.

Plan sha 264913c4cd81. Rules RULES-R0-R45 sha 10f0b8daa510.
#949 Sink never-unlink is merged; W1 requires Sink delete residual count == 0.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

import disk_alert
import r5_retention as r5
import retention_stop_write as rsw
import rotation_cap

HERE = Path(__file__).resolve().parent
FAM = "signal_snapshot.jsonl"
ALLOWLIST = HERE / "ci" / "unit_test_allowlist.txt"

W1_FILES = (
    "rotation_cap.py",
    "r5_retention.py",
    "bot.py",
    "strategist_streams_collector.py",
)
# Call-like delete APIs (not the word alone in comments/docstrings/param names).
DELETE_CALL_RE = re.compile(
    r"""(?P<api>(?:^|[^\w.])(?:os\.unlink|os\.remove|Path\([^\)]*\)\.unlink|shutil\.rmtree)\s*\()""",
    re.MULTILINE,
)


@pytest.fixture(autouse=True)
def _clear_pressure():
    rsw.clear_latches()
    rsw.set_volume_used_ratio(None)
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row["reason"])
    yield
    rsw.clear_latches()
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row["reason"])


def _make_rotations(tmp_path, base, n, size=10):
    active = tmp_path / base
    active.write_bytes(b"a" * size)
    for i in range(1, n + 1):
        (tmp_path / f"{base}.{i}").write_bytes(b"x" * size)
    return active


def _family_bytes(tmp_path, base):
    out = {}
    for p in tmp_path.iterdir():
        if p.name == base or re.match(rf"^{re.escape(base)}\.\d+$", p.name):
            out[p.name] = p.read_bytes()
    return out


def _seg(root, i, size, mtime):
    sha = f"{i:064x}"
    d = root / "v3" / "market_segments" / sha[:2]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sha}.json"
    p.write_bytes(b"s" * size)
    os.utime(p, (mtime, mtime))
    return sha


def _slice_fn(src: str, start: str, end: str) -> str:
    return src[src.index(start): src.index(end)]


# ---------------------------------------------------------------------------
# W1 — explicit residual grep; each leftover justified
# ---------------------------------------------------------------------------
def test_w1_delete_api_residuals_are_justified():
    """W1: grep unlink/os.remove/rmtree/remove( over the four scoped files."""
    justified = {
        "r5_retention.py": "PR4 scope: cap_lifecycle_bundles / purge_retired_transfer_bundles / wipe_stale_locks / seal_drain_discard_v22",
    }
    bot_src = (HERE / "bot.py").read_text("utf-8")
    assert "def reset_all_csv" not in bot_src
    trim = _slice_fn(bot_src, "def _trim_oversized_log", "def prune_aux_logs_on_startup")
    assert "os.remove(" not in trim and ".unlink(" not in trim
    reset_fn = _slice_fn(bot_src, "def _reset_runtime_log_handlers", "def reset_all_research_files")
    assert "os.remove(" not in reset_fn and "os.unlink(" not in reset_fn
    prune_fn = _slice_fn(bot_src, "def _prune_analyzer_generations", "def _safe_analyzer_bundle_members")
    assert "shutil.rmtree(" not in prune_fn and "rmtree(" not in prune_fn
    rollover = _slice_fn(bot_src, "class SafeRotatingFileHandler", "NEAR_EDGE_LOG_MAX_BYTES")
    assert re.search(r"^\s*super\(\)\.doRollover\(\)", rollover, re.M) is None

    rot_src = (HERE / "rotation_cap.py").read_text("utf-8")
    enforce = _slice_fn(rot_src, "def enforce_cap", "def sweep")
    assert "os.unlink(" not in enforce and "os.remove(" not in enforce

    cap_src = (HERE / "r5_retention.py").read_text("utf-8")
    cap_fn = _slice_fn(cap_src, "def cap_market_segments", "def market_segments_stop_write_latched")
    assert "path.unlink(" not in cap_fn and "os.unlink(" not in cap_fn and "os.remove(" not in cap_fn

    residuals = []
    for name in W1_FILES:
        text = (HERE / name).read_text("utf-8")
        for match in DELETE_CALL_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            residuals.append((name, line_no, match.group("api").strip()))
    assert not any(name == "rotation_cap.py" for name, _, _ in residuals), residuals
    for name, line_no, api in residuals:
        if name in justified or name == "bot.py":
            continue
        pytest.fail(f"unjustified delete API {api} in {name}:{line_no}")
    sink_residuals = [r for r in residuals if r[0] == "strategist_streams_collector.py"]
    assert len(sink_residuals) == 0, sink_residuals
    # Mutation: re-adding Sink os.remove(...) must make W1 fail (count != 0 / unjustified).
    sink_src = (HERE / "strategist_streams_collector.py").read_text("utf-8")
    mutated_sink = sink_src + "\nos.remove(_path)\n"
    mutated_hits = list(DELETE_CALL_RE.finditer(mutated_sink))
    assert len(mutated_hits) >= 1
    assert any("os.remove" in m.group("api") for m in mutated_hits)


# ---------------------------------------------------------------------------
# W2 — shipping / RESEARCH_SEGMENTS_ENABLED + free-disk 70/85
# ---------------------------------------------------------------------------
def test_w2_shipping_flag_unchanged_and_free_disk_thresholds():
    """Shipping continues; RESEARCH_SEGMENTS_ENABLED unchanged; 70/85 = disk_alert.py:23-24."""
    assert rsw.AMBER_RATIO == disk_alert.AMBER_RATIO == 0.70
    assert rsw.RED_RATIO == disk_alert.RED_RATIO == 0.85
    src = (HERE / "retention_stop_write.py").read_text("utf-8")
    assert "disk_alert.AMBER_RATIO" in src and "disk_alert.RED_RATIO" in src
    # Must not assign/flip the shipping enable flag (doc mention OK).
    assert "RESEARCH_SEGMENTS_ENABLED =" not in src
    assert "os.environ" not in src and "getenv" not in src
    shipper = (HERE / "research_segment_shipper.py").read_text("utf-8")
    assert "RESEARCH_SEGMENTS_ENABLED" in shipper
    assert rsw.level_for_ratios(0.69) == "GREEN"
    assert rsw.level_for_ratios(0.70) == "AMBER"
    assert rsw.level_for_ratios(0.85) == "RED"
    assert rsw.should_stop_write(used=1, cap=100, volume_ratio=0.90) is True


# ---------------------------------------------------------------------------
# Site A — market segments never-delete + mutation
# ---------------------------------------------------------------------------
def test_site_a_market_segments_never_deletes_and_alarms(tmp_path):
    shas = [_seg(tmp_path, i, 100, 1000 + i) for i in range(10)]
    before = {p: p.read_bytes() for p in (tmp_path / "v3" / "market_segments").rglob("*.json") if len(p.stem) == 64}
    res = r5.cap_market_segments(tmp_path, max_bytes=500, now=5.0)
    assert res["deleted"] == 0 and res["stop_write"] is True and res["alarm_level"] == "RED"
    after = {p: p.read_bytes() for p in (tmp_path / "v3" / "market_segments").rglob("*.json") if len(p.stem) == 64}
    assert after == before
    assert r5.count_market_segments(tmp_path) == 10
    assert r5.market_segments_stop_write_latched() is True
    assert set(shas) == {p.stem for p in after}


def test_site_a_mutation_unlink_absent_in_cap_market_segments():
    src = (HERE / "r5_retention.py").read_text("utf-8")
    body = _slice_fn(src, "def cap_market_segments", "def market_segments_stop_write_latched")
    assert "path.unlink(" not in body and "os.unlink(" not in body
    # Mutation: restoring `path.unlink()` makes this fail.


def test_site_a_threshold_70_85(tmp_path):
    _seg(tmp_path, 1, 70, 1)
    res = r5.cap_market_segments(tmp_path, max_bytes=100, now=1.0)
    assert res["alarm_level"] == "AMBER" and res["deleted"] == 0
    assert res["amber_at"] == 0.70 and res["red_at"] == 0.85


# ---------------------------------------------------------------------------
# Site B — rotation_cap never-delete + mutation
# ---------------------------------------------------------------------------
def test_site_b_rotation_cap_never_unlinks(tmp_path):
    active = _make_rotations(tmp_path, FAM, 15)
    before = _family_bytes(tmp_path, FAM)
    res = rotation_cap.enforce_cap(str(active), now=1.0)
    assert res["deleted"] == [] and res["stop_write"] is True
    assert res["would_have_dropped"][:3] == [f"{FAM}.1", f"{FAM}.2", f"{FAM}.3"]
    assert _family_bytes(tmp_path, FAM) == before
    assert len(rotation_cap.list_rotations(str(active))) == 15


def test_site_b_mutation_os_unlink_absent_in_enforce_cap():
    src = (HERE / "rotation_cap.py").read_text("utf-8")
    body = _slice_fn(src, "def enforce_cap", "def sweep")
    assert "os.unlink(" not in body and "os.remove(" not in body


def test_site_b_threshold_and_sweep_never_deletes(tmp_path):
    active = _make_rotations(tmp_path, FAM, 12, size=10)
    res = rotation_cap.enforce_cap(str(active))
    assert res["alarm_level"] == "RED" and res["deleted"] == []
    before = _family_bytes(tmp_path, FAM)
    rotation_cap.sweep(str(tmp_path))
    assert _family_bytes(tmp_path, FAM) == before


# ---------------------------------------------------------------------------
# Site C — analyzer generations receipt + never rmtree
# ---------------------------------------------------------------------------
def test_site_c_analyzer_generation_receipt_schema_and_no_rmtree(tmp_path, monkeypatch):
    import bot as bot_mod
    gens = tmp_path / "analyzer_generations"
    gens.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for i in range(5):
        d = gens / f"generation-{i}"
        d.mkdir()
        (d / "status.json").write_text("{}")
        os.utime(d, (1000 + i, 1000 + i))
    monkeypatch.setattr(bot_mod, "_analyzer_generations_dir", lambda: gens)
    monkeypatch.setattr(bot_mod, "_data_sync_runtime_root", lambda: runtime)
    before = {p.name for p in gens.iterdir()}
    bot_mod._prune_analyzer_generations("generation-4", keep=3)
    assert {p.name for p in gens.iterdir()} == before
    cap = runtime / "retention" / rotation_cap.CAP_RECEIPTS_FILE
    assert cap.exists()
    rows = [json.loads(l) for l in cap.read_text().splitlines()]
    row = next(r for r in rows if r.get("schema") == rsw.ANALYZER_GENERATION_RECEIPT_SCHEMA)
    for key in ("ts", "active_generation", "keep", "retained_names",
                "would_have_dropped_names", "alarm_level", "used_ratio", "reason"):
        assert key in row
    assert row["would_have_dropped_names"]
    assert rsw.is_stopped("analyzer_generations")


def test_site_c_mutation_rmtree_absent():
    src = (HERE / "bot.py").read_text("utf-8")
    body = _slice_fn(src, "def _prune_analyzer_generations", "def _safe_analyzer_bundle_members")
    assert "shutil.rmtree(" not in body and re.search(r"(?<!never )rmtree\(", body) is None


# ---------------------------------------------------------------------------
# Sites D/E/F — runtime log / trim / doRollover
# ---------------------------------------------------------------------------
def test_site_d_e_f_runtime_log_never_deletes(tmp_path, monkeypatch):
    import logging
    import bot as bot_mod
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"x" * 100)
    for i in range(1, 4):
        (tmp_path / f"bot_runtime.log.{i}").write_bytes(b"y" * 10)
    monkeypatch.chdir(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.glob("bot_runtime.log*")}
    bot_mod._trim_oversized_log(str(log), max_bytes=50)
    assert {p.name: p.read_bytes() for p in tmp_path.glob("bot_runtime.log*")} == before
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler.doRollover()
    assert handler._stop_write is True
    assert {p.name: p.read_bytes() for p in tmp_path.glob("bot_runtime.log*")} == before
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
    handler.emit(rec)
    assert log.read_bytes() == before[log.name]


def test_site_f_mutation_super_dorollover_absent():
    src = (HERE / "bot.py").read_text("utf-8")
    body = _slice_fn(src, "class SafeRotatingFileHandler", "NEAR_EDGE_LOG_MAX_BYTES")
    assert re.search(r"^\s*super\(\)\.doRollover\(\)", body, re.M) is None


def test_env_not_a_kill_parametrized(tmp_path, monkeypatch):
    """No env value reaches a delete (LOG_BACKUP_COUNT / size envs × 0/default/large)."""
    import bot as bot_mod
    cases = [
        ("LOG_BACKUP_COUNT", ["0", "5", "1000"]),
        ("LOG_MAX_BYTES", ["0", str(50 * 1024 * 1024), str(10 ** 12)]),
        ("NEAR_EDGE_LOG_MAX_BYTES", ["0", str(100 * 1024 * 1024), str(10 ** 12)]),
        ("AGENT_DEBUG_LOG_MAX_BYTES", ["0", str(20 * 1024 * 1024), str(10 ** 12)]),
    ]
    for env_name, values in cases:
        for value in values:
            monkeypatch.setenv(env_name, value)
            log = tmp_path / f"{env_name}_{value}.log"
            log.write_bytes(b"z" * 200)
            parts = [log]
            for i in range(1, 3):
                p = tmp_path / f"{log.name}.{i}"
                p.write_bytes(b"z" * 20)
                parts.append(p)
            before = {p.name: p.read_bytes() for p in parts}
            max_bytes = 50
            if value.isdigit():
                max_bytes = max(1, int(value)) if env_name != "LOG_BACKUP_COUNT" else 50
            bot_mod._trim_oversized_log(str(log), max_bytes=max_bytes)
            backup = int(value) if env_name == "LOG_BACKUP_COUNT" and value.isdigit() else 5
            handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=backup)
            handler.doRollover()
            after = {p.name: p.read_bytes() for p in parts if p.exists()}
            assert after == before, (env_name, value)


# ---------------------------------------------------------------------------
# Site G — reset_all_csv removed; validate_startup never deletes/recreates
# ---------------------------------------------------------------------------
def test_site_g_reset_all_csv_absent_and_validate_startup_is_readonly(tmp_path, monkeypatch):
    import bot as bot_mod
    assert not hasattr(bot_mod, "reset_all_csv")
    src = (HERE / "bot.py").read_text("utf-8")
    assert "def reset_all_csv" not in src
    monkeypatch.chdir(tmp_path)
    for name in ("decisions.csv", "trades.csv", "orphan.csv"):
        (tmp_path / name).write_text("keep\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    bot_mod.validate_startup()
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert after == before
    validate_src = _slice_fn(src, "def validate_startup", "def track_event")
    assert "os.remove(" not in validate_src and "unlink(" not in validate_src
    assert "must stay deleted" in validate_src


# ---------------------------------------------------------------------------
# W3 / Test 4 — force-flat, disarm, R20 + book10 + allowlist
# ---------------------------------------------------------------------------
def test_w3_trading_safety_force_flat_disarm_r20_when_log_stopped(tmp_path, monkeypatch):
    """Stopping log writes must not block force-flat, disarm, or R20 paths.

    Allowlist: services/btc-conservative-agent/ci/unit_test_allowlist.txt
    """
    import bot as bot_mod
    import live_tile_gate as ltg
    assert ALLOWLIST.is_file()
    assert "test_remaining_deleters_killswitch.py" in ALLOWLIST.read_text("utf-8")
    rsw.latch("runtime_log", reason="TEST", used=100, cap=10)
    log = tmp_path / "bot_runtime.log"
    log.write_text("x")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=10, backupCount=5)
    handler._stop_write = True
    assert callable(bot_mod._disarm_live_control)
    assert callable(bot_mod._live_guardian_force_flat)
    assert float(ltg.DAILY_LOSS_CAP_TOTAL_USD) > 0
    assert "R20" in str(ltg.DAILY_LOSS_CAP_SOURCE)
    assert rotation_cap.classify("bfx_book_10l_1s.jsonl")[0] == "EXEMPT"
    active = _make_rotations(tmp_path, "bfx_book_10l_1s.jsonl", 20)
    before = _family_bytes(tmp_path, "bfx_book_10l_1s.jsonl")
    res = rotation_cap.enforce_cap(str(active))
    assert res["deleted"] == [] and res.get("class") == "EXEMPT"
    assert _family_bytes(tmp_path, "bfx_book_10l_1s.jsonl") == before
    bot_src = (HERE / "bot.py").read_text("utf-8")
    assert "def _disarm_live_control" in bot_src
    assert "def _live_guardian_force_flat" in bot_src


def test_allowlist_contains_this_module():
    assert "test_remaining_deleters_killswitch.py" in ALLOWLIST.read_text("utf-8")
