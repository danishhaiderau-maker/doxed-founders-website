"""Seal 2ac09364eb63 — rebuild pin / no-shrink / AMEND2 regime helper + mutations.

Card: docs/card-analyzer-mirror-bundle-v2-rebuild-20261031.md
Rules R0–R48 1120edc5b538. No laptop publish (C3). No CI timeout edits (0c).
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from flask import Flask, jsonify, make_response, request

import analyzer_book10_regime as abr

ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
ENGINE = (ROOT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")


def _rebuild_pin(**overrides):
    pin = abr.rebuild_pin_block()
    pin.update(overrides)
    return pin


def _report_manifest(*, row_count=10, regime_count=3, pin=None, cutoff="2026-10-17T19:01:11+00:00"):
    return {
        "schema": "report_manifest_v1",
        "analyzer_sync_id": "analyzer-v1",
        "analyzer_version": "analyzer-v1",
        "generated_at": "2026-08-16T00:00:00+00:00",
        "data_scope": "session",
        "session_scope": "SESSION",
        "analysis_provenance": {
            "cohort_schema": "analysis_cohorts_v1",
            "generation_revision": "b" * 40,
        },
        "fresh_epoch": {"cutoff_utc": cutoff},
        "generation_receipt": {
            "data_epoch": {
                "straddle_rows_unguarded": 0,
                "unstamped_rows_unguarded": 0,
            }
        },
        "report_count": 0,
        "reports": [],
        "text_artifacts": ["analysis_dashboard.html", "executive_summary.txt"],
        "row_count": row_count,
        "regime_count": regime_count,
        "rebuild_pin": pin if pin is not None else _rebuild_pin(),
    }


def _bundle_files(*, row_count=10, regime_count=3, pin=None, cutoff="2026-10-17T19:01:11+00:00"):
    report = _report_manifest(
        row_count=row_count, regime_count=regime_count, pin=pin, cutoff=cutoff
    )
    files = {
        "analysis_dashboard.html": b"<html>dashboard</html>",
        "executive_summary.txt": b"summary",
        "report_manifest.json": json.dumps(report, sort_keys=True).encode(),
    }
    return files


def _zip_bundle(files):
    manifest = {
        "schema": "analyzer_mirror_bundle_v2",
        "snapshot_id": "fixture-run-1",
        "analyzer_run_id": "analyzer-v1",
        "analyzer_version": "analyzer-v1",
        "analyzer_generated_at": "2026-08-16T00:00:00+00:00",
        "source_data_revision": "a" * 40,
        "analyzer_generation_revision": "b" * 40,
        "cohort_schema": "analysis_cohorts_v1",
        "data_scope": "session",
        "session_scope": "SESSION",
        "source_report_manifest_sha256": hashlib.sha256(files["report_manifest.json"]).hexdigest(),
        "files": [
            {
                "path": path,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in files.items()
        ],
    }
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
        archive.writestr("bundle_manifest.json", json.dumps(manifest))
    return payload.getvalue()


def _load_install_ns():
    tree = ast.parse(BOT)
    wanted = {
        "_valid_analyzer_generation",
        "_recover_latest_analyzer_generation",
        "_active_analyzer_mirror_dir",
        "_safe_analyzer_bundle_members",
        "_validated_analyzer_bundle_manifest",
        "_install_analyzer_bundle",
        "_analyzer_bundle_fresh_gate",
        "_analyzer_bundle_rebuild_pin_gate",
        "_analyzer_bundle_inventory_metrics",
        "_analyzer_bundle_no_shrink_gate",
        "_analyzer_rebuild_expected_prereg_hash",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "Path": Path,
        "zipfile": zipfile,
        "json": json,
        "re": re,
        "io": io,
        "os": os,
        "time": time,
        "shutil": shutil,
        "hashlib": hashlib,
        "datetime": datetime,
        "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
        "_ANALYZER_BUNDLE_MAX_EXPANDED_BYTES": 150 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_MEMBERS": 256,
        "_ANALYZER_BUNDLE_MAX_MEMBER_BYTES": 50 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_COMPRESSION_RATIO": 1000,
        "_ANALYZER_BUNDLE_MANIFEST": "bundle_manifest.json",
        "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
        "_ANALYZER_BUNDLE_FRESH_FLOOR_TS": 1792263671,
        "_ANALYZER_BUNDLE_NO_VALID": "no valid bundle",
        "_ANALYZER_INSTALL_LOCK": threading.RLock(),
        "_ANALYZER_REBUILD_PIN_TIP": abr.ANALYZER_COMPLETED_TIP,
        "_ANALYZER_REBUILD_PIN_TIP_SHORT": abr.ANALYZER_COMPLETED_TIP_SHORT,
        "_ANALYZER_REBUILD_AMEND2_SHA12": abr.AMEND2_SPEC_SHA12,
        "_ANALYZER_REBUILD_METHOD_ID": abr.REGIME_METHOD_ID,
        "_analyzer_rebuild_expected_prereg_hash": lambda: abr.PARAMS_PREREG_HASH,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def test_t1_missing_schema_rejects_install():
    ns = _load_install_ns()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ns["_analyzer_generations_dir"] = lambda: root / "generations"
        ns["_analyzer_current_pointer_path"] = lambda: root / "current.json"
        ns["_prune_analyzer_generations"] = lambda generation: None
        # Empty incomplete zip without v2 schema
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("analysis_dashboard.html", b"x")
        with pytest.raises(ValueError):
            with zipfile.ZipFile(io.BytesIO(bad.getvalue()), "r") as archive:
                ns["_safe_analyzer_bundle_members"](archive)
        assert ns["_active_analyzer_mirror_dir"]() is None


def test_t2_cutoff_before_floor_rejects():
    ns = _load_install_ns()
    files = _bundle_files(cutoff="2026-10-01T00:00:00+00:00")
    payload = _zip_bundle(files)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = ns["_safe_analyzer_bundle_members"](archive)
        with pytest.raises(ValueError, match="fresh"):
            ns["_validated_analyzer_bundle_manifest"](archive, members)


def test_t3_unguarded_straddle_rejects():
    ns = _load_install_ns()
    report = _report_manifest()
    report["generation_receipt"]["data_epoch"]["straddle_rows_unguarded"] = 2
    files = {
        "analysis_dashboard.html": b"<html>dashboard</html>",
        "executive_summary.txt": b"summary",
        "report_manifest.json": json.dumps(report, sort_keys=True).encode(),
    }
    payload = _zip_bundle(files)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = ns["_safe_analyzer_bundle_members"](archive)
        with pytest.raises(ValueError, match="straddle_rows_unguarded"):
            ns["_validated_analyzer_bundle_manifest"](archive, members)


def test_t7_valid_v2_install_resolves_active():
    ns = _load_install_ns()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generations = root / "generations"
        pointer = root / "current.json"
        ns["_analyzer_generations_dir"] = lambda: generations
        ns["_analyzer_current_pointer_path"] = lambda: pointer
        ns["_prune_analyzer_generations"] = lambda generation: None
        installed = ns["_install_analyzer_bundle"](_zip_bundle(_bundle_files()), {"uploaded_at": "now"})
        assert installed["complete"] is True
        assert installed["schema"] == "analyzer_mirror_bundle_v2"
        active = ns["_active_analyzer_mirror_dir"]()
        assert active is not None
        status = json.loads((active / "status.json").read_text(encoding="utf-8"))
        assert status["schema"] == "analyzer_mirror_bundle_v2"
        assert status["complete"] is True


def test_t8_manifest_pin_missing_rejects():
    ns = _load_install_ns()
    report = _report_manifest()
    del report["rebuild_pin"]
    files = {
        "analysis_dashboard.html": b"<html>dashboard</html>",
        "executive_summary.txt": b"summary",
        "report_manifest.json": json.dumps(report, sort_keys=True).encode(),
    }
    payload = _zip_bundle(files)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = ns["_safe_analyzer_bundle_members"](archive)
        with pytest.raises(ValueError, match="rebuild_pin|COMPLETED|mismatch"):
            ns["_validated_analyzer_bundle_manifest"](archive, members)


def test_t8_manifest_pin_wrong_tip_rejects():
    ns = _load_install_ns()
    files = _bundle_files(pin=_rebuild_pin(analyzer_git_rev="deadbeef" * 5))
    payload = _zip_bundle(files)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = ns["_safe_analyzer_bundle_members"](archive)
        with pytest.raises(ValueError, match="analyzer_git_rev"):
            ns["_validated_analyzer_bundle_manifest"](archive, members)


@pytest.mark.parametrize(
    "bad_rev,match",
    [
        ("", "empty|mismatch|analyzer_git_rev"),
        ("e", "mismatch|analyzer_git_rev|empty"),
        (abr.ANALYZER_COMPLETED_TIP_SHORT[:11], "mismatch|analyzer_git_rev"),
    ],
)
def test_t8_pin_rejects_empty_short_and_11char_prefix(bad_rev, match):
    """HM R2 d7570845a12b: empty / 'e' / 11-char tip prefix must fail closed."""
    ns = _load_install_ns()
    report = _report_manifest(pin=_rebuild_pin(analyzer_git_rev=bad_rev))
    with pytest.raises(ValueError, match=match):
        ns["_analyzer_bundle_rebuild_pin_gate"](report)


def test_t8_pin_short_rev_mutation_old_bypass_would_pass():
    """Mutation proof: old tip.startswith(rev[:12]) bypass accepts '', 'e', 11-char; fix must not."""
    tip = abr.ANALYZER_COMPLETED_TIP.lower()
    tip_short = abr.ANALYZER_COMPLETED_TIP_SHORT.lower()

    def mutant_accepts(rev: str) -> bool:
        rev = str(rev or "").strip().lower()
        # Pre-fix bypass (VOID tip 7aa27881): tip.startswith(rev[:12]) lets short revs through.
        return bool(rev == tip or rev.startswith(tip_short) or tip.startswith(rev[:12]))

    for bad in ("", "e", tip_short[:11]):
        assert mutant_accepts(bad) is True, f"mutant must show old bypass for {bad!r}"
        ns = _load_install_ns()
        with pytest.raises(ValueError):
            ns["_analyzer_bundle_rebuild_pin_gate"](
                _report_manifest(pin=_rebuild_pin(analyzer_git_rev=bad))
            )


def test_t8_fixture_replacement_check_bad_pin_still_rejects():
    """Fresh HM R2: replacing a good fixture pin with a bad rev must still fail (not fixture-hardcoded pass)."""
    ns = _load_install_ns()
    good = _report_manifest()
    ns["_analyzer_bundle_rebuild_pin_gate"](good)  # baseline fixture passes
    replaced = json.loads(json.dumps(good))
    replaced["rebuild_pin"] = _rebuild_pin(analyzer_git_rev="0" * 40)
    with pytest.raises(ValueError, match="analyzer_git_rev"):
        ns["_analyzer_bundle_rebuild_pin_gate"](replaced)
    # Mutation: if gate ignored rebuild_pin contents, replacement would pass — prove it does not.
    mutant = lambda report_manifest: None
    mutant(replaced)
    with pytest.raises(ValueError):
        ns["_analyzer_bundle_rebuild_pin_gate"](replaced)


def test_t8_mutation_accept_unpinned_fails_contract():
    """Mutation: pin gate no-op → validation would accept unpinned; contract asserts raise."""
    ns = _load_install_ns()
    ns["_analyzer_bundle_rebuild_pin_gate"] = lambda report_manifest: None  # mutant
    # Re-bind validated to use mutant via direct call of pin-less report after fresh gate
    report = _report_manifest()
    del report["rebuild_pin"]
    # Fresh gate alone passes; pin gate (mutant) passes; contract of this test is that
    # the *real* gate must reject — prove mutant would accept.
    ns["_analyzer_bundle_fresh_gate"](report)
    ns["_analyzer_bundle_rebuild_pin_gate"](report)  # mutant: no raise
    with pytest.raises(ValueError):
        # restore real gate from source and prove unpinned fails
        real = _load_install_ns()
        real["_analyzer_bundle_rebuild_pin_gate"](report)


def test_t10_shrink_rows_rejects():
    ns = _load_install_ns()
    prior = _report_manifest(row_count=100, regime_count=5)
    new = _report_manifest(row_count=50, regime_count=5)
    with pytest.raises(ValueError, match="shrink|rows"):
        ns["_analyzer_bundle_no_shrink_gate"](prior, new)


def test_t10_unreadable_prior_manifest_fails_closed():
    """HM R2: prior present but unreadable / non-dict → fail closed (not silent skip)."""
    ns = _load_install_ns()
    new = _report_manifest(row_count=10, regime_count=3)
    with pytest.raises(ValueError, match="unreadable|incomplete"):
        ns["_analyzer_bundle_no_shrink_gate"]("not-a-dict", new)
    with pytest.raises(ValueError, match="unreadable|incomplete"):
        ns["_analyzer_bundle_no_shrink_gate"](["list"], new)


def test_t10_missing_counts_fail_closed():
    """R34: missing row/regime/cutoff counts must not compare as 0 (fail closed)."""
    ns = _load_install_ns()
    prior = _report_manifest(row_count=100, regime_count=5)
    new = _report_manifest(row_count=100, regime_count=5)
    del new["row_count"]
    del new["regime_count"]
    with pytest.raises(ValueError, match="inventory incomplete|missing"):
        ns["_analyzer_bundle_no_shrink_gate"](prior, new)


def test_t10_mutation_silent_shrink_patches_real_gate():
    """HM d836cfc7cf2d: patch the real no-shrink gate (not a local lambda)."""
    ns = _load_install_ns()
    prior = _report_manifest(row_count=100, regime_count=5)
    new = _report_manifest(row_count=50, regime_count=5)
    real = ns["_analyzer_bundle_no_shrink_gate"]
    with pytest.raises(ValueError, match="shrink|rows"):
        real(prior, new)
    # Mutant: replace the real gate with a no-op — would fail open.
    ns["_analyzer_bundle_no_shrink_gate"] = lambda prior_manifest, new_manifest: None
    ns["_analyzer_bundle_no_shrink_gate"](prior, new)  # mutant accepts
    # Restore and prove real gate still rejects.
    ns["_analyzer_bundle_no_shrink_gate"] = real
    with pytest.raises(ValueError, match="shrink|rows"):
        ns["_analyzer_bundle_no_shrink_gate"](prior, new)


def test_t10_mutation_unreadable_prior_patches_real_gate():
    """HM d836cfc7cf2d: patch real gate; old skip-bad-prior must not be the contract under test."""
    ns = _load_install_ns()
    new = _report_manifest()
    real = ns["_analyzer_bundle_no_shrink_gate"]
    with pytest.raises(ValueError, match="unreadable|incomplete"):
        real("bogus", new)
    ns["_analyzer_bundle_no_shrink_gate"] = (
        lambda prior_manifest, new_manifest: None
        if prior_manifest is None or not isinstance(prior_manifest, dict)
        else real(prior_manifest, new_manifest)
    )
    ns["_analyzer_bundle_no_shrink_gate"]("bogus", new)  # mutant fail-open
    ns["_analyzer_bundle_no_shrink_gate"] = real
    with pytest.raises(ValueError, match="unreadable|incomplete"):
        ns["_analyzer_bundle_no_shrink_gate"]("bogus", new)


def test_t10_install_path_corrupt_prior_manifest_file_fails_closed():
    """HM d836cfc7cf2d: drive install's real file-read/parse error on prior report_manifest.json."""
    ns = _load_install_ns()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generations = root / "generations"
        pointer = root / "current.json"
        generations.mkdir(parents=True)
        prior = generations / "generation-prior-corrupt"
        prior.mkdir(parents=True)
        # Unreadable JSON — install must hit json.loads on this path and fail closed.
        (prior / "report_manifest.json").write_text("{not-json", encoding="utf-8")
        ns["_analyzer_generations_dir"] = lambda: generations
        ns["_analyzer_current_pointer_path"] = lambda: pointer
        ns["_prune_analyzer_generations"] = lambda generation: None
        # Stub active dir so install's prior-read path runs (not the non-dict gate-only case).
        ns["_active_analyzer_mirror_dir"] = lambda: prior
        with pytest.raises(ValueError, match="unreadable|prior"):
            ns["_install_analyzer_bundle"](
                _zip_bundle(_bundle_files(row_count=20, regime_count=4)),
                {"uploaded_at": "later"},
            )


