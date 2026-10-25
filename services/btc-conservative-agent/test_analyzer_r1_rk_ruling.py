"""RK R1 ruling (25 Oct 2026): fail-closed integrity verdict, v3_read_authority, writer fix, book-walk baseline replay."""
import json

import pytest

import research_v3_bridge as bridge
from research import generation_receipt as gr
from research.analyzer_integrity_reconciliation import apply_v3_read_authority_check, v3_read_authority_check
from research.entry_baseline_book_replay import build_entry_baseline_book_replay


# ---- (1a) receipt reads "check" and fails closed -------------------------------------------------------------
def test_verdict_reads_check_key_and_names_failures():
    status, names = gr.integrity_verdict({"report_status": "INVALID", "checks": [
        {"check": "a", "passed": True}, {"check": "policy_identity", "passed": False}],
        "failed_checks": [{"check": "policy_identity", "passed": False}]})
    assert status == "INVALID" and names == ["policy_identity"]


def test_unnamed_check_is_invalid_even_if_report_says_valid():
    status, names = gr.integrity_verdict({"report_status": "VALID", "checks": [{"passed": True}, {"passed": False}]})
    assert status == "INVALID" and names == [gr.INTEGRITY_CHECK_UNNAMED]


def test_unchecked_and_empty_never_valid():
    assert gr.integrity_verdict({"report_status": "VALID", "checks": [{"check": "x", "passed": None}]}) == (
        "INVALID", ["INTEGRITY_CHECK_UNCHECKED:x"])
    assert gr.integrity_verdict({"report_status": "VALID", "checks": []})[0] == "INVALID"
    assert gr.integrity_verdict({"report_status": "VALID", "checks": [{"check": "x", "passed": True}]}) == ("VALID", [])


# ---- (2) v3_read_authority fails closed as a named check -----------------------------------------------------
def test_v3_read_authority_check_and_apply(tmp_path):
    assert v3_read_authority_check({})["passed"] is True
    path = tmp_path / "analyzer_integrity_report.json"
    path.write_text(json.dumps({"report_status": "VALID", "checks": [{"check": "a", "passed": True}]}))
    out = apply_v3_read_authority_check(path, {"decision": "RuntimeError: RETIRED_IDENTITY_TILE_CONFIG_SIGNATURE_DIFFERS"})
    assert out["report_status"] == "INVALID"
    assert [c["check"] for c in out["failed_checks"]] == ["v3_read_authority"]
    assert gr.integrity_verdict(out) == ("INVALID", ["v3_read_authority"])


def test_engine_records_refusal_instead_of_silent_empty():
    src = open("analyzer_research_engine_v62.py", encoding="utf-8").read()
    assert 'V3_READ_AUTHORITY_FAILURES[name] = f"{type(exc).__name__}: {exc}"' in src
    assert "checks.append(v3_read_authority_check(V3_READ_AUTHORITY_FAILURES))" in src


# ---- (1b) writer fix: restart reconciler never mints a NO_ORDER under a divergent signature -------------------
def _store(tmp_path, appended):
    class Store:
        def __init__(self, *a, **k): pass
        def ledger_path(self, name): return tmp_path / (name + ".jsonl")
        def append(self, name, row):
            appended.append(row)
            return {"written": True}
    return Store


def test_restart_reconciler_skips_identity_divergent_episode(tmp_path, monkeypatch):
    bridge._OVERDUE_INDEX_CACHE.clear()
    appended = []
    monkeypatch.setattr(bridge, "V3EvidenceStore", _store(tmp_path, appended))
    monkeypatch.setattr(bridge, "_committed_receipt_bytes_durable", lambda *a: False)
    monkeypatch.setattr(bridge, "_reconcile_record_id", lambda s, e, p, l: f"lifecycle:{e}:{p}:{l}")
    rows = {"decision": [{"epoch_id": "e", "episode_id": ep, "policy_signature": "paper-policy-decision",
                          "research_lane": "L", "order_intent_expected": True, "resolution_deadline_ts": 1,
                          "record_id": f"decision:{ep}"} for ep in ("div", "lost")],
            "order_intent": [{"epoch_id": "e", "episode_id": "div", "policy_signature": "paper-policy-order",
                              "research_lane": "L"}]}
    for name, items in rows.items():
        (tmp_path / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in items))
    out = bridge.reconcile_overdue_expected_order_decisions(epoch_id="e", data_dir=str(tmp_path), observed_ts=5)
    assert out["identity_divergent"] == 1 and out["reconciled"] == 1
    assert [r["episode_id"] for r in appended] == ["lost"]
    bridge._OVERDUE_INDEX_CACHE.clear()


# ---- (3) entry baseline on book_limit_entry ------------------------------------------------------------------
class _Window:
    def book_at(self, t):
        return None
    def trades_between(self, a, b):
        return []
    def gap_clause_at(self, ts):
        return 0


class _Tape:
    def __init__(self, covered=True):
        self.covered, self.windows = covered, []
    def coverage(self, t0, t1):
        return {"covered": self.covered, "reason": None if self.covered else "BOOK_ROWS_DO_NOT_COVER_WINDOW"}
    def window(self, t0, t1):
        self.windows.append((t0, t1))
        return _Window()


