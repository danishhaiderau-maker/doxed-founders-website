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


def test_oldest_first_active_never_and_three_unlinks_per_call(tmp_path):
    active = _make(tmp_path, FAM, 15)
    (tmp_path / f"{FAM}.bak").write_text("not a rotation")
    (tmp_path / f"{FAM}.1.tmp").write_text("not a rotation")
    res = rotation_cap.enforce_cap(str(active), now=1.0)
    assert res["deleted"] == [f"{FAM}.1", f"{FAM}.2", f"{FAM}.3"]  # .1 is the OLDEST
    assert res["backlog"] is True
    rotation_cap.enforce_cap(str(active))
    res = rotation_cap.enforce_cap(str(active))
    assert res["remaining_rotations"] == 10 and res["backlog"] is False
    assert active.exists() and (tmp_path / f"{FAM}.bak").exists() and (tmp_path / f"{FAM}.1.tmp").exists()
    assert [p for _, p in rotation_cap.list_rotations(str(active))][0].endswith(f"{FAM}.6")


def test_custody_stalled_still_caps(tmp_path, monkeypatch):
    # R5: no custody/ack input exists; a stalled custody marker changes nothing.
    (tmp_path / "custody_stalled.flag").write_text("1")
    monkeypatch.setenv("ARCHIVE_CUSTODY_STALLED", "1")
    active = _make(tmp_path, FAM, 12)
    assert len(rotation_cap.enforce_cap(str(active))["deleted"]) == 2


def test_byte_backstop(tmp_path):
    active = _make(tmp_path, FAM, 4, size=100)
    res = rotation_cap.enforce_cap(str(active), max_bytes=250)
    assert res["deleted"] == [f"{FAM}.1", f"{FAM}.2"]


def test_protected_exempt_and_unclassified_untouched(tmp_path):
    for base in ("lane_pnl_ledger_epoch_receipts.jsonl", "pre_entry_evidence_handoffs.jsonl",
                 "bfx_book_10l_1s.jsonl", "research_events_v22.jsonl", "mystery.jsonl",
                 "relay_outbox_quarantine.jsonl"):  # named live safety (BOT-36)
        active = _make(tmp_path, base, 15)
        res = rotation_cap.enforce_cap(str(active))
        assert res["deleted"] == [], base
        assert len(rotation_cap.list_rotations(str(active))) == 15
    assert rotation_cap.classify("v3/anything.jsonl")[0] == "PROTECTED"
    assert rotation_cap.classify("bfx_book_10l_1s.jsonl")[0] == "EXEMPT"  # paper_fill path unchanged
    assert rotation_cap.classify("x.malformed_rows.jsonl")[0] == "EXEMPT"
    # R22: bare "*quarantine*" substring no longer protects; named live files are CAP_EXEMPT.
    assert rotation_cap.classify("rotation_cap_quarantine.jsonl")[0] == "UNCLASSIFIED"
    assert rotation_cap.classify("relay_outbox_quarantine.jsonl")[0] == "EXEMPT"


def test_no_raw_generation_cleanup_symbols():
    """B2: owner/module/class importables must be gone; lease gate helpers may remain."""
    import ast

    banned_names = {
        "raw_generation_cleanup_owner",
        "RawGenerationCleanup",
        "raw_generation_cleanup_transactions",
        "raw_generation_cleanup_quarantine",
        "raw_generation_cleanup",
    }
    for path in (HERE / "bot.py", HERE / "research_reset_auxiliary_audit.py",
                 HERE / "local_fresh_collection_owner_audit.py"):
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                else:
                    mods = [node.module or ""] + [a.name for a in node.names]
                for mod in mods:
                    for banned in banned_names:
                        assert banned not in (mod or "").split("."), f"{path.name} imports {mod}"
            if isinstance(node, ast.Name) and node.id in banned_names:
                # Allow comments-only mentions via Name would not fire; Name means live symbol use.
                assert False, f"{path.name} still references symbol {node.id}"
            if isinstance(node, ast.Attribute) and node.attr in banned_names:
                assert False, f"{path.name} still references attribute {node.attr}"
    assert not (HERE / "raw_generation_cleanup.py").exists()
    assert not (HERE / "raw_generation_cleanup_owner.py").exists()
    # Gate helpers intentionally kept for fresh-reset lease.
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    assert "def _raw_generation_cleanup_gate_acquire" in src
    assert "def _raw_generation_cleanup_gate_release" in src


