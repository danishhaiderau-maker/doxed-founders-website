"""Runtime paper-accounting integrity: order identity, CSV quarantine/replay, PnL parity.

bot.py cannot be imported in unit tests (exchange/WS side effects), so the
real functions are extracted from its AST and executed against stubs.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

import pytest

from combo_pathway_config import ACTIVE_TILE_REGISTRY

BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)

FUNCTIONS = (
    "_trade_row_net_pnl_usd",
    "_trade_row_is_forced_close",
    "_derive_lane_pnl_ledger_from_trades",
    "_session_trade_accounting_locked",
    "paper_order_identity_violation",
    "refuse_non_registry_order",
    "_quarantine_overflow_csv_rows",
    "_dynamic_csv_writer_once",
    "_atomic_write_csv_rows",
    "_csv_write_fallback",
    "_replay_csv_write_fallback_locked",
)
CONSTANTS = (
    "STATS_EXCLUDED_EXIT_REASONS",
    "CSV_FALLBACK_JSONL",
    "CSV_OVERFLOW_RESTKEY",
    "CSV_MALFORMED_QUARANTINE_SUFFIX",
    "CSV_FALLBACK_REPLAY_RECEIPT",
    "CSV_FALLBACK_REPLAY_TARGETS",
    "_csv_fallback_pending_targets",
)


def _function_source(name: str) -> str:
    node = next(
        n for n in BOT_TREE.body
        if isinstance(n, ast.FunctionDef) and n.name == name
    )
    source = ast.get_source_segment(BOT_SOURCE, node)
    assert source is not None
    return source


def _load(trades=None, session_filter=None):
    nodes = []
    for node in BOT_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            nodes.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and target.id in CONSTANTS:
                nodes.append(node)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    def append_jsonl(path, payload, **_kwargs):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
        return True

    namespace = {
        "csv": csv, "json": json, "os": os, "hashlib": hashlib, "threading": threading,
        "logger": logging.getLogger("test_paper_accounting"),
        "utc_iso": lambda: "2026-09-30T00:00:00+00:00",
        "EXECUTION_FIX_VERSION": "test",
        "STARTING_BALANCE": 100.0,
        "safe_csv_row": lambda row: {k: ("" if v is None else v) for k, v in row.items()},
        "_safe_append_jsonl": append_jsonl,
        "_normalize_lane_key": lambda lane: str(lane or "").strip().upper(),
        "_trade_row_in_session": session_filter or (lambda row, start: True),
        "trades": trades if trades is not None else [],
        "ACTIVE_TILE_REGISTRY": ACTIVE_TILE_REGISTRY,
    }
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace


def _tile(index: int = 0):
    lane = list(ACTIVE_TILE_REGISTRY)[index]
    return lane, ACTIVE_TILE_REGISTRY[lane]["id_prefix"]


# --- order identity -------------------------------------------------------

def test_registered_tile_with_its_namespace_is_allowed() -> None:
    ns = _load()
    lane, prefix = _tile()
    assert ns["paper_order_identity_violation"](
        {"research_lane": lane, "trade_id": f"{prefix}-abc123"}
    ) is None


@pytest.mark.parametrize("signal, expected", [
    ({"research_lane": "CONTINUOUS", "trade_id": "cont-abc"}, "NON_REGISTRY_ORDER_LANE:CONTINUOUS"),
    ({"trade_id": "cont-abc"}, "NON_REGISTRY_ORDER_LANE:MISSING"),
    ({"research_lane": "AI_SCAN", "trade_id": "x-1"}, "NON_REGISTRY_ORDER_LANE:AI_SCAN"),
])
def test_non_registry_lanes_are_refused(signal, expected) -> None:
    ns = _load()
    assert ns["paper_order_identity_violation"](signal) == expected
    assert ns["refuse_non_registry_order"](signal, "TEST") is True
    assert signal["status"] == "BLOCKED"
    assert signal["outcome"] == "NON_REGISTRY_ORDER_IDENTITY"
    assert signal["order_placed"] is False


def test_tile_lane_with_foreign_trade_id_namespace_is_refused() -> None:
    ns = _load()
    lane, _prefix = _tile(0)
    other_prefix = "fat"
    assert other_prefix != _prefix
    signal = {"research_lane": lane, "trade_id": f"{other_prefix}-abc"}
    assert ns["paper_order_identity_violation"](signal) == f"ORDER_ID_NAMESPACE_MISMATCH:{lane}"
    assert ns["refuse_non_registry_order"]({"research_lane": lane, "trade_id": "cont-1"}, "TEST")


@pytest.mark.parametrize("entry_point, context", [
    ("_place_simulated_limit_order", "SIM_LIMIT_CREATE"),
    ("create_limit_order", "LIMIT_CREATE"),
    ("execute_market_order", "MARKET_EXECUTE"),
])
def test_every_order_entry_point_fails_closed_on_identity(entry_point, context) -> None:
    body = _function_source(entry_point)
    assert f'refuse_non_registry_order(signal, "{context}")' in body
    assert 'get("research_lane", "CONTINUOUS")' not in body
    assert "or RESEARCH_LANE_CONTINUOUS" not in body


# --- CSV malformed-row quarantine ------------------------------------------

def test_schema_expansion_quarantines_overflow_row_instead_of_failing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    ns = _load()
    target = "trades_3factor.csv"
    Path(target).write_text(
        "trade_id,ts,net_pnl_usd\n"
        "fc3-a,1,0.10\n"
        "fc3-bad,2,0.20,EXTRA,CELLS\n"
        "fc3-b,3,-0.05\n",
        encoding="utf-8",
    )
    ns["_dynamic_csv_writer_once"](target, {"trade_id": "fc3-c", "ts": "4", "net_pnl_usd": "0.3", "new_col": "x"})

    with open(target, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["trade_id"] for r in rows] == ["fc3-a", "fc3-b", "fc3-c"]

    sidecar = Path(target + ".malformed_rows.jsonl")
    records = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["reason"] == "ROW_WIDER_THAN_HEADER"
    assert records[0]["cells"] == ["fc3-bad", "2", "0.20", "EXTRA", "CELLS"]
    assert records[0]["csv_line"] == 3


# --- CSV fallback replay ----------------------------------------------------

def test_fallback_replay_is_idempotent_dedupes_and_never_edits_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    ns = _load()
    target = "trades_3factor.csv"
    Path(target).write_text("trade_id,ts,net_pnl_usd\nfc3-a,1,0.10\n", encoding="utf-8")
    err = ValueError("dict contains fields not in fieldnames: None")
    ns["_csv_write_fallback"](target, {"trade_id": "fc3-a", "ts": "1", "net_pnl_usd": "0.10"}, err)
    ns["_csv_write_fallback"](target, {"trade_id": "fat-b", "ts": "2", "net_pnl_usd": "0.20"}, err)
    ns["_csv_write_fallback"]("other.csv", {"trade_id": "z", "ts": "9"}, err)
    assert target in ns["_csv_fallback_pending_targets"]
    evidence_before = Path("csv_write_fallback.jsonl").read_bytes()

    first = ns["_replay_csv_write_fallback_locked"]({target, "other.csv"})
    assert first == {target: {"replayed": 1, "already_present": 1, "remaining": 0, "error": None}}
    assert target not in ns["_csv_fallback_pending_targets"]

    second = ns["_replay_csv_write_fallback_locked"]({target})
    assert second == {}

    with open(target, encoding="utf-8") as handle:
        ids = [r["trade_id"] for r in csv.DictReader(handle)]
    assert ids == ["fc3-a", "fat-b"]
    assert not Path("other.csv").exists()
    assert Path("csv_write_fallback.jsonl").read_bytes() == evidence_before
    receipt = json.loads(Path("csv_write_fallback_replay.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == "csv_write_fallback_replay_v1"
    assert len(receipt["replayed"][target]) == 2


def test_replay_runs_at_startup_before_session_trades_load() -> None:
    replay_at = BOT_SOURCE.index("        replay_csv_write_fallback()\n")
    load_at = BOT_SOURCE.index("    load_session_trades_from_csv()\n", replay_at)
    assert load_at - replay_at < 400
    writer = _function_source("dynamic_csv_writer")
    assert "_replay_csv_write_fallback_locked({filename})" in writer


# --- one accounting function ------------------------------------------------

def test_session_headline_equals_lane_ledger_sum() -> None:
    rows = [
        {"research_lane": "FAMILY_CHANDELIER_3", "net_pnl_usd": "0.25", "dir": "LONG"},
        {"research_lane": "FAMILY_ATR_TARGET_2_5", "net_pnl_usd": "0.12"},
        {"research_lane": "FAMILY_ATR_TARGET_2_5", "net_pnl_usd": 0.0, "pnl": 9.0},
        {"research_lane": "CONTINUOUS", "net_pnl_usd": "-0.08"},
        {"research_lane": "", "net": "0.01"},
        {"research_lane": "FAMILY_ATR_TRAIL", "net_pnl_usd": "5.0", "prior_session": True},
    ]
    ns = _load(trades=rows, session_filter=lambda row, start: not row.get("prior_session"))
    count, realized, ledger = ns["_session_trade_accounting_locked"](1.0)
    assert count == 5
    assert ledger["FAMILY_ATR_TARGET_2_5"]["net_pnl_usd"] == 0.12
    assert ledger["FAMILY_ATR_TARGET_2_5"]["closes"] == 2
    assert "FAMILY_ATR_TRAIL" not in ledger
    lane_sum = round(sum(b["net_pnl_usd"] for b in ledger.values()), 2)
    assert lane_sum == 0.29
    assert realized == 0.30


def test_forced_closes_are_excluded_from_tile_and_strategy_stats() -> None:
    rows = [
        {"research_lane": "FAMILY_COMMITTED_FADE_TAKER_90", "net_pnl_usd": "0.04", "exit_reason": "TIME_EXIT"},
        {"research_lane": "FAMILY_COMMITTED_FADE_TAKER_90", "net_pnl_usd": "0.03", "exit_reason": "ADMIN_MANUAL_CLOSE"},
        {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "net_pnl_usd": "-0.05", "exit_reason": "ADMIN_FORCE_FLAT"},
        {"research_lane": "", "net_pnl_usd": "0.50", "exit_reason": "CIRCUIT_BREAKER_ADMIN_MANUAL"},
    ]
    ns = _load(trades=rows)
    count, realized, ledger = ns["_session_trade_accounting_locked"](1.0)
    assert count == 4  # the Trades table still lists every epoch row
    assert ledger["FAMILY_COMMITTED_FADE_TAKER_90"]["closes"] == 1
    assert ledger["FAMILY_COMMITTED_FADE_TAKER_90"]["net_pnl_usd"] == 0.04
    assert "FAMILY_RANDOM_CONTROL_TAKER_90" not in ledger
    assert realized == 0.04


def test_dashboard_snapshots_use_the_single_session_aggregate() -> None:
    for builder in ("_build_relay_execution_state_snapshot", "_build_api_state_snapshot"):
        body = _function_source(builder)
        assert "_session_trade_accounting_locked(session_start)" in body
        assert 'snapshot["lane_pnl_ledger"] = session_lane_ledger' in body
        assert "_derive_lane_pnl_ledger_from_trades(recent_trades)" not in body
        assert "_derive_lane_pnl_ledger_from_trades(trades_copy)" not in body
    realized = _function_source("_session_realized_pnl_usd")
    assert "_session_trade_accounting_locked(session_start)[1]" in realized
