"""R5 retention: PR2 market-segments never-delete + PR4 lifecycle/transfer/v22 never-delete."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import disk_alert
import r5_retention as r5
import retention_stop_write
import rotation_cap


@pytest.fixture(autouse=True)
def _clear_red_and_latches():
    retention_stop_write.clear_latches()
    retention_stop_write.set_volume_used_ratio(None)
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row["reason"])
    yield
    retention_stop_write.clear_latches()
    retention_stop_write.set_volume_used_ratio(None)
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row["reason"])


def _seg(root, i, size, mtime):
    sha = f"{i:064x}"
    d = root / "v3" / "market_segments" / sha[:2]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sha}.json"
    p.write_bytes(b"s" * size)
    os.utime(p, (mtime, mtime))
    return sha


def _bundle(root, kind, prefix, name, size, mtime):
    d = root / "v3" / kind / "aa" / f"{prefix}{name}"
    d.mkdir(parents=True)
    (d / "payload.bin").write_bytes(b"b" * size)
    os.utime(d, (mtime, mtime))
    return d


def _event(event_id):
    from collector_v22_schema import OBS_DATA_ERROR
    return {"event_id": event_id, "trade_id": event_id, "collector_version": "collector_v2.2",
            "observation_status": OBS_DATA_ERROR, "primary_outcome": "REJECTED",
            "envelope": {"signal_ts": 1700000000.0}}


def _fn_source(name: str) -> str:
    src = Path(r5.__file__).read_text(encoding="utf-8")
    start = src.index(f"def {name}(")
    rest = src[start + 4:]
    nxt = rest.find("\ndef ")
    return src[start: start + 4 + (nxt if nxt >= 0 else len(rest))]


def _fn_code_no_doc(name: str) -> str:
    body = _fn_source(name)
    if '"""' in body:
        a = body.index('"""')
        b = body.index('"""', a + 3)
        body = body[:a] + body[b + 3:]
    return body


def _assert_no_delete_calls(name: str) -> None:
    body = _fn_code_no_doc(name)
    assert ".unlink(" not in body and "os.unlink(" not in body, name
    assert "rmtree(" not in body, name
    assert "os.remove(" not in body, name


def _fingerprint(root: Path) -> dict[str, bytes]:
    out = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[str(path.relative_to(root))] = path.read_bytes()
    return out


# ---- PR2 market segments (owned by PR2; must stay never-delete) -------------

def test_segment_cap_oldest_first_and_count(tmp_path):
    """PR2: never-delete. Over cap → stop-write + would_have_dropped; files stay."""
    shas = [_seg(tmp_path, i, 100, 1000 + i) for i in range(10)]
    (tmp_path / "v3" / "market_segments" / "zz").mkdir()
    (tmp_path / "v3" / "market_segments" / "zz" / "notasegment.json").write_text("x")
    assert r5.count_market_segments(tmp_path) == 10
    res = r5.cap_market_segments(tmp_path, max_bytes=500, now=5.0)
    assert res["deleted"] == 0 and res["stop_write"] is True
    assert "referenced_deleted" not in res
    assert res["would_have_dropped"]  # old policy would have dropped oldest first
    left = sorted(p.stem for p in (tmp_path / "v3" / "market_segments").rglob("*.json") if len(p.stem) == 64)
    assert left == sorted(shas)
    assert (tmp_path / rotation_cap.CAP_RECEIPTS_FILE).exists()
    assert r5.market_segments_stop_write_latched() is True


def test_market_segments_over_cap_stop_write_never_deletes(tmp_path):
    """PR2: over cap → stop-write + RED; no unlinks (referenced/max_unlinks params removed)."""
    shas = [_seg(tmp_path, i, 100, 1000 + i) for i in range(4)]
    res = r5.cap_market_segments(tmp_path, max_bytes=100, now=time.time())
    assert res["deleted"] == 0 and res["stop_write"] is True
    assert "referenced_deleted" not in res
    assert r5.count_market_segments(tmp_path) == 4
    assert set(shas) == {
        p.stem for p in (tmp_path / "v3" / "market_segments").rglob("*.json") if len(p.stem) == 64
    }
    assert any(r["reason"].startswith("STOP_WRITE") for r in disk_alert.red_conditions())
    assert disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": 1})["level"] == "RED"


