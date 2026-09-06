"""Slow I/O overruns stop before publication, leaving retryable evidence."""
import time

import pytest

import data_sync_bundle_transport as transport
from test_data_sync_bundle_transport import generation, make_row, segment


@pytest.mark.parametrize("stage", ["source", "fsync", "verification"])
def test_slow_io_deadline_leaves_no_published_or_temporary_archive(tmp_path, monkeypatch, stage):
    source, output = tmp_path / "source", tmp_path / "packages"
    row = make_row(source, segment())
    baseline = transport.build_bundle(generation(), [row], source, tmp_path / "baseline")
    original_bytes = (source / row["path"]).read_bytes()
    target_owner, target_name = {
        "source": (transport, "_read_fenced"),
        "fsync": (transport.os, "fsync"),
        "verification": (transport, "_read_package_bytes"),
    }[stage]
    real = getattr(target_owner, target_name)
    deadline = [float("inf")]
    def delayed(*args, **kwargs):
        # Set the deadline at actual I/O entry, then genuinely overrun it.
        deadline[0] = time.monotonic() + 0.005
        result = real(*args, **kwargs)
        time.sleep(0.015)
        return result
    def check():
        if time.monotonic() >= deadline[0]:
            raise transport.BundleTransportError("BUNDLE_BUILD_DEADLINE")
    monkeypatch.setattr(target_owner, target_name, delayed)
    with pytest.raises(transport.BundleTransportError, match="BUNDLE_BUILD_DEADLINE"):
        transport.build_bundle(generation(), [row], source, output, deadline_check=check)
    assert not output.exists() or list(output.iterdir()) == []
    assert (source / row["path"]).read_bytes() == original_bytes
    monkeypatch.setattr(target_owner, target_name, real)
    retry = transport.build_bundle(generation(), [row], source, output, deadline_check=lambda: None)
    assert retry["package_sha256"] == baseline["package_sha256"]
    assert retry["member_count"] == 1
