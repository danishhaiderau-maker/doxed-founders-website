"""Analyzer report-set history: full snapshots, hardlinked dedupe, bounded retention, append-only ledgers."""
import gzip
import json
import os

from research import report_history as RH

DAY = 86400
NOW = 1790900000.0


def _report_dir(tmp_path, gen, *, big=b"", events=3, health="OK"):
    d = tmp_path / "reports"
    d.mkdir(exist_ok=True)
    (d / "report_manifest.json").write_text(json.dumps({"generation_id": gen, "generated_at": "x"}), encoding="utf-8")
    (d / "small_report.json").write_text(json.dumps({"gen": gen}), encoding="utf-8")
    (d / "static_report.json").write_text(json.dumps({"static": True}), encoding="utf-8")
    if big:
        (d / "entry_baseline_replay_report.json").write_bytes(big)
    (d / "event_study_report.json").write_text(json.dumps({
        "schema": "event_study_report_v1", "generated_ts": NOW, "dataset_epoch": "ep",
        "hypotheses": [{"id": f"H{i}", "spec_hash": f"s{i}", "status": "LOCKBOX_ACCRUING",
                        "lockbox": {"events_counted": i}} for i in range(1, events + 1)]}), encoding="utf-8")
    (d / "data_health_report.json").write_text(json.dumps({
        "status": health, "streams": [{"stream": "bfx_1s", "status": health, "coverage_pct_24h": 98.5}]}),
        encoding="utf-8")
    (d / "notes.txt").write_text("ignored", encoding="utf-8")
    return d


def test_snapshot_writes_full_set_with_manifest_and_verifies(tmp_path):
    big = json.dumps({"rows": ["x" * 100] * 5000}).encode()
    src = _report_dir(tmp_path, "gen-1", big=big)
    root = tmp_path / "history"
    out = RH.snapshot_report_set(str(src), str(root), now=NOW, compress_min_bytes=1024)
    assert out["status"] == "WRITTEN" and out["generation_id"] == "gen-1"
    assert out["files"] == 6                                    # every *.json, not notes.txt
    snap = root / os.path.basename(out["path"])
    manifest = json.loads((snap / RH.MANIFEST).read_text(encoding="utf-8"))
    entry = manifest["files"]["entry_baseline_replay_report.json"]
    assert entry["codec"] in ("gzip", "zstd") and entry["stored_bytes"] < len(big)
    if entry["codec"] == "gzip":
        with gzip.open(snap / entry["stored"], "rb") as fh:
            assert fh.read() == big
    assert manifest["files"]["small_report.json"]["codec"] == "none"
    assert RH.verify_snapshot(str(snap))["ok"]
    assert not list(root.glob(RH.STAGING_PREFIX + "*"))


def test_same_generation_is_idempotent_and_unchanged_files_are_hardlinked(tmp_path):
    src = _report_dir(tmp_path, "gen-1")
    root = tmp_path / "history"
    first = RH.snapshot_report_set(str(src), str(root), now=NOW)
    again = RH.snapshot_report_set(str(src), str(root), now=NOW + 60)
    assert again["status"] == "EXISTS" and len(RH.list_snapshots(root)) == 1
    _report_dir(tmp_path, "gen-2")
    second = RH.snapshot_report_set(str(src), str(root), now=NOW + 120)
    assert second["status"] == "WRITTEN" and len(RH.list_snapshots(root)) == 2
    # report_manifest, small_report, event_study and data_health ledgers' source reports change or not:
    # static_report.json, event_study_report.json and data_health_report.json are byte-identical.
    assert second["linked_files"] >= 3
    a = os.path.join(first["path"], "static_report.json")
    b = os.path.join(second["path"], "static_report.json")
    assert os.path.samefile(a, b)
    assert RH.verify_snapshot(second["path"])["ok"]