def test_pr4_does_not_edit_cap_market_segments_delete_path():
    """Plan 51dde5f11446: PR4 must not touch market-segment unlink (PR2 owns)."""
    body = _fn_code_no_doc("cap_market_segments")
    assert "retention_stop_write" in body
    assert ".unlink(" not in body and "rmtree(" not in body
    assert "SEGMENT_CAP_BYTES" in Path(r5.__file__).read_text()


# ---- PR4 lifecycle / transfer / v22 mutations -------------------------------

def test_cap_lifecycle_bundles_never_rmtree_mutation(tmp_path):
    """Mutation: restoring shutil.rmtree in cap_lifecycle_bundles must fail."""
    _assert_no_delete_calls("cap_lifecycle_bundles")
    now = time.time()
    old = _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "a1", 10, now - 8 * 86400)
    fresh = _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "c3", 10, now - 60)
    before = _fingerprint(tmp_path / "v3" / "lifecycle_bundles")
    res = r5.cap_lifecycle_bundles(tmp_path, max_bytes=0, now=now)
    assert res["removed"] == 0 and res["stop_write"] is True
    assert old.exists() and fresh.exists()
    assert _fingerprint(tmp_path / "v3" / "lifecycle_bundles") == before
    assert r5.lifecycle_bundles_stop_write_latched() is True


def test_lifecycle_alarm_ladder_70_85(tmp_path):
    now = time.time()
    _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "x1", 860, now - 700)
    res = r5.cap_lifecycle_bundles(tmp_path, max_bytes=1000, now=now)
    assert res["alarm_level"] == "RED" and res["removed"] == 0
    # stop_write at hard over (>=100%) only via should_stop_write RED path / hard_over
    _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "x2", 200, now - 700)
    res = r5.cap_lifecycle_bundles(tmp_path, max_bytes=1000, now=now)
    assert res["over_cap"] is True and res["stop_write"] is True and res["removed"] == 0


