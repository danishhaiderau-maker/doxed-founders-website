import json

import book_gap_stats as bgs


def _write(p, secs, carried=()):
    p.write_text("".join(json.dumps({"s": s, "b": [[1, 1]], "a": [[2, 1]], **({"carried": True} if s in carried else {})}) + "\n"
                         for s in secs))


def test_gap_stats_count_missing_seconds_gaps_and_carried_separately(tmp_path):
    p = tmp_path / "b.jsonl"
    secs = [s for s in range(1000, 1100) if s not in (1010, 1011, 1012, 1050, 1090, 1091, 1092, 1093, 1094)]
    _write(p, secs, carried={1020, 1021})
    out = bgs.book_gap_stats(str(p), hours=1.0, now=1100.0)
    assert out["status"] == "OK" and out["window_seconds"] == 100 and out["rows"] == len(secs)
    assert out["missing_seconds"] == 9 and out["missing_share"] == 0.09
    assert out["gaps"] == 3 and out["gaps_ge_3s"] == 2 and out["longest_gap_s"] == 5
    assert out["carried_rows"] == 2 and out["carried_share"] == round(2 / len(secs), 5)  # carried is not missing


def test_gap_stats_since_boot_window_and_unavailable_source(tmp_path):
    p = tmp_path / "b.jsonl"
    _write(p, [10, 11, 12, 20, 21])
    out = bgs.book_gap_stats(str(p), hours=24.0, now=30.0, since=11.0)
    assert out["rows"] == 4 and out["missing_seconds"] == 7 and out["longest_gap_s"] == 7
    assert bgs.book_gap_stats(str(tmp_path / "nope.jsonl"))["status"] == "UNAVAILABLE"
    assert bgs.book_gap_stats(str(p), hours=0.0001, now=1e9)["status"] == "NO_ROWS"
