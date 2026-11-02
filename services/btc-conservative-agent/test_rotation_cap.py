"""DISK-CAP: rotation cap (R5: never custody-gated) and cap receipts."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import rotation_cap
from data_retention_policy import FLY_KEEP_ROTATION_BASES, deletable_rotation

HERE = Path(__file__).resolve().parent
FAM = "signal_snapshot.jsonl"


def _make(tmp_path, base, n, size=10):
    active = tmp_path / base
    active.write_bytes(b"a" * size)
    for i in range(1, n + 1):
        (tmp_path / f"{base}.{i}").write_bytes(b"x" * size)
    return active


def _family_bytes(tmp_path, base):
    import re
    out = {}
    for p in tmp_path.iterdir():
        if p.name == base or re.match(rf"^{re.escape(base)}\.\d+$", p.name):
            out[p.name] = p.read_bytes()
    return out


def test_oldest_first_active_never_and_three_unlinks_per_call(tmp_path):
    """PR2: never-delete. Over keep-N → would_have_dropped (.1 oldest) + stop-write; files stay."""
    active = _make(tmp_path, FAM, 15)
    (tmp_path / f"{FAM}.bak").write_text("not a rotation")
    (tmp_path / f"{FAM}.1.tmp").write_text("not a rotation")
    before = _family_bytes(tmp_path, FAM)
    res = rotation_cap.enforce_cap(str(active), now=1.0)
    assert res["deleted"] == []
    assert res["would_have_dropped"][:3] == [f"{FAM}.1", f"{FAM}.2", f"{FAM}.3"]  # .1 is the OLDEST
    assert res["stop_write"] is True and res["backlog"] is True
    assert _family_bytes(tmp_path, FAM) == before
    assert res["remaining_rotations"] == 15
    assert active.exists() and (tmp_path / f"{FAM}.bak").exists() and (tmp_path / f"{FAM}.1.tmp").exists()
    assert [p for _, p in rotation_cap.list_rotations(str(active))][0].endswith(f"{FAM}.1")


def test_custody_stalled_still_caps(tmp_path, monkeypatch):
    # R5: no custody/ack input exists; a stalled custody marker changes nothing.
    (tmp_path / "custody_stalled.flag").write_text("1")
    monkeypatch.setenv("ARCHIVE_CUSTODY_STALLED", "1")
    active = _make(tmp_path, FAM, 12)
    res = rotation_cap.enforce_cap(str(active))
    assert res["deleted"] == [] and len(res["would_have_dropped"]) == 2 and res["stop_write"] is True


def test_byte_backstop(tmp_path):
    active = _make(tmp_path, FAM, 4, size=100)
    before = _family_bytes(tmp_path, FAM)
    res = rotation_cap.enforce_cap(str(active), max_bytes=250)
    assert res["deleted"] == [] and res["would_have_dropped"] == [f"{FAM}.1", f"{FAM}.2"]
    assert _family_bytes(tmp_path, FAM) == before


def test_protected_exempt_and_unclassified_untouched(tmp_path):
    for base in ("lane_pnl_ledger_epoch_receipts.jsonl", "pre_entry_evidence_handoffs.jsonl",
                 "bfx_book_10l_1s.jsonl", "research_events_v22.jsonl", "mystery.jsonl",
                 "rotation_cap_quarantine.jsonl"):
        active = _make(tmp_path, base, 15)
        res = rotation_cap.enforce_cap(str(active))
        assert res["deleted"] == [], base
        assert len(rotation_cap.list_rotations(str(active))) == 15
    assert rotation_cap.classify("v3/anything.jsonl")[0] == "PROTECTED"
    assert rotation_cap.classify("bfx_book_10l_1s.jsonl")[0] == "EXEMPT"  # paper_fill path unchanged
    assert rotation_cap.classify("x.malformed_rows.jsonl")[0] == "EXEMPT"


def test_cap_receipts_self_capped_and_failure_never_blocks(tmp_path):
    big = {"pad": "z" * 4000}
    for _ in range(1300):  # ~5 MB of receipts
        assert rotation_cap.write_cap_receipt(str(tmp_path), big)
    files = [p for p in tmp_path.iterdir() if p.name.startswith(rotation_cap.CAP_RECEIPTS_FILE)]
    assert {p.name for p in files} <= {rotation_cap.CAP_RECEIPTS_FILE, rotation_cap.CAP_RECEIPTS_FILE + ".1"}
    assert sum(p.stat().st_size for p in files) <= 2 * rotation_cap.MB + 8192
    # A failing receipt writer never blocks the stop-write decision.
    active = _make(tmp_path, FAM, 12)

    def boom(*_a, **_k):
        raise OSError("disk full")
    res = rotation_cap.enforce_cap(str(active), receipt_writer=boom)
    assert res["deleted"] == [] and len(res["would_have_dropped"]) == 2 and res["stop_write"] is True
    assert rotation_cap.write_cap_receipt(str(tmp_path / "missing" / "dir"), {}) is False


def test_cap_writes_receipt(tmp_path):
    active = _make(tmp_path, FAM, 11)
    rotation_cap.enforce_cap(str(active), now=5.0)
    rows = [json.loads(l) for l in (tmp_path / rotation_cap.CAP_RECEIPTS_FILE).read_text().splitlines()]
    assert rows[-1]["deleted"] == [] and rows[-1]["would_have_dropped"] == [f"{FAM}.1"]
    assert rows[-1]["rule"] == "R25_NEVER_DELETE" and rows[-1]["stop_write"] is True


def test_reset_lists_clear_cap_receipts():
    import full_reset_no_archive
    import runtime_hygiene
    assert "cap_receipts.jsonl" in full_reset_no_archive.DELETE_EXACT_NAMES
    assert "v22_discard_receipts.jsonl" in full_reset_no_archive.DELETE_EXACT_NAMES
    assert "cap_receipts.jsonl" in runtime_hygiene.PRE_EPOCH_LEDGERS
    src = (HERE / "bot.py").read_text("utf-8")
    # research_wipe_file_paths + _research_wipe_rotated_jsonl_paths
    assert src.count('"v22_discard_receipts.jsonl"') >= 2
    assert src.count("rotation_cap.CAP_RECEIPTS_FILE,") >= 2


def test_sweep_only_touches_allow_list(tmp_path):
    _make(tmp_path, FAM, 13)
    _make(tmp_path, "mystery.jsonl", 13)
    out = rotation_cap.sweep(str(tmp_path))
    assert [r["path"] for r in out] == [FAM]
    assert len(rotation_cap.list_rotations(str(tmp_path / "mystery.jsonl"))) == 13


def test_fly_keep_chase_grid_is_not_retention_deletable():
    assert "chase_offset_touch_grid.jsonl" in FLY_KEEP_ROTATION_BASES
    assert deletable_rotation("chase_offset_touch_grid.jsonl.3", fly=True) is False


# ---- CI: every rotate_log/_safe_append_jsonl family in bot.py is classified ----
# Names bound via imported modules (not resolvable by AST) -> their basename.
_IMPORTED_FAMILY_NAMES = {
    "AI_SHADOW_CHALLENGER_FILE": "ai_shadow_challengers.jsonl",
    "AI_SHADOW_COMPACT_PROMPT_FILE": "ai_shadow_compact_prompt.jsonl",
    "AI_SHADOW_REGIME_PROMPT_FILE": "ai_shadow_regime_prompt.jsonl",
    "CHASE_OFFSET_TOUCH_GRID_FILE": "chase_offset_touch_grid.jsonl",
    "CYCLE_3M_UNIVERSE_FILE": "cycle_3m_universe.jsonl",
    "DECISION_FEATURE_SNAPSHOT_FILE": "decision_feature_snapshots.jsonl",
    "MICROSTRUCTURE_TAPE_FILE": "market_microstructure_1s.jsonl",
    "OPPORTUNITY_CAPTURE_FILE": "opportunity_capture.jsonl",
    "PATH_REPLAY_FILE": "path_replay.jsonl",
    "_dyn_regime.FILE_NAME": "dyn_regime_minutes.jsonl",
    "execution_markouts.TAKER_FILE": "taker_signal_counterfactuals.jsonl",
}
# Dynamic paths (sidecars/generic helpers); classified by suffix at runtime.
_DYNAMIC_ARGS = {"path", "sidecar"}


def test_every_appended_family_is_classified():
    tree = ast.parse((HERE / "bot.py").read_text("utf-8"))
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            strs = [c.value for c in ast.walk(node.value)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value.endswith(".jsonl")]
            if strs:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = strs[-1]
    families, unresolved = set(), set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("rotate_log", "_safe_append_jsonl") and node.args):
            arg = node.args[0]
            text = ast.unparse(arg)
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                families.add(arg.value)
            elif isinstance(arg, ast.Name) and arg.id in consts:
                families.add(consts[arg.id])
            elif text in _IMPORTED_FAMILY_NAMES:
                families.add(_IMPORTED_FAMILY_NAMES[text])
            elif text not in _DYNAMIC_ARGS:
                unresolved.add(text)
    assert unresolved == set(), f"add these families to rotation_cap or this test: {sorted(unresolved)}"
    unclassified = sorted(f for f in families if not rotation_cap.is_classified(f))
    assert unclassified == []
    assert os.path.basename("x") == "x"


# ---- RK 23 Oct ~21:12: 1 s tape is days-capped (14 d), tied to the GS-FADE3 AMEND1-3 preregs ----
TAPE = "market_microstructure_1s.jsonl"
DAY = 86400.0


def test_tape_cap_is_days_based_with_60mb_per_day_backstop():
    days, per_day = rotation_cap.CAP_DAYS[TAPE]
    assert days == 14 and per_day >= 60 * rotation_cap.MB  # Strategist's spec (RK 23 Oct): 60 MB/day
    assert per_day == 60 * rotation_cap.MB
    rot, cap_bytes = rotation_cap.CAP_ALLOW[TAPE]
    assert cap_bytes == int(days * per_day) and rot >= 10_000  # count never binds; age + byte backstop do


def test_tape_cap_comment_names_the_fade3_prereg_set():
    src = (HERE / "rotation_cap.py").read_text("utf-8")
    for token in ("GS-FADE3-TP30", "AMEND1", "AMEND2", "AMEND3", "CLOSE or are KILLED", "must never price (R17)",
                  "TICKER-derived", "tape_minute_bars"):
        assert token in src


def test_tape_keeps_14_days_of_rotations_and_drops_older(tmp_path):
    """PR2: age pressure → would_have_dropped telemetry + stop-write; no unlinks."""
    now = 100 * DAY
    active = _make(tmp_path, TAPE, 20)
    for i in range(1, 21):  # .1 oldest: 20 days old ... .20: 1 day old
        os.utime(tmp_path / f"{TAPE}.{i}", (now - (21 - i) * DAY, now - (21 - i) * DAY))
    before = _family_bytes(tmp_path, TAPE)
    would = rotation_cap.enforce_cap(str(active), now=now)["would_have_dropped"]
    # rotations older than 14 days (ages 20..15 d -> .1-.6) would have gone
    assert would == [f"{TAPE}.{i}" for i in range(1, 7)]
    assert len(rotation_cap.list_rotations(str(active))) == 20
    assert _family_bytes(tmp_path, TAPE) == before


def test_tape_byte_backstop_still_binds_when_young(tmp_path):
    now = 100 * DAY
    active = _make(tmp_path, TAPE, 4, size=100)
    res = rotation_cap.enforce_cap(str(active), max_bytes=250, now=now)
    assert res["deleted"] == [] and res["would_have_dropped"] == [f"{TAPE}.1", f"{TAPE}.2"]
    assert res["cap_days"] == 14.0 and res["stop_write"] is True