def test_a7a_lifecycle_unknown_receipt_reports_red(tmp_path, monkeypatch):
    """A7a/F3: UNKNOWN decision → lifecycle receipt alarm_level is RED (never GREEN/UNKNOWN)
    and alarm/health surfaces treat it as RED (plan F2 half A).

    Fixture: GREEN cap ratio + volume_ratio=None → level_for_ratios UNKNOWN.
    Mutation proof (REAL code): monkeypatch ``retention_stop_write.receipt_alarm_level``
    to soft-GREEN / passthrough UNKNOWN → ``res["alarm_level"] == "RED"`` fails (R23).
    """
    import json

    retention_stop_write.set_volume_used_ratio(None)
    now = time.time()
    _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "u1", 100, now - 60)
    assert retention_stop_write.level_for_ratios(0.1, None) == "UNKNOWN"
    assert retention_stop_write.should_stop_write(used=100, cap=1000, volume_ratio=None) is False
    # Normalised helper: case / missing / garbage → RED; known levels uppercased.
    assert retention_stop_write.receipt_alarm_level("UNKNOWN") == "RED"
    assert retention_stop_write.receipt_alarm_level("unknown") == "RED"
    assert retention_stop_write.receipt_alarm_level(None) == "RED"
    assert retention_stop_write.receipt_alarm_level("") == "RED"
    assert retention_stop_write.receipt_alarm_level("amber") == "AMBER"
    assert retention_stop_write.receipt_alarm_level("bogus") == "RED"
    res = r5.cap_lifecycle_bundles(tmp_path, max_bytes=1000, now=now)
    assert res["alarm_level"] == "RED", res
    assert res["alarm_level"] != "UNKNOWN"
    assert res["alarm_level"] != "GREEN"
    assert res["stop_write"] is False
    assert r5.lifecycle_bundles_stop_write_latched() is False
    # Alarm/health surface = lifecycle receipt (F3): written row must be RED, never UNKNOWN/GREEN.
    receipts = list(tmp_path.rglob("cap_receipts.jsonl"))
    assert receipts, "expected lifecycle receipt for RED alarm_level"
    rows = [json.loads(line) for path in receipts for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    life = [row for row in rows if row.get("kind") == "LIFECYCLE_BUNDLE_CAP_PRESSURE_R5"]
    assert life and all(row.get("alarm_level") == "RED" for row in life), life
    # Mutation against the REAL helper cap_lifecycle_bundles calls (not a local stand-in).
    retention_stop_write.clear_latches()

    def soft_green_or_passthru(level):
        # MUTANT: restore ``or "GREEN"`` / leave UNKNOWN ambiguous (pre-F3).
        text = str(level).strip() if level is not None else ""
        return text or "GREEN"

    monkeypatch.setattr(retention_stop_write, "receipt_alarm_level", soft_green_or_passthru)
    mut_root = tmp_path / "mut_a7a"
    _bundle(mut_root, "lifecycle_bundles", "lifecycle-", "m1", 100, now - 60)
    mut = r5.cap_lifecycle_bundles(mut_root, max_bytes=1000, now=now)
    with pytest.raises(AssertionError):
        assert mut["alarm_level"] == "RED"


def test_a7b_lifecycle_unknown_alone_does_not_stop_write(tmp_path, monkeypatch):
    """A7b/F1: UNKNOWN alone must NOT stop-write / latch / block collection.

    Positive control: measured RED ratio DOES stop-write.
    Mutation proof (REAL code): monkeypatch ``should_stop_write`` so UNKNOWN stops →
    ``stop_write is False`` fails (R23).
    """
    retention_stop_write.set_volume_used_ratio(None)
    now = time.time()
    _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "u2", 100, now - 60)
    assert retention_stop_write.should_stop_write(used=100, cap=1000, volume_ratio=None) is False
    res = r5.cap_lifecycle_bundles(tmp_path, max_bytes=1000, now=now)
    assert res["stop_write"] is False
    assert r5.lifecycle_bundles_stop_write_latched() is False
    assert retention_stop_write.is_stopped(r5.FAMILY_LIFECYCLE_BUNDLES) is False
    # Positive control: measured RED ratio (>=0.85) stops.
    retention_stop_write.clear_latches()
    _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "r1", 900, now - 60)
    red = r5.cap_lifecycle_bundles(tmp_path, max_bytes=1000, now=now)
    assert red["alarm_level"] == "RED"
    assert red["stop_write"] is True
    assert r5.lifecycle_bundles_stop_write_latched() is True
    # Mutation against REAL should_stop_write used by evaluate_and_latch.
    retention_stop_write.clear_latches()
    retention_stop_write.set_volume_used_ratio(None)

    def mutant_should_stop(*, used=None, cap=None, volume_ratio=None, hard_over=False):
        if hard_over:
            return True
        level = retention_stop_write.level_for_ratios(
            retention_stop_write.used_ratio(used, cap),
            volume_ratio,
        )
        return level in ("RED", "UNKNOWN")  # MUTANT: UNKNOWN stops

    monkeypatch.setattr(retention_stop_write, "should_stop_write", mutant_should_stop)
    mut_root = tmp_path / "mut_a7b"
    _bundle(mut_root, "lifecycle_bundles", "lifecycle-", "m2", 100, now - 60)
    assert retention_stop_write.should_stop_write(used=100, cap=1000, volume_ratio=None) is True
    mut = r5.cap_lifecycle_bundles(mut_root, max_bytes=1000, now=now)
    with pytest.raises(AssertionError):
        assert mut["stop_write"] is False


