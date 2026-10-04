"""Self-aware data compatibility: versions per stream, clean-epoch classes, segregation, schema drift, purity."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import data_compat as dc  # noqa: E402
from self_aware import diagnose, server  # noqa: E402
from self_aware.config import CADENCE_SEC, THRESHOLDS, Paths  # noqa: E402

NOW = 1_790_930_000.0
EPOCH = "ce-20261004-v31-clean"
START = NOW - 3600


def _paths(tmp: Path) -> Paths:
    return Paths(home=tmp / "home", chain=tmp / "chain", mirror=tmp / "mirror", mirror_archive=tmp / "am",
                 puller=tmp / "pu", exports=tmp / "ex", archive=tmp / "ar", diagnostics=tmp / "d",
                 analyzer_repo=tmp / "v2c", retention=tmp / "ret")


def _write(path: Path, rows, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _mirror(tmp: Path):
    m = tmp / "mirror"
    _write(m / "fill_quality.jsonl.1", [{"ts": START - 900 + i, "bot_version": "v31-x-v5", "slip": 1.0} for i in range(5)])
    _write(m / "fill_quality.jsonl", [{"ts": START - 100 + i, "bot_version": "v31-x-v6", "slip": 1.0} for i in range(3)])
    _write(m / "execution_funnel.jsonl", [{"ts": START - 50, "stage": "scan", "n": 0}])
    _write(m / "market_microstructure_1s.jsonl", [{"ts": START - 10, "px": 1.0}])
    (tmp / "chain").mkdir(parents=True, exist_ok=True)
    return m


def _run(paths, monkeypatch, budget=10**9):
    monkeypatch.setattr(dc, "current_release", lambda: "v31-x-v6")
    return dc.run(paths, {}, now=NOW, budget=budget)


def test_legacy_versions_are_segregated_without_epoch(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _mirror(tmp_path)
    doc = _run(paths, monkeypatch)
    by = {s["stream"]: s for s in doc["streams"]}
    fq = by["fill_quality.jsonl"]
    assert fq["rows"] == 8 and fq["versions"] == {"bot_version=v31-x-v5": 5, "bot_version=v31-x-v6": 3}
    assert fq["severity"] == "AMBER" and fq["segregated"]["LEGACY_VERSION"]["rows"] == 5
    assert fq["whole_files_incompatible"] == ["fill_quality.jsonl.1"]
    assert by["execution_funnel.jsonl"]["segregated"] == {}
    assert by["market_microstructure_1s.jsonl"]["epoch_independent"] is True
    assert doc["undeclared_streams"] == ["execution_funnel.jsonl"]
    assert doc["coverage"]["complete"] and doc["epoch"]["declared"] is False
    part = json.loads((paths.home / "compat" / "segregated" / "LEGACY_VERSION" / "fill_quality.jsonl.json").read_text())
    assert part["ranges"][0]["file"] == "fill_quality.jsonl.1" and part["bytes"] > 0
    assert "clean_epoch_wipe.py plan --scope laptop" in doc["segregated"]["delete_command"]
    fnd = {f["id"]: f for f in dc.findings(doc, NOW, 7200)}
    assert fnd["data.compat_mixed"]["severity"] == "AMBER"
    assert fnd["data.compat_epoch_purity"]["severity"] == "SKIP"
    assert fnd["data.compat_schema"]["severity"] == "AMBER"  # nothing announced yet


def test_index_is_incremental_and_rescans_truncated_files(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    m = _mirror(tmp_path)
    _run(paths, monkeypatch)
    _write(m / "fill_quality.jsonl", [{"ts": START, "bot_version": "v31-x-v6"}], mode="a")
    doc = _run(paths, monkeypatch)
    assert {s["stream"]: s for s in doc["streams"]}["fill_quality.jsonl"]["rows"] == 9
    _write(m / "fill_quality.jsonl", [{"ts": START, "bot_version": "v31-x-v6"}])
    doc = _run(paths, monkeypatch)
    assert {s["stream"]: s for s in doc["streams"]}["fill_quality.jsonl"]["rows"] == 6


def test_scan_defers_during_analyzer_cycle(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    m = _mirror(tmp_path)
    _run(paths, monkeypatch)
    (tmp_path / "chain" / "segment-analyzer-cycle.status.json").write_text(json.dumps({"phase": "PROMOTION"}))
    _write(m / "fill_quality.jsonl", [{"ts": START, "bot_version": "v31-x-v6"}], mode="a")
    doc = _run(paths, monkeypatch)
    assert doc["scan_deferred_for_analyzer_cycle"] is True
    assert {s["stream"]: s for s in doc["streams"]}["fill_quality.jsonl"]["rows"] == 8


def test_declared_epoch_turns_pre_epoch_rows_red_and_purity_needs_receipt(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    m = _mirror(tmp_path)
    (m / "data_epoch.json").write_text(json.dumps(dc.de.new_manifest(EPOCH, started_at_ts=START)))
    _write(m / "fill_quality.jsonl", [{"ts": START + 5, "data_epoch_id": EPOCH, "bot_version": "v31-x-v6"}], mode="a")
    doc = _run(paths, monkeypatch)
    fq = {s["stream"]: s for s in doc["streams"]}["fill_quality.jsonl"]
    assert fq["severity"] == "RED" and fq["classes"]["PRE_EPOCH"] == 8 and fq["classes"]["CURRENT"] == 1
    assert fq["segregated"] == {"PRE_EPOCH": {"rows": 8, "bytes": fq["segregated"]["PRE_EPOCH"]["bytes"]}}
    assert EPOCH in doc["segregated"]["delete_command"]
    purity = dc.epoch_purity(paths, doc)
    assert purity["severity"] == "RED" and "not wired" in purity["observed"]
    rdir = paths.analyzer_repo / "services" / "btc-conservative-agent" / "canonical-research-data" / "analyzer"
    rdir.mkdir(parents=True)
    receipt = {"generation_id": "g1", "data_epoch": {"epoch_id": EPOCH, "pre_epoch_rows_admitted": 0,
                                                     "pre_epoch_rows_rejected": 8}}
    (rdir / "analyzer_generation_receipt.json").write_text(json.dumps(receipt))
    assert dc.epoch_purity(paths, doc)["severity"] == "GREEN"
    receipt["data_epoch"]["pre_epoch_rows_admitted"] = 2
    (rdir / "analyzer_generation_receipt.json").write_text(json.dumps(receipt))
    purity = dc.epoch_purity(paths, doc)
    fnd = {f["id"]: f for f in dc.findings(doc, NOW, 7200, purity)}
    assert fnd["data.compat_mixed"]["severity"] == "RED" and fnd["data.compat_epoch_purity"]["severity"] == "RED"


def _declared_doc_with_receipt(tmp_path, monkeypatch, data_epoch):
    paths = _paths(tmp_path)
    m = _mirror(tmp_path)
    (m / "data_epoch.json").write_text(json.dumps(dc.de.new_manifest(EPOCH, started_at_ts=START)))
    _write(m / "fill_quality.jsonl", [{"ts": START + 5, "data_epoch_id": EPOCH, "bot_version": "v31-x-v6"}], mode="a")
    doc = _run(paths, monkeypatch)
    rdir = paths.analyzer_repo / "services" / "btc-conservative-agent" / "canonical-research-data" / "analyzer"
    rdir.mkdir(parents=True)
    (rdir / "analyzer_generation_receipt.json").write_text(json.dumps({"generation_id": "g2",
                                                                       "data_epoch": data_epoch}))
    purity = dc.epoch_purity(paths, doc)
    dc.reconcile_retained(doc, purity)
    return doc, purity


def test_retained_pre_epoch_rows_never_opened_unguarded_are_not_mixed(tmp_path, monkeypatch):
    block = {"epoch_id": EPOCH, "pre_epoch_rows_admitted": 0, "pre_epoch_rows_rejected": 8,
             "pre_epoch_rows_retained": 8, "pre_epoch_rows_admitted_by_stream": {},
             "read_monitor": {"active": True, "unguarded_stream_reads": {}}}
    doc, purity = _declared_doc_with_receipt(tmp_path, monkeypatch, block)
    fq = {s["stream"]: s for s in doc["streams"]}["fill_quality.jsonl"]
    assert fq["severity"] != "RED" and fq["retained_pre_epoch_rows"] == 8
    assert not any(p.startswith("pre-epoch/foreign rows in") for p in fq["problems"])
    # execution_funnel.jsonl's single row is pre-epoch too
    assert doc["retained"] == {"proven": True, "generation_id": "g2", "streams": 2, "rows": 9}
    fnd = {f["id"]: f for f in dc.findings(doc, NOW, 7200, purity)}
    assert fnd["data.compat_mixed"]["severity"] == "GREEN"
    assert "2 streams retain 9 pre-epoch rows on disk" in fnd["data.compat_mixed"]["observed"]
    assert fnd["data.compat_epoch_purity"]["severity"] == "GREEN"
    assert "8 retained on disk" in fnd["data.compat_epoch_purity"]["observed"]


def test_any_pre_epoch_row_in_results_stays_red_with_the_offending_reader(tmp_path, monkeypatch):
    block = {"epoch_id": EPOCH, "pre_epoch_rows_admitted": 8, "pre_epoch_rows_rejected": 0,
             "pre_epoch_rows_retained": 8, "pre_epoch_rows_admitted_by_stream": {"fill_quality.jsonl": 8},
             "read_monitor": {"active": True, "unguarded_stream_reads": {
                 "fill_quality.jsonl": {"opens": 1, "pre_epoch_rows": 8, "sites": ["x.py:load:3"]}}}}
    doc, purity = _declared_doc_with_receipt(tmp_path, monkeypatch, block)
    assert purity["severity"] == "RED" and "fill_quality.jsonl 8 via ['x.py:load:3']" in purity["observed"]
    fnd = {f["id"]: f for f in dc.findings(doc, NOW, 7200, purity)}
    assert fnd["data.compat_mixed"]["severity"] == "RED" and fnd["data.compat_epoch_purity"]["severity"] == "RED"


def test_retention_needs_an_active_read_monitor_on_the_declared_epoch(tmp_path, monkeypatch):
    block = {"epoch_id": EPOCH, "pre_epoch_rows_admitted": 0, "pre_epoch_rows_rejected": 8,
             "read_monitor": {"active": False}}
    doc, purity = _declared_doc_with_receipt(tmp_path, monkeypatch, block)
    assert {s["stream"]: s for s in doc["streams"]}["fill_quality.jsonl"]["severity"] == "RED"
    assert doc["retained"]["proven"] is False
    unaccounted = {"epoch_id": EPOCH, "pre_epoch_rows_admitted": None, "error": "OSError: boom"}
    _, purity = _declared_doc_with_receipt(tmp_path / "b", monkeypatch, unaccounted)
    assert purity["severity"] == "RED" and "could not account" in purity["observed"]


def test_unstampable_csv_counts_as_declared_once_every_post_epoch_row_is_epoch_dated(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    m = tmp_path / "mirror"
    (tmp_path / "chain").mkdir(parents=True)
    m.mkdir(parents=True)
    (m / "data_epoch.json").write_text(json.dumps(dc.de.new_manifest(EPOCH, started_at_ts=START)))
    (m / "trend_health.csv").write_text(f"ts,score\n{START + 5},1\n{START + 6},2\n", encoding="utf-8")
    _write(m / "execution_funnel.jsonl", [{"ts": START + 5, "stage": "scan", "data_epoch_id": EPOCH}])
    _write(m / "xvl_shadow_signals.jsonl", [{"ts": START + 5, "side": "LONG"}])
    # Written only before the epoch: nothing to declare until its first post-epoch row.
    (m / "expired_orders_3factor.csv").write_text(f"ts,score\n{START - 50},1\n", encoding="utf-8")
    _write(m / "approved_but_rejected.jsonl", [{"ts": START - 50, "side": "LONG"}])
    doc = _run(paths, monkeypatch)
    by = {s["stream"]: s for s in doc["streams"]}
    assert by["approved_but_rejected.jsonl"]["quiet_this_epoch"] is True
    assert by["expired_orders_3factor.csv"]["version_declared"] is True
    assert by["xvl_shadow_signals.jsonl"]["quiet_this_epoch"] is False
    assert by["trend_health.csv"]["version_declared"] is True
    assert by["execution_funnel.jsonl"]["version_declared"] is True
    assert doc["undeclared_streams"] == ["xvl_shadow_signals.jsonl"]


def test_stale_doc_skips_and_diagnose_registers_check(tmp_path, monkeypatch):
    assert all(f["severity"] == "SKIP" for f in dc.findings(None, NOW, 7200))
    assert CADENCE_SEC["compat"] == 1800 and THRESHOLDS["compat_doc_max_age_sec"] == 7200
    paths = _paths(tmp_path)
    _mirror(tmp_path)
    doc = _run(paths, monkeypatch)
    out = diagnose.check_data_compat({"now": NOW, "data_compat": doc}, None, None)
    assert {f.id for f in out} == {"data.compat_mixed", "data.compat_schema", "data.compat_declared",
                                   "data.compat_epoch_purity"}


def test_endpoint_and_dashboard_panel(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _mirror(tmp_path)
    doc = _run(paths, monkeypatch)
    sent = []

    class _Eng:
        docs = {"compat": doc}

    class _H:
        eng = _Eng()

        def _send(self, code, body, ctype="application/json"):
            sent.append((code, body))

    assert server.ROUTES["/api/selfaware/data/compatibility"] is server.Handler.data_compat
    server.Handler.data_compat(_H(), {"stream": "fill_quality.jsonl"})
    assert sent[-1][0] == 200 and len(sent[-1][1]["streams"]) == 1
    server.Handler.data_compat(_H(), {"severity": "GREEN"})
    assert all(s["severity"] == "GREEN" for s in sent[-1][1]["streams"])
    html = server.render_compat(_Eng())
    assert "copy delete command" in html and "LEGACY_VERSION" in html
    _Eng.docs = {}
    server.Handler.data_compat(_H(), {})
    assert sent[-1][0] == 503

def test_retired_custody_ops_and_read_guarded_streams_do_not_turn_mixed_red(tmp_path, monkeypatch):
    """#420: Fly-retired custody copies are not scanned, ops ledgers never raise severity, read-guarded
    streams with pre-epoch rows are AMBER (every reader rejects them), real evidence still turns RED."""
    paths = _paths(tmp_path)
    m = tmp_path / "mirror"
    (tmp_path / "chain").mkdir(parents=True)
    m.mkdir(parents=True)
    (m / "data_epoch.json").write_text(json.dumps(dc.de.new_manifest(EPOCH, started_at_ts=START)))
    _write(m / "post_exit_replay.jsonl", [{"ts": START - 600 + i, "data_epoch_id": "ce-old"} for i in range(50)])
    _write(m / "execution_funnel.jsonl", [{"ts": START + 5, "stage": "scan", "data_epoch_id": EPOCH}])
    _write(m / "relay_outbox_quarantine.jsonl", [{"created_at_unix": START - 86400, "reason": "STALE_OWNER"}])
    _write(m / "runtime_telemetry_1m.jsonl", [{"ts": START - 60}, {"ts": START + 60, "data_epoch_id": EPOCH}])
    _write(m / "taker_signal_counterfactuals.jsonl", [{"ts": START - 300, "data_epoch_id": "ce-old"},
                                                      {"ts": START + 30, "data_epoch_id": EPOCH}])
    paths.puller.mkdir(parents=True)
    (paths.puller / "state.json").write_text(json.dumps({"tombstoned": {"post_exit_replay.jsonl": 96}}))
    doc = _run(paths, monkeypatch)
    by = {s["stream"]: s for s in doc["streams"]}
    assert "post_exit_replay.jsonl" not in by and doc["retired_custody_files"] == 1
    for ops in ("relay_outbox_quarantine.jsonl", "runtime_telemetry_1m.jsonl"):
        # Only the (unrelated) unannounced-schema note may remain; no epoch-class problem.
        assert by[ops]["non_evidence"] is True and by[ops]["severity"] != "RED"
        assert by[ops]["problems"][0].startswith("ops stream, not analyzer evidence")
        assert not any("undated" in p or "pre-epoch" in p or "without data_epoch_id" in p for p in by[ops]["problems"])
    taker = by["taker_signal_counterfactuals.jsonl"]
    assert taker["severity"] == "AMBER" and taker["read_guarded"] is True and taker["classes"]["FOREIGN_EPOCH"] == 1
    assert "rejected by every analyzer reader" in taker["problems"][0]
    fnd = {f["id"]: f for f in dc.findings(doc, NOW, 7200)}
    assert fnd["data.compat_mixed"]["severity"] != "RED"
    # The same retired file without the custody record is analyzer-visible evidence again -> RED.
    (paths.puller / "state.json").write_text(json.dumps({"tombstoned": {}}))
    doc = _run(paths, monkeypatch)
    by = {s["stream"]: s for s in doc["streams"]}
    assert by["post_exit_replay.jsonl"]["severity"] == "RED"
    assert {f["id"]: f for f in dc.findings(doc, NOW, 7200)}["data.compat_mixed"]["severity"] == "RED"


def test_stamped_writer_legacy_rows_are_declared_not_undeclared(tmp_path, monkeypatch):
    """execution_settings_history: rows from before the writer stamped are legacy, the stream is declared."""
    paths = _paths(tmp_path)
    m = tmp_path / "mirror"
    (tmp_path / "chain").mkdir(parents=True)
    m.mkdir(parents=True)
    (m / "data_epoch.json").write_text(json.dumps(dc.de.new_manifest(EPOCH, started_at_ts=START)))
    _write(m / "execution_settings_history.jsonl", [
        {"ts": START - 900, "epoch": START - 900, "reason": "FRESH_COLLECTION_STARTED", "signature": "gap=0"},
        {"ts": START + 60, "epoch": START + 60, "reason": "FRESH_COLLECTION_STARTED", "signature": "gap=0"},
    ])
    _write(m / "xvl_shadow_signals.jsonl", [{"ts": START + 5, "side": "LONG"}])
    doc = _run(paths, monkeypatch)
    by = {s["stream"]: s for s in doc["streams"]}
    hist = by["execution_settings_history.jsonl"]
    assert hist["version_declared"] is True
    assert hist["legacy_unstamped_rows"] == 2
    assert not any("post-epoch rows without data_epoch_id" in p for p in hist["problems"])
    # Other unstamped writers are still reported.
    assert doc["undeclared_streams"] == ["xvl_shadow_signals.jsonl"]
