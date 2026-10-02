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