def test_is_stopped_rejects_non_red_latch_mutation(monkeypatch):
    """HM: ``is_stopped`` must require alarm_level RED — any-latch ``bool(row)`` is forbidden.

    AMBER latch must not stop. Mutation: replace ``is_stopped`` with ``bool(latch_state)``
    → AMBER-not-stopped assert fails (R23).
    """
    retention_stop_write.clear_latches()
    retention_stop_write.set_volume_used_ratio(None)
    amber = retention_stop_write.evaluate_and_latch(
        "hm_amber_family", used=75, cap=100, reason="HM_AMBER_LATCH",
    )
    assert amber["alarm_level"] == "AMBER"
    assert retention_stop_write.latch_state("hm_amber_family")
    assert retention_stop_write.is_stopped("hm_amber_family") is False
    red = retention_stop_write.evaluate_and_latch(
        "hm_red_family", used=90, cap=100, reason="HM_RED_LATCH",
    )
    assert red["alarm_level"] == "RED"
    assert retention_stop_write.is_stopped("hm_red_family") is True

    def mutant_any_latch(family: str) -> bool:
        return bool(retention_stop_write.latch_state(family))  # MUTANT: any latch = stop

    monkeypatch.setattr(retention_stop_write, "is_stopped", mutant_any_latch)
    assert retention_stop_write.is_stopped("hm_amber_family") is True  # mutant fires
    with pytest.raises(AssertionError):
        assert retention_stop_write.is_stopped("hm_amber_family") is False


def test_purge_retired_transfer_never_rmtree_mutation(tmp_path):
    _assert_no_delete_calls("purge_retired_transfer_bundles")
    now = time.time()
    t = _bundle(tmp_path, "lifecycle_transfer_bundles", "transfer-", "b" * 64, 10, now - 9 * 86400)
    before = _fingerprint(tmp_path / "v3" / "lifecycle_transfer_bundles")
    res = r5.purge_retired_transfer_bundles(tmp_path, now=now)
    assert res["removed"] is False and res["present"] is True and t.exists()
    assert res["stop_write"] is True
    assert _fingerprint(tmp_path / "v3" / "lifecycle_transfer_bundles") == before
    assert "RETIRED_TRANSFER_BUNDLES_PRESENT" in res["reason"]
    absent = r5.purge_retired_transfer_bundles(tmp_path / "empty", now=now)
    assert absent["present"] is False and absent["removed"] is False and absent["stop_write"] is False


def test_seal_drain_discard_v22_never_unlinks_or_rotates_mutation(tmp_path, monkeypatch):
    _assert_no_delete_calls("seal_drain_discard_v22")
    assert "rotate_research_events" not in _fn_code_no_doc("seal_drain_discard_v22")
    from collector_v22 import write_research_event_once, RESEARCH_EVENTS_FILE
    import collector_v22

    monkeypatch.setattr(
        collector_v22, "rotate_research_events",
        lambda **_k: (_ for _ in ()).throw(AssertionError("no rotate-discard")),
    )
    write_research_event_once(_event("e1"), data_dir=str(tmp_path))
    active = tmp_path / RESEARCH_EVENTS_FILE
    before = active.read_bytes()
    now = time.time()
    res = r5.seal_drain_discard_v22(tmp_path, min_bytes=1, clock=lambda: now)
    assert res["status"] == "STOP_WRITE_RETAINED" and res["stop_write"] is True
    assert active.exists() and active.read_bytes() == before
    assert write_research_event_once(_event("after"), data_dir=str(tmp_path)) == (False, "V22_SEAL_DISCARD_STOP_WRITE")


def test_v22_below_threshold_is_noop(tmp_path, monkeypatch):
    from collector_v22 import write_research_event_once
    import collector_v22
    monkeypatch.setattr(
        collector_v22, "rotate_research_events",
        lambda **_k: (_ for _ in ()).throw(AssertionError("no rotate")),
    )
    write_research_event_once(_event("e1"), data_dir=str(tmp_path))
    assert r5.seal_drain_discard_v22(tmp_path, min_bytes=10 ** 9)["status"] == "BELOW_THRESHOLD"
    assert r5.v22_stop_write_latched() is False


def test_v22_seeded_head_at_least_512mib_stop_write(tmp_path, monkeypatch):
    """Plan: seeded v22 active head ≥ V22_SEAL_MIN_BYTES (512 MiB)."""
    import collector_v22
    monkeypatch.setattr(
        collector_v22, "rotate_research_events",
        lambda **_k: (_ for _ in ()).throw(AssertionError("no rotate-discard")),
    )
    active = tmp_path / collector_v22.RESEARCH_EVENTS_FILE
    active.parent.mkdir(parents=True, exist_ok=True)
    # Sparse file — size metadata only; does not allocate 512 MiB of disk.
    with open(active, "wb") as handle:
        handle.truncate(r5.V22_SEAL_MIN_BYTES)
    assert active.stat().st_size >= r5.V22_SEAL_MIN_BYTES
    before = active.stat().st_size
    now = time.time()
    res = r5.seal_drain_discard_v22(tmp_path, min_bytes=r5.V22_SEAL_MIN_BYTES, clock=lambda: now)
    assert res["status"] == "STOP_WRITE_RETAINED" and res["stop_write"] is True
    assert active.stat().st_size == before
    assert res["alarm_level"] == "RED"
    assert r5.v22_stop_write_latched() is True


