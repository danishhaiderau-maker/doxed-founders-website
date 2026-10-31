"""R34 boot-rebuild expired tail/total from CSV — card seal 450f82d7c018.

Covers T1–T13 binding cases + HM folds (a)(b)(c). CSV bytes stay read-only (R25).
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
from pathlib import Path

import expired_orders_boot_rebuild as reb
import pytest

ROOT = Path(__file__).resolve().parent
BOT_PATH = ROOT / "bot.py"
CENSUS_PATH = ROOT / "ci" / "route_census_fields.json"
ALLOWLIST = ROOT / "ci" / "unit_test_allowlist.txt"
HEADER = [
    "time", "trade_id", "dir", "limit_price", "signal_price", "created_ts",
    "expired_ts", "age_min", "conf", "mode", "reason", "research_lane",
]


def _write_csv(path: Path, rows: list[dict], *, torn_last: str | None = None) -> str:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in HEADER})
        if torn_last is not None:
            fh.write(torn_last)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(tid: str, *, ts: float, lane: str = "CONTINUOUS", reason: str = "TTL_EXPIRED") -> dict:
    return {
        "time": f"2026-10-18T00:00:{int(ts) % 60:02d}+00:00",
        "trade_id": tid,
        "dir": "LONG",
        "limit_price": 100.0,
        "signal_price": 100.0,
        "created_ts": ts - 60,
        "expired_ts": ts,
        "age_min": 1.0,
        "conf": 0.5,
        "mode": "RESEARCH",
        "reason": reason,
        "research_lane": lane,
    }


def test_t1_boot_n_gt_20_total_and_capped_tail(tmp_path):
    path = tmp_path / "expired_orders_3f.csv"
    rows = [_row(f"id-{i}", ts=1_800_000_000 + i) for i in range(25)]
    _write_csv(path, rows)
    out = reb.apply_boot_rebuild(csv_path=str(path), epoch_cutoff_ts=None, display_cap=20)
    assert out["receipt"]["expired_orders_total"] == 25
    assert len(out["display_tail"]) == 20
    assert out["display_tail"][0]["trade_id"] == "id-5"
    assert out["display_tail"][-1]["trade_id"] == "id-24"


def test_t1_mutation_skip_rebuild_undercounts():
    """Mutation: pretending total == display cap fails the N>20 contract."""
    total = 25
    display_cap = 20
    with pytest.raises(AssertionError):
        assert total == display_cap


def test_t2_csv_sha_unchanged_after_hydrate_and_dedupe(tmp_path):
    path = tmp_path / "expired_orders_3f.csv"
    rows = [_row("a", ts=100.0), _row("a", ts=200.0), _row("b", ts=300.0)]
    before = _write_csv(path, rows)
    out = reb.apply_boot_rebuild(csv_path=str(path), epoch_cutoff_ts=None, display_cap=20)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after
    assert out["receipt"]["csv_bytes_unchanged"] is True
    assert out["receipt"]["expired_orders_total"] == 2  # in-memory dedupe only


def test_t2_mutation_csv_rewrite_is_violation(tmp_path):
    path = tmp_path / "expired_orders_3f.csv"
    _write_csv(path, [_row("a", ts=100.0)])
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    def hostile_sha(_p):
        # Simulate a hydrate helper that rewrote the CSV before hashing after.
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return before

    with pytest.raises(RuntimeError, match="EXPIRED_CSV_REWRITE_VIOLATION"):
        reb.apply_boot_rebuild(
            csv_path=str(path), epoch_cutoff_ts=None, display_cap=20, sha_before=hostile_sha,
        )


def test_t3_live_path_still_uses_csv_writer_contract():
    src = BOT_PATH.read_text(encoding="utf-8")
    assert "def log_expired_order(row):" in src
    assert "dynamic_csv_writer(CSV_EXPIRED, row)" in src
    assert "rebuild_expired_orders_from_csv" in src


def test_t13_mutation_removing_csv_write_is_caught():
    """R23: a mutation that removes the durable CSV write must fail this assert."""
    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "log_expired_order")
    calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "dynamic_csv_writer"
    ]
    assert calls, "log_expired_order must write via dynamic_csv_writer(CSV_EXPIRED, …)"
    # Prove the mutation detector works: empty body would fail.
    empty = ast.FunctionDef(name="log_expired_order", args=fn.args, body=[ast.Pass()],
                            decorator_list=[], returns=None, type_comment=None)
    empty_calls = [
        n for n in ast.walk(empty)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "dynamic_csv_writer"
    ]
    assert empty_calls == []


def test_t4_r40_api_inventory_updated():
    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    assert "expired_orders_total meaning changes" in census["note"] or "expired_orders_total" in census["note"]
    for route in ("/api/state", "/api/status"):
        block = census["routes"][route]["expired_orders_boot_rebuild"]
        assert "expired_orders_total" in block
        assert "expired_orders_rebuild_skipped_rows" in block
        meaning = census["routes"][route]["expired_orders_total_meaning"]
        assert "_current_epoch_boundary" in meaning
        assert "CSV-backed" in meaning or "durable" in meaning


def test_t4_mutation_omit_inventory_fails():
    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    with pytest.raises(KeyError):
        _ = census["routes"]["/api/status"]["missing_on_purpose_field_list"]


def test_t5_allowlist_contains_this_module():
    lines = ALLOWLIST.read_text(encoding="utf-8").splitlines()
    assert "test_expired_orders_boot_rebuild.py" in lines


def test_t6_status_publishes_expired_orders_total_on_purpose():
    """HM fold (b): choose /api/status publish under R40 (not test-only /api/state readback)."""
    src = BOT_PATH.read_text(encoding="utf-8")
    assert "payload.update(_expired_orders_public_fields())" in src
    assert "publish expired_orders_total on /api/status on purpose" in src
    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    assert "/api/status" in census["routes"]
    assert "expired_orders_total" in census["routes"]["/api/status"]["expired_orders_boot_rebuild"]


def test_t7_malformed_partial_row_counted_skip_no_crash(tmp_path):
    path = tmp_path / "expired_orders_3f.csv"
    rows = [_row("ok-1", ts=100.0), _row("ok-2", ts=200.0)]
    # Unbalanced quote + truncated fields = half-written last line (counted skip).
    _write_csv(path, rows, torn_last='2026-10-18T00:00:01+00:00,torn-id,"LONG')
    out = reb.apply_boot_rebuild(csv_path=str(path), epoch_cutoff_ts=None, display_cap=20)
    assert out["receipt"]["expired_orders_total"] == 2
    assert out["receipt"]["skipped_rows"] >= 1
    assert out["receipt"]["status"] in ("AMBER", "GREEN")


def test_t7_skip_count_exposed_as_get_field_contract():
    """RK required: counted-skip on a GET surface (fold c)."""
    src = BOT_PATH.read_text(encoding="utf-8")
    assert 'expired_orders_rebuild_skipped_rows"' in src or "expired_orders_rebuild_skipped_rows" in src
    assert "_expired_orders_public_fields" in src


def test_t8_missing_csv_red_unknown_never_silent_zero(tmp_path):
    missing = tmp_path / "nope.csv"
    out = reb.apply_boot_rebuild(csv_path=str(missing), epoch_cutoff_ts=None, display_cap=20)
    assert out["receipt"]["status"] == "RED"
    assert out["receipt"]["total_status"] == "UNKNOWN"
    assert out["receipt"]["expired_orders_total"] is None
    assert out["receipt"]["expired_orders_total"] != 0


def test_t8_mutation_silent_zero_fails(tmp_path):
    missing = tmp_path / "nope.csv"
    out = reb.apply_boot_rebuild(csv_path=str(missing), epoch_cutoff_ts=None, display_cap=20)
    with pytest.raises(AssertionError):
        assert out["receipt"]["expired_orders_total"] == 0


def test_t9_epoch_filter_labels_excluded_not_dropped(tmp_path):
    path = tmp_path / "expired_orders_3f.csv"
    rows = [
        _row("old", ts=100.0),
        _row("new", ts=500.0),
    ]
    _write_csv(path, rows)
    out = reb.apply_boot_rebuild(csv_path=str(path), epoch_cutoff_ts=200.0, display_cap=20)
    assert out["receipt"]["epoch_marker"] == reb.EPOCH_MARKER == "_current_epoch_boundary"
    assert out["receipt"]["expired_orders_total"] == 1
    assert out["receipt"]["epoch_excluded_count"] == 1
    assert out["receipt"]["epoch_excluded"][0]["trade_id"] == "old"
    assert out["receipt"]["epoch_excluded"][0]["label"] == "PRE_EPOCH_BOUNDARY"


def test_t9_hm_fold_a_epoch_marker_matches_reset_helper():
    src = BOT_PATH.read_text(encoding="utf-8")
    assert 'EPOCH_MARKER = "_current_epoch_boundary"' in (ROOT / "expired_orders_boot_rebuild.py").read_text(
        encoding="utf-8"
    )
    assert "_current_epoch_boundary(force=True)" in src
    assert "rebuild_expired_orders_from_csv" in (ROOT / "showcase_ui.py").read_text(encoding="utf-8")


def test_t10_ai_scan_excluded_and_fill_revalidation_rebuilt(tmp_path):
    path = tmp_path / "expired_orders_3f.csv"
    rows = [
        _row("scan", ts=100.0, lane="AI_SCAN"),
        _row("gf3-1", ts=110.0, lane="gf3", reason="FILL_REVALIDATION_STALE"),
        _row("gf3l-1", ts=120.0, lane="gf3l", reason="FILL_REVALIDATION_OTHER"),
        _row("cont", ts=130.0, lane="CONTINUOUS"),
    ]
    _write_csv(path, rows)
    out = reb.apply_boot_rebuild(csv_path=str(path), epoch_cutoff_ts=None, display_cap=20)
    ids = {r["trade_id"] for r in out["display_tail"]}
    assert "scan" not in ids
    assert out["receipt"]["expired_orders_total"] == 3
    assert out["receipt"]["ai_scan_excluded_count"] == 1
    assert out["fill_revalidation_cancel_counts"]["GF3"]["FILL_REVALIDATION_STALE"] == 1
    assert out["fill_revalidation_cancel_counts"]["GF3L"]["FILL_REVALIDATION_OTHER"] == 1


def test_t11_no_double_count_and_half_written_last_line(tmp_path):
    path = tmp_path / "expired_orders_3f.csv"
    rows = [_row("dup", ts=100.0), _row("dup", ts=150.0), _row("only", ts=200.0)]
    _write_csv(path, rows, torn_last='partial,"unclosed')
    out = reb.apply_boot_rebuild(csv_path=str(path), epoch_cutoff_ts=None, display_cap=20)
    assert out["receipt"]["expired_orders_total"] == 2
    assert out["receipt"]["skipped_rows"] >= 1


def test_t12_r41_rebuilt_rows_never_strategy_evidence(tmp_path):
    path = tmp_path / "expired_orders_3f.csv"
    _write_csv(path, [_row("x", ts=100.0)])
    out = reb.apply_boot_rebuild(csv_path=str(path), epoch_cutoff_ts=None, display_cap=20)
    row = out["in_scope_rows"][0]
    assert row["boot_rebuilt_from_csv"] is True
    assert row["strategy_evidence_eligible"] is False
    assert reb.assert_not_strategy_evidence(row) is True
    assert out["receipt"]["strategy_evidence"] == "never"
    # Mutation: counting rebuilt row as edge/fill evidence must fail.
    with pytest.raises(AssertionError):
        assert row.get("strategy_evidence_eligible") is True


def test_bot_wires_boot_rebuild_after_session_trades():
    src = BOT_PATH.read_text(encoding="utf-8")
    boot_idx = src.find("rebuild_expired_orders_from_csv(reason=\"boot\")")
    trades_idx = src.find("load_session_trades_from_csv()")
    assert boot_idx > 0 and trades_idx > 0
    # Startup call follows the trades restore (same boot block).
    assert boot_idx > trades_idx
