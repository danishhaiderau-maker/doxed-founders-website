"""Shared R17 gap detector: post-resubscribe trades snapshot completeness (HM #851 N3)."""
import r17_gap_detector as gd


def _snap(start_ms, n=30, step_ms=100):
    return [[1000 + i, start_ms + i * step_ms, 0.01, 100.0] for i in range(n)]


def test_snapshot_starting_after_gap_start_is_incomplete():
    # gap began at 100.0 s; the ~30-print snapshot only reaches back to 101.0 s -> prints may be missing
    assert gd.resubscribe_snapshot_complete(100.0, _snap(101_000)) is False


def test_snapshot_reaching_back_to_gap_start_is_complete():
    assert gd.resubscribe_snapshot_complete(100.0, _snap(99_500)) is True
    assert gd.resubscribe_snapshot_complete(100.0, _snap(100_000)) is True


def test_empty_malformed_or_unbounded_gap_is_incomplete():
    assert gd.resubscribe_snapshot_complete(100.0, []) is False
    assert gd.resubscribe_snapshot_complete(100.0, None) is False
    assert gd.resubscribe_snapshot_complete(100.0, [[1, "x", 0.1, 1.0]]) is False
    assert gd.resubscribe_snapshot_complete(float("-inf"), _snap(0)) is False