def test_v22_alarm_ladder_70_85(tmp_path, monkeypatch):
    import collector_v22
    monkeypatch.setattr(
        collector_v22, "rotate_research_events",
        lambda **_k: (_ for _ in ()).throw(AssertionError("no rotate")),
    )
    active = tmp_path / collector_v22.RESEARCH_EVENTS_FILE
    active.parent.mkdir(parents=True, exist_ok=True)
    cap = 1000
    with open(active, "wb") as handle:
        handle.truncate(860)  # 86% → RED pressure without hard stop if below min? use min_bytes=cap
    now = time.time()
    res = r5.seal_drain_discard_v22(tmp_path, min_bytes=cap, clock=lambda: now)
    # 860 < 1000 → BELOW_THRESHOLD but evaluate still reports ratio
    assert res["status"] == "BELOW_THRESHOLD"
    with open(active, "wb") as handle:
        handle.truncate(1000)
    res = r5.seal_drain_discard_v22(tmp_path, min_bytes=cap, clock=lambda: now)
    assert res["status"] == "STOP_WRITE_RETAINED" and res["alarm_level"] == "RED"


# ---- wipe_stale_locks pin + once-path grep ----------------------------------

def test_wipe_stale_locks_scope_pinned(tmp_path):
    locks = tmp_path / "v3" / ".locks"
    locks.mkdir(parents=True)
    now = time.time()
    old = locks / "old.lock"; old.write_text("")
    os.utime(old, (now - 7200, now - 7200))
    recent = locks / "recent.lock"; recent.write_text("")
    os.utime(recent, (now - 600, now - 600))
    other = locks / "keep.txt"; other.write_text("data")
    seg = _seg(tmp_path, 1, 10, now)
    bundle = _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "keep", 10, now - 60)
    res = r5.wipe_stale_locks(tmp_path, boot_ts=now - 1000, now=now, min_age_sec=r5.LOCK_MIN_AGE_SEC)
    assert res["deleted"] == 1 and res["min_age_sec"] == 3600.0
    assert not old.exists() and recent.exists() and other.exists() and bundle.exists()
    assert r5.count_market_segments(tmp_path) == 1
    body = _fn_code_no_doc("wipe_stale_locks")
    assert "os.unlink(" in body
    assert 'endswith(".lock")' in body or ".lock" in body