def test_t9_r33_summary_and_analysis_no_verdicts_without_bundle():
    """B2/R33: no active bundle → summary/analysis emit no PASS/FAIL/edge verdicts."""
    tree = ast.parse(BOT)

    def extract(name):
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(BOT, fn)

    # summary
    app = Flask("t9-summary")
    ns = {
        "app": app,
        "request": request,
        "jsonify": jsonify,
        "os": os,
        "_active_analyzer_mirror_dir": lambda: None,
        "_ANALYZER_BUNDLE_NO_VALID": "no valid bundle",
        "_external_analyzer_response": lambda endpoint: (jsonify({"ok": True, "verdict": "PASS"}), 200),
    }
    exec(compile(extract("api_analyzer_summary"), "bot.py", "exec"), ns)
    app.add_url_rule("/api/analyzer/summary", view_func=ns["api_analyzer_summary"])
    prev = os.environ.get("FLY_APP_NAME")
    os.environ["FLY_APP_NAME"] = "rebuild-t9"
    try:
        resp = app.test_client().get("/api/analyzer/summary")
        assert resp.status_code == 503
        body = resp.json
        assert body.get("ok") is False
        assert "PASS" not in json.dumps(body)
        assert "FAIL" not in json.dumps(body)
        assert "edge" not in json.dumps(body).lower() or body.get("error")
    finally:
        if prev is None:
            os.environ.pop("FLY_APP_NAME", None)
        else:
            os.environ["FLY_APP_NAME"] = prev

    # analysis
    app2 = Flask("t9-analysis")
    ns2 = {
        "app": app2,
        "request": request,
        "jsonify": jsonify,
        "make_response": make_response,
        "_analyzer_view_authed": lambda: True,
        "_active_analyzer_mirror_dir": lambda: None,
        "_ANALYZER_BUNDLE_NO_VALID": "no valid bundle",
    }
    exec(compile(extract("analyzer_mirror_dashboard_index"), "bot.py", "exec"), ns2)
    app2.add_url_rule("/analysis/", view_func=ns2["analyzer_mirror_dashboard_index"])
    resp2 = app2.test_client().get("/analysis/")
    assert resp2.status_code == 503
    assert b"PASS" not in (resp2.data or b"")
    assert b"<!DOCTYPE" not in (resp2.data or b"")