def test_rotation_cap_protects_only_named_quarantines():
    """B1/B2: only named live quarantines are EXEMPT; tombstone + corrupt_evidence never scanned."""
    assert "quarantine" not in rotation_cap._PROTECTED_SUBSTRINGS
    assert rotation_cap.NAMED_LIVE_QUARANTINE_BASES == frozenset({"relay_outbox_quarantine.jsonl"})
    for base in rotation_cap.NAMED_LIVE_QUARANTINE_BASES:
        assert rotation_cap.classify(base)[0] == "EXEMPT"
        assert base in rotation_cap.CAP_EXEMPT
    # Tombstone + corrupt_evidence_quarantine/ are documented never-scanned (not rotation bases).
    assert "deleted_trade_ids.json.corrupt.*" in rotation_cap.CAP_NEVER_SCANNED_QUARANTINES
    assert "corrupt_evidence_quarantine/" in rotation_cap.CAP_NEVER_SCANNED_QUARANTINES
    assert rotation_cap.classify("deleted_trade_ids.json.corrupt.20261030T120000.1")[0] == "UNCLASSIFIED"
    assert rotation_cap.classify("some_other_quarantine.jsonl")[0] == "UNCLASSIFIED"


def test_cap_receipts_self_capped_and_failure_never_blocks(tmp_path):
    big = {"pad": "z" * 4000}
    for _ in range(1300):  # ~5 MB of receipts
        assert rotation_cap.write_cap_receipt(str(tmp_path), big)
    files = [p for p in tmp_path.iterdir() if p.name.startswith(rotation_cap.CAP_RECEIPTS_FILE)]
    assert {p.name for p in files} <= {rotation_cap.CAP_RECEIPTS_FILE, rotation_cap.CAP_RECEIPTS_FILE + ".1"}
    assert sum(p.stat().st_size for p in files) <= 2 * rotation_cap.MB + 8192
    # A failing receipt writer never blocks the cap.
    active = _make(tmp_path, FAM, 12)

    def boom(*_a, **_k):
        raise OSError("disk full")
    assert len(rotation_cap.enforce_cap(str(active), receipt_writer=boom)["deleted"]) == 2
    assert rotation_cap.write_cap_receipt(str(tmp_path / "missing" / "dir"), {}) is False


def test_cap_writes_receipt(tmp_path):
    active = _make(tmp_path, FAM, 11)
    rotation_cap.enforce_cap(str(active), now=5.0)
    rows = [json.loads(l) for l in (tmp_path / rotation_cap.CAP_RECEIPTS_FILE).read_text().splitlines()]
    assert rows[-1]["deleted"] == [f"{FAM}.1"] and rows[-1]["rule"] == "R5"


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
    now = 100 * DAY
    active = _make(tmp_path, TAPE, 20)
    for i in range(1, 21):  # .1 oldest: 20 days old ... .20: 1 day old
        os.utime(tmp_path / f"{TAPE}.{i}", (now - (21 - i) * DAY, now - (21 - i) * DAY))
    deleted = []
    for _ in range(5):
        deleted += rotation_cap.enforce_cap(str(active), now=now)["deleted"]
    # rotations older than 14 days (ages 20..15 d -> .1-.6) go; 14 d and younger stay
    assert deleted == [f"{TAPE}.{i}" for i in range(1, 7)]
    assert len(rotation_cap.list_rotations(str(active))) == 14


def test_tape_byte_backstop_still_binds_when_young(tmp_path):
    now = 100 * DAY
    active = _make(tmp_path, TAPE, 4, size=100)
    res = rotation_cap.enforce_cap(str(active), max_bytes=250, now=now)
    assert res["deleted"] == [f"{TAPE}.1", f"{TAPE}.2"] and res["cap_days"] == 14.0