def test_r5_retention_once_only_locks_unlink_reachable():
    """Grep proof: no delete reachable from r5_retention_once except wipe_stale_locks."""
    bot = (Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8")
    i = bot.index("def r5_retention_once")
    j = bot.index("\ndef r5_retention_loop", i)
    once = bot[i:j]
    assert "wipe_stale_locks" in once
    assert "cap_market_segments" in once
    assert "cap_lifecycle_bundles" in once
    assert "purge_retired_transfer_bundles" in once
    assert "seal_drain_discard_v22" in once
    assert "_r5_v22_drain_probe" not in once
    for name in ("cap_market_segments", "cap_lifecycle_bundles",
                 "purge_retired_transfer_bundles", "seal_drain_discard_v22"):
        _assert_no_delete_calls(name)
    # Only wipe_stale_locks may unlink, and only *.lock under v3/.locks
    wipe = _fn_code_no_doc("wipe_stale_locks")
    assert "os.unlink(" in wipe and ".locks" in _fn_source("wipe_stale_locks")


@pytest.mark.parametrize("segments_env", [None, "0", "1"])
def test_research_segments_env_pr4_deletes_off(tmp_path, monkeypatch, segments_env):
    monkeypatch.delenv("RESEARCH_SEGMENTS_ENABLED", raising=False)
    monkeypatch.delenv("R5_RETENTION_DELETES_ENABLED", raising=False)
    if segments_env is not None:
        monkeypatch.setenv("RESEARCH_SEGMENTS_ENABLED", segments_env)
    now = time.time()
    _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "e1", 50, now - 9 * 86400)
    t = _bundle(tmp_path, "lifecycle_transfer_bundles", "transfer-", "t1", 20, now - 9 * 86400)
    from collector_v22 import write_research_event_once, RESEARCH_EVENTS_FILE
    write_research_event_once(_event("e1"), data_dir=str(tmp_path))
    b_before = _fingerprint(tmp_path / "v3" / "lifecycle_bundles")
    t_before = _fingerprint(tmp_path / "v3" / "lifecycle_transfer_bundles")
    v_before = (tmp_path / RESEARCH_EVENTS_FILE).read_bytes()
    assert r5.cap_lifecycle_bundles(tmp_path, max_bytes=1, now=now)["removed"] == 0
    assert r5.purge_retired_transfer_bundles(tmp_path, now=now)["removed"] is False
    assert r5.seal_drain_discard_v22(tmp_path, min_bytes=1)["status"] == "STOP_WRITE_RETAINED"
    assert _fingerprint(tmp_path / "v3" / "lifecycle_bundles") == b_before
    assert _fingerprint(tmp_path / "v3" / "lifecycle_transfer_bundles") == t_before
    assert (tmp_path / RESEARCH_EVENTS_FILE).read_bytes() == v_before
    assert t.exists()


def test_rejected_arming_env_absent_from_pr4_paths():
    src = Path(r5.__file__).read_text(encoding="utf-8")
    bot = (Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8")
    assert "R5_RETENTION_DELETES_ENABLED" not in src
    assert "R5_RETENTION_DELETES_ENABLED" not in bot


def test_stop_write_never_blocks_force_flat_disarm_r20_or_book10(tmp_path):
    retention_stop_write.latch(r5.FAMILY_LIFECYCLE_BUNDLES, reason="t", used=10, cap=1, now=time.time())
    retention_stop_write.latch(r5.FAMILY_V22, reason="t", used=10, cap=1, now=time.time())
    bot = (Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8")
    for name in ("_disarm_live_control", "_live_guardian_force_flat",
                 "_live_guardian_force_flat_check", "_live_total_loss_arm_block"):
        start = bot.index(f"def {name}")
        window = bot[start: start + 2500]
        assert "retention_stop_write" not in window, name
        assert "lifecycle_bundles_stop_write" not in window, name
    sink = (Path(__file__).resolve().parent / "strategist_streams_collector.py").read_text(encoding="utf-8")
    assert "r5_retention" not in sink and "retention_stop_write" not in sink
    import strategist_streams_collector as ssc
    path = tmp_path / "book10.jsonl"
    # #949 Sink: keep= removed; cap is max_total_bytes (merge-adapt only).
    s = ssc.Sink(str(path), rotate_bytes=10_000, max_total_bytes=10 * 1024 * 1024)
    s.write({"m": 1, "pad": "ok"})
    assert path.exists() and s.rows >= 1


def test_lifecycle_materialize_respects_stop_write(tmp_path):
    from lifecycle_bundles import LifecycleKey, materialize_bundle
    retention_stop_write.latch(r5.FAMILY_LIFECYCLE_BUNDLES, reason="t", used=10, cap=1, now=time.time())
    key = LifecycleKey(collection_epoch_id="e", episode_id="ep", policy_signature="p", research_lane="H_A")
    out = materialize_bundle(tmp_path, key, [])
    assert out["written"] is False and out["stopped_by"] == "LIFECYCLE_BUNDLE_CAP_STOP_WRITE"


def test_reset_asserts_zero_market_segments():
    src = (Path(__file__).resolve().parent / "bot.py").read_text("utf-8")
    i = src.index('stage = "V22_SEAL_RETIREMENT"')
    j = src.index('stage = "GENOME_RESET"', i)
    block = src[i:j]
    assert "r5_retention.count_market_segments(root)" in block
    assert 'disk_alert.raise_red("RESET_LEFT_MARKET_SEGMENTS"' in block
