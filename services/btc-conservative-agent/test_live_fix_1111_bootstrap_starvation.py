"""LIVE-FIX-1111: transient worker TIMEOUTs retry fast instead of 180->1800 s backoff."""
import lifecycle_pipeline_runtime as rt


def test_timeout_backoff_is_small_and_bounded():
    assert rt.TIMEOUT_MIN_BACKOFF_SEC <= 15
    assert rt.TIMEOUT_MAX_BACKOFF_SEC <= 60
    assert rt.MIN_BACKOFF_SEC == 180.0  # non-timeout failures unchanged
