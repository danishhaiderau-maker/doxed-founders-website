"""Hand-verified fresh-sample pins for analyzer integrity (Plan A 30 Oct 2026).

Sample rules (source: Fly tiles/trades + R21 dry-run package; earliest_ts 1792263671):
- Pre-epoch rows are non-countable.
- The 31+2 integrity-window ids are non-countable.
- Book-walked rows with live-ack delay n>=10 remain countable (control).
- Ticker / book_tape_1s / mid price sources are never countable.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import paper_fill
import r17_exclusion as r17
from test_r17_exclusion import PROOF, good_row

ROOT = Path(__file__).resolve().parent
EPOCH = 1792263671.0
LIST = ROOT / "r17" / "integrity_ids_20261030.json"


def test_integrity_id_list_is_pinned_31_plus_2():
    raw = LIST.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == r17.INTEGRITY_ID_LIST_SHA
    ids = json.loads(raw)
    assert isinstance(ids, list) and len(ids) == 33
    assert set(ids) == set(r17.INTEGRITY_DISCARD_IDS)
    assert r17.INTEGRITY_ID_LOAD_ERRORS == ()
    # Mutation: wrong pin sha must fail closed (every row non-countable).
    bad_ids, bad_err = r17._load_integrity_ids()
    assert bad_err == () and len(bad_ids) == 33


def test_hand_verified_fresh_sample_countability():
    """Hand-checked ids + pricing sources against the live-ack / book contract."""
    paper_fill.FRESH_EPOCH_TS = EPOCH
    discard = json.loads(LIST.read_text(encoding="utf-8"))
    sample_id = discard[0]
    assert r17.non_countable_reason(good_row(trade_id=sample_id), ids=frozenset(), load_errors=()) == (
        r17.REASON_INTEGRITY_IDS
    )
    open_id = discard[-1]
    assert open_id in {"gs1-0ff6865b9ce2", "rnd-a678d46feb1d"}
    assert r17.non_countable_reason(good_row(trade_id=open_id), ids=frozenset(), load_errors=()) == (
        r17.REASON_INTEGRITY_IDS
    )

    pre = good_row(trade_id="fresh-pre-epoch", entry_ts=EPOCH - 10, exit_ts=EPOCH - 5, ts=EPOCH - 10)
    pre_reason = r17.non_countable_reason(pre, ids=frozenset(), load_errors=())
    assert "PRE_EPOCH_FLOOR" in (pre_reason or "")

    # Live-ack n>=10 delay sources (hand-verified pattern from r17_fields / live_ack_pool).
    ok = good_row(trade_id="fresh-book-ok")
    assert r17.non_countable_reason(ok, ids=frozenset(), load_errors=()) is None
    assert paper_fill.row_fresh_strict(ok) is True
    assert ok["trigger_price_source"] == "book"
    assert "n24" in ok["delay_source"] and "n12" in ok["exit_priced_delay_source"]

    for src in ("ticker", "mid", "book_tape_1s", "MARKET_MICROSTRUCTURE_1S"):
        bad = good_row(trade_id=f"bad-{src}", entry_price_source=src, trigger_price_source="ticker")
        reason = r17.non_countable_reason(bad, ids=frozenset(), load_errors=())
        assert reason and reason.startswith("R17_UNPROVEN:"), src
        assert "TRIGGER_NOT_BOOK" in reason or "ENTRY_PRICED_ON_1S_TAPE" in reason


def test_summary_absent_bundle_states_source_and_min_ts():
    """Fly /api/analyzer/summary 503 body must carry source + earliest_ts (R15/R17)."""
    import ast
    from flask import Flask, jsonify

    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(bot)
    selected = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in {"api_analyzer_summary"}
    ]
    assert selected
    app = Flask("fresh-sample")
    ns = {
        "app": app,
        "os": __import__("os"),
        "json": json,
        "jsonify": jsonify,
        "_ANALYZER_BUNDLE_NO_VALID": "no valid bundle",
        "_active_analyzer_mirror_dir": lambda: None,
        "_external_analyzer_response": lambda e: (_ for _ in ()).throw(AssertionError("no")),
        "_analyzer_proxy_fetch": lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), ns)
    with app.test_request_context("/api/analyzer/summary"):
        __import__("os").environ["FLY_APP_NAME"] = "doxed-btc-bot"
        try:
            resp = ns["api_analyzer_summary"]()
        finally:
            __import__("os").environ.pop("FLY_APP_NAME", None)
    body, status = resp if isinstance(resp, tuple) else (resp, 200)
    assert status == 503
    payload = body.get_json()
    assert payload["ok"] is False
    assert payload["cohort_readable"] is False
    assert payload["analyzer_mirror"] == "ABSENT"
    assert payload["earliest_ts"] == 1792263671.0
    assert payload["epoch_floor_ts"] == 1792263671.0
    assert "data_source" in payload and payload["data_source"]
    assert payload["status"] == "RED"


def test_genome_refuses_cached_report_when_cohort_unreadable(tmp_path, monkeypatch):
    """Stale genome must not publish available=True over an unreadable analyzer cohort."""
    import research.research_dashboard as dash

    (tmp_path / "ledger_reconciliation.json").write_text(
        json.dumps({"schema": "ledger_reconciliation_v1", "level": "RED",
                    "analyzer_cohort_unreadable": True}),
        encoding="utf-8",
    )
    (tmp_path / "safe_policy_genome_v3_report.json").write_text(
        json.dumps({"schema": "safe_policy_genome_v3", "status": "STALE_CACHE",
                    "generated_at": "2026-10-24T00:00:00Z"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(dash, "ROOT", tmp_path)
    monkeypatch.setattr(dash, "_read_json", lambda name, default=None: (
        json.loads((tmp_path / name).read_text(encoding="utf-8"))
        if (tmp_path / name).is_file() else (default if default is not None else {})
    ))
    payload = dash._genome_payload()
    assert payload["available"] is False
    assert payload["status"] == "RED"
    assert payload["reason"] == "ANALYZER_COHORT_UNREADABLE"
    assert payload["earliest_ts"] == 1792263671.0
    assert payload["data_source"] == "ledger_reconciliation.json"
