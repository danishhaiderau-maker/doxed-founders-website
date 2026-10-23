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


# ------------------------------------------------------------------ RK C4c: epoch cap and reset clear
EP_A, EP_B = "ce-20261020-epoch-a", "ce-20261024-epoch-b"
WIN_TEMP = r"C:\Users\danis\AppData\Local\Temp"


def _rows(path):
    return RH.read_ledger(str(path)) if path.exists() else []


def test_c4c_history_is_stamped_and_capped_to_the_current_epoch(tmp_path):
    root = tmp_path / "history"
    for i, (gen, ep) in enumerate((("gen-1", EP_A), ("gen-2", EP_A), ("gen-3", EP_B))):
        src = _report_dir(tmp_path, gen)
        out = RH.snapshot_report_set(str(src), str(root), now=NOW + i * 60, epoch_id=ep)
        assert out["status"] == "WRITTEN" and out[RH.EPOCH_FIELD] == ep
    assert out["epoch_cap"]["status"] == "OK" and len(out["epoch_cap"]["deleted_snapshots"]) == 2
    snaps = RH.list_snapshots(root)
    assert [json.loads((s / RH.MANIFEST).read_text(encoding="utf-8"))["generation_id"] for s in snaps] == ["gen-3"]
    for name in RH.OWNED_FILES:
        rows = _rows(root / name)
        assert rows and {r[RH.EPOCH_FIELD] for r in rows} == {EP_B}, name
        assert {r["generation_id"] for r in rows} == {"gen-3"}, name


def test_c4c_unstamped_legacy_history_is_dropped_but_unknown_epoch_never_deletes(tmp_path, monkeypatch):
    root = tmp_path / "history"
    monkeypatch.setattr(RH, "current_epoch_id", lambda epoch_id=None: epoch_id)
    legacy = RH.snapshot_report_set(str(_report_dir(tmp_path, "gen-legacy")), str(root), now=NOW)
    assert legacy["status"] == "WRITTEN" and legacy["epoch_cap"]["status"] == "EPOCH_UNKNOWN"
    assert len(RH.list_snapshots(root)) == 1                     # no epoch known: nothing deleted
    assert RH.cap_to_epoch(str(root), None)["deleted_snapshots"] == []
    out = RH.snapshot_report_set(str(_report_dir(tmp_path, "gen-b")), str(root), now=NOW + 60, epoch_id=EP_B)
    assert out["epoch_cap"]["deleted_snapshots"] and len(RH.list_snapshots(root)) == 1
    assert {r["generation_id"] for r in _rows(root / RH.INDEX)} == {"gen-b"}


def _populated_history_with_neighbours(tmp_path):
    root = tmp_path / "report-history"
    RH.snapshot_report_set(str(_report_dir(tmp_path, "gen-1")), str(root), now=NOW, epoch_id=EP_A)
    (root / (RH.STAGING_PREFIX + "20261024T000000Z_gen-x")).mkdir()
    neighbours = {
        ".puller-backup-20261020T101500/report_manifest.json": "{\"keep\": 1}",
        ".puller-backup-20261020T101500/index.jsonl": "{}\n",
        "gs-export-20261023-153000.csv": "a,b\n1,2\n",
        "gs-export-tmp/part.json": "{}",
        "operator_notes.txt": "mine",
        "20261024T000000Z_not-a-dir.json": "{}",
    }
    for rel, text in neighbours.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root, neighbours


def test_c4c_clear_for_reset_removes_only_owned_entries_and_reads_back(tmp_path):
    root, neighbours = _populated_history_with_neighbours(tmp_path)
    assert RH.clear_for_reset(str(root), confirm="yes")["status"] == "REFUSED"
    assert RH.list_snapshots(root)
    out = RH.clear_for_reset(str(root), confirm=RH.CLEAR_CONFIRM)
    assert out["status"] == "CLEARED" and out["readback_leftover"] == []
    assert set(out["removed"]) >= {RH.INDEX, RH.EVENT_LEDGER, RH.HEALTH_LEDGER,
                                    RH.STAGING_PREFIX + "20261024T000000Z_gen-x"}
    assert not RH.list_snapshots(root) and RH.owned_entries(str(root)) == []
    for rel, text in neighbours.items():                           # every neighbour byte-identical
        assert (root / rel).read_text(encoding="utf-8") == text, rel
    assert {".puller-backup-20261020T101500", "gs-export-20261023-153000.csv", "gs-export-tmp"} <= set(out["untouched"])


def test_c4c_puller_backup_and_gs_export_are_excluded_everywhere(tmp_path):
    # The shared guard: operator .puller-backup-* folders and the GS export temp files are never touched.
    for path, reason in (
            (WIN_TEMP + r"\gs-export-20261023-153000.csv", "GS_EXPORT_TEMP"),
            (WIN_TEMP + r"\gs-export-abc\part-0.json", "GS_EXPORT_TEMP"),
            (WIN_TEMP + r"\anything-else.tmp", "WINDOWS_USER_TEMP"),
            (r"C:\DoxxedCrypto\analysis-archive\.puller-backup-20261020\index.jsonl", "PULLER_BACKUP"),
            (r"C:\DoxxedCrypto\fly-mirror-segments\.puller-backup-1", "PULLER_BACKUP"),
            ("report-history/.puller-backup-x/manifest.json", "PULLER_BACKUP")):
        assert RH.never_touch_reason(path) == reason, path
    assert RH.never_touch_reason(RH.DEFAULT_HISTORY_ROOT) is None
    assert RH.never_touch_reason(r"C:\DoxxedCrypto\analysis-archive\report-history\index.jsonl") is None
    # A history root inside a user Temp folder is refused outright: nothing in it is removed or written.
    temp = tmp_path / "Users" / "danis" / "AppData" / "Local" / "Temp"
    root = temp / "report-history"
    export = temp / "gs-export-20261023-153000.csv"
    export.parent.mkdir(parents=True)
    export.write_text("a,b\n", encoding="utf-8")
    root.mkdir()
    (root / RH.INDEX).write_text("{}\n", encoding="utf-8")
    assert RH.clear_for_reset(str(root), confirm=RH.CLEAR_CONFIRM)["status"] == "REFUSED"
    assert RH.clear_for_reset(WIN_TEMP, confirm=RH.CLEAR_CONFIRM)["status"] == "REFUSED"
    assert RH.cap_to_epoch(str(root), EP_B)["status"] == "REFUSED"
    snap = RH.snapshot_report_set(str(_report_dir(tmp_path, "gen-t")), str(root), now=NOW, epoch_id=EP_B)
    assert snap["status"] == "REFUSED"
    assert (root / RH.INDEX).read_text(encoding="utf-8") == "{}\n"
    assert export.read_text(encoding="utf-8") == "a,b\n"
    assert sorted(p.name for p in root.iterdir()) == [RH.INDEX]