def test_retention_enforces_cap_but_keeps_min_generations_and_daily(tmp_path):
    src = _report_dir(tmp_path, "g0", big=os.urandom(4000))
    root = tmp_path / "history"
    for i in range(10):                            # one snapshot per day, each with a fresh 4 KB payload
        (src / "entry_baseline_replay_report.json").write_bytes(os.urandom(4000))
        (src / "report_manifest.json").write_text(json.dumps({"generation_id": f"g{i}"}), encoding="utf-8")
        out = RH.snapshot_report_set(str(src), str(root), now=NOW + i * DAY, keep_bytes_cap=10 ** 9,
                                     keep_min_generations=3, daily_keep_days=0)
        assert out["status"] == "WRITTEN"
    assert len(RH.list_snapshots(root)) == 10
    ret = RH.enforce_retention(str(root), keep_bytes_cap=1, keep_min_generations=3, daily_keep_days=0,
                               now=NOW + 9 * DAY)
    assert [s.name.split("_", 1)[1] for s in RH.list_snapshots(root)] == ["g7", "g8", "g9"]
    assert ret["status"] == "OVER_CAP_PROTECTED" and len(ret["deleted"]) == 7
    # newest-per-day protection inside the daily window
    ret = RH.enforce_retention(str(root), keep_bytes_cap=1, keep_min_generations=0, daily_keep_days=2,
                               now=NOW + 9 * DAY)
    assert [s.name.split("_", 1)[1] for s in RH.list_snapshots(root)] == ["g8", "g9"]


def test_retention_under_cap_deletes_nothing(tmp_path):
    src = _report_dir(tmp_path, "g0")
    root = tmp_path / "history"
    for i in range(5):
        (src / "report_manifest.json").write_text(json.dumps({"generation_id": f"g{i}"}), encoding="utf-8")
        RH.snapshot_report_set(str(src), str(root), now=NOW + i * 3600, keep_min_generations=1)
    assert len(RH.list_snapshots(root)) == 5


def test_ledgers_are_append_only_and_deduped_per_generation(tmp_path):
    src = _report_dir(tmp_path, "gen-1", events=5)
    root = tmp_path / "history"
    RH.snapshot_report_set(str(src), str(root), now=NOW)
    RH.snapshot_report_set(str(src), str(root), now=NOW + 1)           # same generation: no new lines
    events = RH.read_ledger(str(root / RH.EVENT_LEDGER))
    health = RH.read_ledger(str(root / RH.HEALTH_LEDGER))
    assert [e["hypothesis_id"] for e in events] == ["H1", "H2", "H3", "H4", "H5"]
    assert events[4]["lockbox"]["events_counted"] == 5 and len(health) == 1
    assert health[0]["streams"]["bfx_1s"]["coverage_pct_24h"] == 98.5
    before = (root / RH.EVENT_LEDGER).read_bytes()
    with open(root / RH.EVENT_LEDGER, "ab") as fh:                    # simulate a torn write
        fh.write(b'{"generation_id": "torn"')
    _report_dir(tmp_path, "gen-2", events=2, health="WARN")
    RH.snapshot_report_set(str(src), str(root), now=NOW + 60)
    after = (root / RH.EVENT_LEDGER).read_bytes()
    assert after.startswith(before)                                    # history is never rewritten
    events = RH.read_ledger(str(root / RH.EVENT_LEDGER))
    assert [e["generation_id"] for e in events].count("gen-2") == 2
    assert [e["generation_id"] for e in events].count("torn") == 0
    assert RH.read_ledger(str(root / RH.HEALTH_LEDGER))[-1]["status"] == "WARN"


def test_refuses_onedrive_and_missing_dirs_never_raises(tmp_path):
    out = RH.snapshot_report_set(str(tmp_path / "OneDrive" / "reports"), str(tmp_path / "h"))
    assert out["status"] == "REFUSED"
    assert RH.snapshot_report_set(str(tmp_path / "nope"), str(tmp_path / "h"))["status"] == "NO_REPORT_DIR"


def test_engine_snapshots_the_manifest_report_dir_with_its_generation():
    # The published reports/ copy lacks report_manifest.json and part of the set,
    # which produced nogen-* snapshots missing reports.
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyzer_research_engine_v62.py"),
               encoding="utf-8").read()
    start = src.index("snapshot_report_set(", src.index("from research.report_history import snapshot_report_set"))
    call = src[start:src.index(")\n", src.index("generation_id=", start)) + 1]
    assert "os.path.dirname(os.path.abspath(REPORT_MANIFEST_FILE))" in call
    assert '_load_json_report(REPORT_MANIFEST_FILE) or {}).get("generation_id")' in call
    assert "REPORTS_DIR" not in call
