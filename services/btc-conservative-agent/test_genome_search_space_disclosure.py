import gzip
import json

from research import research_dashboard as dashboard


def _report(generated_at, evaluated, variants=70, truncated=True):
    return {"generated_at": generated_at, "candidate_screen": {
        "unique_policies_evaluated": evaluated, "protection_variants": variants, "input_events": 120,
        "input_window": {"events_eligible": 2792, "events_selected": 150, "truncated": truncated}}}


def _history(tmp_path, monkeypatch, reports):
    root = tmp_path / "report-history"
    for i, report in enumerate(reports):
        snap = root / f"20261002T{10 + i:02d}0000Z_gen{i}"
        snap.mkdir(parents=True)
        (snap / "manifest.json").write_text("{}")
        raw = json.dumps(report).encode()
        if i % 2:
            (snap / f"{dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE}.gz").write_bytes(gzip.compress(raw))
        else:
            (snap / dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE).write_bytes(raw)
    monkeypatch.setenv("DOXXED_REPORT_HISTORY_DIR", str(root))


def test_shrunk_search_names_the_factor_that_fell(tmp_path, monkeypatch):
    current = _report("2026-10-02T14:37:00Z", 280)
    _history(tmp_path, monkeypatch, [_report("2026-10-02T12:30:00Z", 21280), _report("2026-10-02T13:30:00Z", 700),
                                     current])
    out = dashboard._genome_search_space_disclosure(current)
    assert out["status"] == "SHRANK"
    assert out["reasons"] == ["ENTRY_SPECS_FELL", "REPLAY_WINDOW_TRUNCATED"]
    assert (out["current"]["entry_specs"], out["reference_max_recent"]["entry_specs"]) == (4, 304)
    assert "SHRANK from 21280" in out["text"] and "not a narrower protection grid" in out["text"]


def test_protection_grid_shrink_and_stable_search(tmp_path, monkeypatch):
    _history(tmp_path, monkeypatch, [_report("2026-10-02T12:30:00Z", 4 * 70)])
    stable = dashboard._genome_search_space_disclosure(_report("2026-10-02T14:37:00Z", 4 * 70, truncated=False))
    assert (stable["status"], stable["reasons"]) == ("STABLE_OR_UNKNOWN", [])
    assert "SHRANK" not in stable["text"]
    fewer = dashboard._genome_search_space_disclosure(_report("2026-10-02T14:37:00Z", 4 * 10, variants=10))
    assert fewer["reasons"][0] == "PROTECTION_VARIANTS_FELL"


def test_no_history_still_explains_the_search(tmp_path, monkeypatch):
    monkeypatch.setenv("DOXXED_REPORT_HISTORY_DIR", str(tmp_path / "missing"))
    out = dashboard._genome_search_space_disclosure(_report("2026-10-02T14:37:00Z", 280))
    assert out["status"] == "NO_HISTORY"
    assert out["text"].startswith("Search space this generation: 280 policies = 4 entry specs x 70")