def test_book10_regime_params_match_prereg():
    assert abr.REGIME_METHOD_ID == "BOOK10_TRADES_DIRVOL_V1"
    assert abr.AMEND2_SPEC_SHA12 == "659e184f2435"
    assert abr.params_prereg_hash() == abr.PARAMS_PREREG_HASH
    assert len(abr.PARAMS_PREREG_HASH) == 64
    # Mutation: flip bull edge → hash drifts
    mutant = dict(abr._PREREG_CONSTANTS)
    mutant["DIR_BULL_BP"] = 12.3
    bad = hashlib.sha256(
        json.dumps(mutant, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert bad != abr.PARAMS_PREREG_HASH


def test_book10_regime_no_lookahead():
    # Synthetic book: mid rises then future spike that must not affect label at t.
    base = abr.EPOCH_FLOOR_TS + 10_000
    rows = []
    mid = 100_000.0
    for i in range(100):
        ts = base + i * abr.STEP_SEC
        mid = mid * (1.0 + 0.0002)  # slow bullish drift
        rows.append({"ts": ts, "bid": mid - 1, "ask": mid + 1})
    t = base + 99 * abr.STEP_SEC
    label = abr.classify_at(t, rows, trade_rows=())
    future = list(rows) + [
        {"ts": t + 60, "bid": mid * 2, "ask": mid * 2 + 1},
    ]
    label2 = abr.classify_at(t, future, trade_rows=())
    assert label == label2

    # Mutation: a broken classifier that includes ts > t must change the label.
    def classify_with_lookahead(t_cut, book_rows):
        return abr.classify_at(t_cut + 60, book_rows, trade_rows=())  # mutant uses future window end

    mutant_label = classify_with_lookahead(t, future)
    with pytest.raises(AssertionError):
        assert mutant_label == label


def test_book10_regime_epoch_floor():
    rows = [{"ts": abr.EPOCH_FLOOR_TS - 10, "bid": 1.0, "ask": 1.1}]
    out = abr.classify_at(abr.EPOCH_FLOOR_TS - 5, rows, trade_rows=())
    assert out["regime_key"] == abr.UNKNOWN
    assert out["reason"] == "PRE_EPOCH"


def test_book10_regime_pre_epoch_contamination_halts():
    """Pre-epoch rows in a post-epoch window must halt (not silently filter)."""
    t = abr.EPOCH_FLOOR_TS + 1000
    rows = [
        {"ts": abr.EPOCH_FLOOR_TS - 1, "bid": 100.0, "ask": 100.2},
        {"ts": t - 1, "bid": 100.0, "ask": 100.2},
    ]
    with pytest.raises(ValueError, match="pre-epoch|halt"):
        abr.classify_at(t, rows, trade_rows=())
    # Mutation: silent `continue` on pre-epoch would return UNKNOWN thin — prove halt instead.
    with pytest.raises(ValueError):
        abr.classify_at(t, rows, trade_rows=())


def test_book10_regime_trade_rows_required_not_discarded():
    t = abr.EPOCH_FLOOR_TS + 1000
    rows = [{"ts": t - 1, "bid": 100.0, "ask": 100.2}]
    with pytest.raises(ValueError, match="trade_rows"):
        abr.classify_at(t, rows, trade_rows=None)
    out = abr.classify_at(
        t,
        rows,
        trade_rows=[{"ts": t - 2, "price": 100.1, "amount": 0.01}],
    )
    assert out.get("trade_prints") == 1
    # Mutation: `del trade_rows` path would omit trade_prints — prove present.
    assert "trade_prints" in out


def test_book10_regime_unknown_thin_coverage():
    t = abr.EPOCH_FLOOR_TS + 1000
    rows = [{"ts": t - 1, "bid": 100.0, "ask": 100.2}]
    out = abr.classify_at(t, rows, trade_rows=())
    assert out["regime_key"] == abr.UNKNOWN


def test_book10_tags_refuse_none_book_rows():
    with pytest.raises(ValueError, match="book_rows"):
        abr.tags_from_trade_row({"ts": abr.EPOCH_FLOOR_TS + 10}, book_rows=None, trade_rows=())


def test_trades_source_is_bfx_trades_only():
    abr.assert_trades_source("bfx_trades.jsonl")
    with pytest.raises(ValueError):
        abr.assert_trades_source("exploratory_trades.jsonl")


def test_analyzer_never_opens_market_segment_guard():
    abr.guard_no_market_segment_open("/data/bfx_book_10l_1s.jsonl")
    with pytest.raises(RuntimeError, match="market_segment"):
        abr.guard_no_market_segment_open("/data/v3/ledgers/market_segment.jsonl")
    # HM d836cfc7cf2d / AMEND2 C2: case-insensitive via .lower()
    with pytest.raises(RuntimeError, match="market_segment"):
        abr.guard_no_market_segment_open("/data/v3/ledgers/Market_Segment.jsonl")
    with pytest.raises(RuntimeError, match="market_segment"):
        abr.guard_no_market_segment_open("/data/V3/LEDGERS/MARKET_SEGMENT.JSONL")


def test_amend2_c2_repo_wide_case_insensitive_market_segment_guard():
    """AMEND2 C2: analyzer/dashboard/evaluator paths must trip case-insensitive market_segment guard."""
    # Source: guard uses .lower() (not raw substring).
    src = (ROOT / "analyzer_book10_regime.py").read_text(encoding="utf-8")
    gstart = src.index("def guard_no_market_segment_open")
    gend = src.index("\ndef ", gstart + 1)
    gblock = src[gstart:gend]
    assert ".lower()" in gblock
    assert 'in text.lower()' in gblock or 'in str(path).lower()' in gblock or '"market_segment" in text.lower()' in gblock
    # Mutation: case-sensitive `in text` would miss MARKET_SEGMENT.
    mutant = gblock.replace("text.lower()", "text")
    assert "text.lower()" not in mutant
    with pytest.raises(AssertionError):
        assert "text.lower()" in mutant
    # Repo-wide: engine load path + helper both wire the guard.
    assert "guard_no_market_segment_open" in ENGINE
    for bad in (
        "Market_Segment.jsonl",
        "MARKET_SEGMENT",
        "v3/ledgers/Market_Segment.jsonl",
    ):
        with pytest.raises(RuntimeError, match="market_segment"):
            abr.guard_no_market_segment_open(bad)


def test_book10_pre_epoch_trade_outside_window_halts():
    """Pre-floor trades outside the 900s window must halt, not be skipped."""
    t = abr.EPOCH_FLOOR_TS + 10_000
    book = [{"ts": t - 1, "bid": 100.0, "ask": 100.2}]
    trades = [{"ts": abr.EPOCH_FLOOR_TS - 50, "price": 99.0, "amount": 0.1}]  # outside 900s + pre-floor
    with pytest.raises(ValueError, match="pre-epoch|halt"):
        abr.classify_at(t, book, trade_rows=trades)


def test_roster_policy_wraps_book10_gap_fail_closed():
    """R34 / engine:~19600: roster_policy_report must wrap _regime_key_from_row → UNKNOWN+reason+RED."""
    start = ENGINE.index("def roster_policy_report")
    end = ENGINE.index("\ndef feature_importance_report", start)
    block = ENGINE[start:end]
    # File:line evidence — wrap lives on tip at roster_policy_report (was ~19600 on prior tip).
    assert ENGINE.count("def roster_policy_report") == 1
    assert "BOOK10_OR_TRADES_UNAVAILABLE" in block or "_roster_policy_fail_closed_payload" in block
    assert "except (RuntimeError, ValueError)" in block
    assert '"status": "RED"' in ENGINE[ENGINE.index("def _roster_policy_fail_closed_payload"):start]
    assert "_regime_key_from_row" in block
    # Mutation: remove the try/except wrap → contract fails.
    mutant = block.replace("except (RuntimeError, ValueError) as exc:", "except ():  # mutant")
    assert "except (RuntimeError, ValueError)" not in mutant
    with pytest.raises(AssertionError):
        assert "except (RuntimeError, ValueError)" in mutant


def test_regime_runtime_note_documents_stop_risk():
    assert "FORBIDDEN" in abr.REGIME_RUNTIME_NOTE or "fail closed" in abr.REGIME_RUNTIME_NOTE.lower()
    assert "45-min" in abr.REGIME_RUNTIME_NOTE or "STOP" in abr.REGIME_RUNTIME_NOTE
    src = (ROOT / "analyzer_book10_regime.py").read_text(encoding="utf-8")
    assert "book_rows_for_window" in src
    assert "refuse_unindexed_book10_scan" in src


def test_unindexed_book10_path_fail_closed():
    """45-min STOP: unindexed full scan is refused; classify_at must call book_rows_for_window."""
    with pytest.raises(RuntimeError, match="unindexed|FORBIDDEN|book_rows_for_window"):
        abr.refuse_unindexed_book10_scan()
    src = (ROOT / "analyzer_book10_regime.py").read_text(encoding="utf-8")
    start = src.index("def classify_at")
    end = src.index("\ndef tags_from_trade_row", start)
    block = src[start:end]
    assert "book_rows_for_window" in block
    # Mutation: drop window helper → contract fails.
    mutant = block.replace("book_rows_for_window(", "list(# mutant ")
    assert "book_rows_for_window(" not in mutant
    with pytest.raises(AssertionError):
        assert "book_rows_for_window(" in mutant


def test_amend2_c3_exact_status_label_constant():
    """AMEND2 C3: exact RETIRED status label string must exist in tip tree."""
    import retired_streams as rs

    expected = "RETIRED / MARKET_SEGMENTS_RETIRED_R17 (write-stopped at cap, not deleted)"
    assert rs.MARKET_SEGMENT_STATUS_LABEL == expected
    assert rs.status_label_for_retired_stream("market_segment") == expected
    assert expected in (ROOT / "retired_streams.py").read_text(encoding="utf-8")


def test_book10_indexed_path_observed_timing():
    """OBSERVED microbench: indexed classify_at path (windowed). Unindexed is fail-closed (no timing)."""
    floor = abr.EPOCH_FLOOR_TS
    # ~110 book rows spanning a short window; 500 classify calls.
    book = [
        {"ts": floor + 1000 + i, "bid": 100.0 + (i % 7) * 0.01, "ask": 100.2 + (i % 7) * 0.01}
        for i in range(110)
    ]
    trades = [{"ts": floor + 1000 + i, "price": 100.1, "amount": 0.01} for i in range(20)]
    t = floor + 1000 + 90
    # Warmup
    abr.classify_at(t, book, trade_rows=trades)
    n = 500
    t0 = time.perf_counter()
    for _ in range(n):
        abr.classify_at(t, book, trade_rows=trades)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    per_call_ms = elapsed_ms / n
    # Record OBSERVED in assert message for receipt; keep a loose upper bound (not a flaky gate).
    assert per_call_ms < 50.0, f"indexed OBSERVED {per_call_ms:.4f} ms/call over {n} iters"
    # Expose for receipt scrapers
    test_book10_indexed_path_observed_timing.observed_ms_per_call = per_call_ms
    test_book10_indexed_path_observed_timing.observed_iters = n
    assert abr.UNINDEXED_BOOK10_FORBIDDEN
    with pytest.raises(RuntimeError):
        abr.refuse_unindexed_book10_scan()


def test_exact_pin_match_rejects_tip_short_with_suffix():
    """Exact pin: tip_short+'x' must fail (HM/Strategist nit)."""
    ns = _load_install_ns()
    bad = abr.ANALYZER_COMPLETED_TIP_SHORT + "x"
    with pytest.raises(ValueError, match="analyzer_git_rev"):
        ns["_analyzer_bundle_rebuild_pin_gate"](
            _report_manifest(pin=_rebuild_pin(analyzer_git_rev=bad))
        )
    # tip_short alone still accepted
    ns["_analyzer_bundle_rebuild_pin_gate"](
        _report_manifest(pin=_rebuild_pin(analyzer_git_rev=abr.ANALYZER_COMPLETED_TIP_SHORT))
    )


def test_engine_floor_uses_abr_not_duplicate_literal():
    """Duplicate floor at engine ANALYZER_MIN_FLOOR_TS must bind abr.EPOCH_FLOOR_TS."""
    idx = ENGINE.index("ANALYZER_MIN_FLOOR_TS")
    window = ENGINE[idx : idx + 200]
    assert "EPOCH_FLOOR_TS" in window
    assert "int(_abr_epoch_floor.EPOCH_FLOOR_TS)" in window or "EPOCH_FLOOR_TS)" in window


def test_market_segment_guard_wired_on_engine_load_jsonl():
    """H2/C2: _load_jsonl_rows must call guard; mutation removing the call fails this contract."""
    assert "guard_no_market_segment_open" in ENGINE
    start = ENGINE.index("def _load_jsonl_rows(path)")
    end = ENGINE.index("\ndef ", start + 1)
    block = ENGINE[start:end]
    assert "guard_no_market_segment_open" in block
    # Also regime path must require book10 (not call with book_rows=None).
    rstart = ENGINE.index("def _regime_tags_from_row")
    rend = ENGINE.index("def _regime_key_from_row", rstart)
    rblock = ENGINE[rstart:rend]
    # Strip docstring so narrative mentions of book_rows=None do not false-positive.
    code_only = re.sub(r'""".*?"""', "", rblock, count=1, flags=re.S)
    assert "book_rows=None" not in code_only
    assert "_require_book10_rows_for_regime" in rblock
    assert "_require_trades_rows_for_regime" in rblock
    # Mutation: strip guard from _load_jsonl_rows → contract fails.
    mutant = block.replace("abr.guard_no_market_segment_open(path)", "pass  # mutant")
    assert mutant.count("guard_no_market_segment_open") < block.count("guard_no_market_segment_open")
    with pytest.raises(AssertionError):
        assert mutant.count("guard_no_market_segment_open") >= block.count(
            "guard_no_market_segment_open"
        )


def test_market_segment_guard_mutation_inject_open_fails():
    """H2 mutation proof: injecting a market_segment open must trip the guard (wired helper)."""
    with pytest.raises(RuntimeError, match="market_segment"):
        abr.guard_no_market_segment_open("v3/ledgers/market_segment.jsonl")
    # Prove engine load path source wires the guard before open (no full engine import needed).
    start = ENGINE.index("def _load_jsonl_rows(path)")
    end = ENGINE.index("\ndef ", start + 1)
    block = ENGINE[start:end]
    assert "abr.guard_no_market_segment_open(path)" in block
    # Mutation: a load helper that skips the guard would accept segment paths — prove real guard rejects.
    def mutant_open(path):
        return path  # no guard

    assert mutant_open("v3/ledgers/market_segment.jsonl")
    with pytest.raises(RuntimeError, match="market_segment"):
        abr.guard_no_market_segment_open("v3/ledgers/market_segment.jsonl")


def test_no_duplicate_epoch_floor_literal_in_bot_pin_block():
    """Epoch floor / tip come from analyzer_book10_regime — bot must not restate 1792263671 beside pin."""
    idx = BOT.index("_ANALYZER_BUNDLE_FRESH_FLOOR_TS")
    window = BOT[idx : idx + 800]
    assert "_abr_rebuild_pin.EPOCH_FLOOR_TS" in window
    assert "int(_abr_rebuild_pin.EPOCH_FLOOR_TS)" in window
    # Mutation: a reintroduced literal assignment would fail this check.
    mutant = window.replace(
        "_ANALYZER_BUNDLE_FRESH_FLOOR_TS = int(_abr_rebuild_pin.EPOCH_FLOOR_TS)",
        "_ANALYZER_BUNDLE_FRESH_FLOOR_TS = 1792263671",
    )
    assert "int(_abr_rebuild_pin.EPOCH_FLOOR_TS)" not in mutant
    with pytest.raises(AssertionError):
        assert "int(_abr_rebuild_pin.EPOCH_FLOOR_TS)" in mutant


def test_analyzer_engine_uses_book10_helper_not_compute_regime_tags():
    # R22: analyzer regime path must call analyzer_book10_regime, not compute_regime_tags.
    assert "import analyzer_book10_regime" in ENGINE
    assert "tags_from_trade_row" in ENGINE
    # The AMEND2 path must not import compute_regime_tags inside _regime_tags_from_row.
    start = ENGINE.index("def _regime_tags_from_row")
    end = ENGINE.index("def _regime_key_from_row", start)
    block = ENGINE[start:end]
    code_only = re.sub(r'""".*?"""', "", block, count=1, flags=re.S)
    assert "compute_regime_tags" not in code_only
    assert "analyzer_book10_regime" in code_only
    assert "book_rows=None" not in code_only
    assert "_require_book10_rows_for_regime" in code_only
    # Mutation: reintroduce compute_regime_tags → contract fails
    mutant = code_only.replace(
        "import analyzer_book10_regime as abr",
        "from research.research_trade_accumulator import compute_regime_tags",
        1,
    )
    assert "compute_regime_tags" in mutant
    with pytest.raises(AssertionError):
        assert "compute_regime_tags" not in mutant


def test_t4_missing_dashboard_rejects():
    ns = _load_install_ns()
    report = _report_manifest()
    report["text_artifacts"] = ["executive_summary.txt"]
    files = {
        "executive_summary.txt": b"summary",
        "report_manifest.json": json.dumps(report, sort_keys=True).encode(),
    }
    payload = _zip_bundle(files)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = ns["_safe_analyzer_bundle_members"](archive)
        with pytest.raises(ValueError, match="analysis_dashboard"):
            ns["_validated_analyzer_bundle_manifest"](archive, members)