def _opp(epoch="ep1", **kw):
    row = {"epoch_id": epoch, "episode_id": "episode-1", "opportunity_id": "opp-1", "trade_id": "t-1",
           "direction": "LONG", "requested_qty": 0.01, "latency_sec": 0.4, "symbol": "tBTCF0:USTF0",
           "baseline_schedules": {"MARKET_ENTRY_AT_SIGNAL": {"schedule": [
               {"bucket_id": "b0", "start_ts": 100, "end_ts": 101, "limit_price": 50000.0}]}}}
    row.update(kw)
    return row


def test_book_replay_hard_rejects_other_epoch_and_lists_non_book_rows():
    tape = _Tape(covered=False)
    rep = build_entry_baseline_book_replay("/nonexistent", {"epoch_id": "ep1"}, provenance={"epoch_id": "ep1"},
        opportunities=[_opp(), _opp(epoch="old"), _opp(latency_sec=None, episode_id="episode-2")],
        tape_loader=lambda d, w: tape)
    assert rep["helper"] == "book_limit_entry" and rep["epoch_id"] == "ep1"
    assert rep["other_epoch_rejected_count"] == 1
    assert rep["other_epoch_rejected"][0]["reason"] == "OTHER_EPOCH_HARD_REJECTED"
    assert rep["non_book_reason_counts"] == {"BOOK_ROWS_DO_NOT_COVER_WINDOW": 1, "MEASURED_DELAY_MISSING": 1}
    assert {r["trade_id"] for r in rep["non_book_rows"]} == {"t-1"}
    assert rep["results"] == []


def test_book_replay_calls_book_limit_entry_with_measured_delay(monkeypatch):
    import book_limit_entry as ble
    seen = {}

    def fake(direction, qty, schedule, book_at, **kw):
        seen.update(kw, direction=direction, qty=qty)
        return {"supported": True, "final_classification": "FULL_FILL", "fill_price": 50001.0,
                "entry_book_age_ms": 250, "delay_s": kw["delay_s"], "filled_qty": qty}
    monkeypatch.setattr(ble, "book_limit_entry", fake)
    rep = build_entry_baseline_book_replay("/nonexistent", {"epoch_id": "ep1"}, provenance={"epoch_id": "ep1"},
        opportunities=[_opp()], tape_loader=lambda d, w: _Tape())
    assert seen["delay_s"] == 0.4 and seen["qty"] == 0.01 and seen["delay_source"] == "measured_latency_sec"
    (res,) = rep["results"]
    assert res["outcome_state"] == "FULL_FILL" and res["price_age_ms"] == 250 and res["epoch_id"] == "ep1"
    assert rep["summaries"]["MARKET_ENTRY_AT_SIGNAL"] == {
        "opportunities": 1, "full_fills": 1, "partial_fills": 0, "no_fills": 0, "non_book": 0}


def test_book_replay_requires_epoch():
    with pytest.raises(ValueError, match="EPOCH_MISSING"):
        build_entry_baseline_book_replay("/x", {}, opportunities=[])


# ---- (4) evidence coverage honest interim error ---------------------------------------------------------------
def test_evidence_coverage_interim_error_names_retired_source():
    src = open("analyzer_research_engine_v62.py", encoding="utf-8").read()
    assert "CONSERVATIVE_RESULTS_SOURCE_RETIRED_R17" in src


def test_book_replay_health_is_native_to_the_new_layout():
    from research.entry_baseline_book_replay import book_replay_health
    rep = {"schema": "entry_baseline_book_replay_v1", "non_book_reason_counts": {"NO_BOOK": 1}, "summaries": {
        "A": {"opportunities": 2, "full_fills": 1, "partial_fills": 0, "no_fills": 0, "non_book": 1}}}
    h = book_replay_health(rep)
    assert (h["status"], h["fills"], h["book_priced"], h["non_book"]) == ("OK", 1, 1, 1)
    rep["summaries"]["B"] = {"opportunities": 1, "full_fills": 0, "partial_fills": 0, "no_fills": 1, "non_book": 0}
    assert book_replay_health(rep)["degraded_baselines"] == ["B"]
    assert book_replay_health({"summaries": {}})["reason_code"] == "BASELINE_REPLAY_SCHEMA_INVALID"


def test_dashboard_research_design_ships_book_replay_summaries_not_rows(monkeypatch):
    from research import research_dashboard as dashboard
    monkeypatch.setattr(dashboard, "_API_RESPONSE_CACHE", {})
    rows = [{"pad": "x" * 1000} for _ in range(50)]
    def report(name):
        value = ({"schema": "entry_baseline_book_replay_v1", "same_opportunity_count": 50, "results": rows,
                  "non_book_rows": rows, "summaries": {"A": {"opportunities": 100, "full_fills": 50,
                  "partial_fills": 0, "no_fills": 0, "non_book": 50}}}
                 if name == "entry_baseline_replay_report.json" else {})
        return value, {"manifest": {"generation_id": "t", "generated_at": "2026-10-03T00:00:00+00:00"}}
    monkeypatch.setattr(dashboard, "_declared_atomic_generation_report", report)
    monkeypatch.setattr(dashboard, "_generation_freshness_meta", lambda *_: {"current": True})
    replay = dashboard.app.test_client().get("/api/research-design").get_json()["entry_baseline_replay"]
    assert "results" not in replay and "non_book_rows" not in replay and "episode_receipts" not in replay
    assert replay["summaries"]["A"]["non_book"] == 50
    assert replay["rows_url"] == "/api/report/entry_baseline_replay_report.json"
