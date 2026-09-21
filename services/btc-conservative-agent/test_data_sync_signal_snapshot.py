import hashlib

import pytest

import data_sync_bundle_transport as transport
from collector_signal_snapshot import freeze_signal_snapshot
from test_collector_signal_snapshot import event, SIGNAL
from test_data_sync_bundle_transport import generation, make_row, GEN


def source_fixture(tmp_path):
    root = tmp_path / "source"
    ref = freeze_signal_snapshot(event(), data_dir=root, captured_at=SIGNAL + 1)
    path = root / ref["relative_path"]
    return root, ref, make_row(root, ref["relative_path"], path.read_bytes())


def test_signal_snapshots_are_fail_closed_from_transport_packages(tmp_path):
    # Residual purge proved signal_snapshots_v1 alone can exceed the 80 MiB
    # CURRENT soft-cap. Inventory/client/package all exclude the tree; local
    # freeze/load remains available for Fly-side provenance.
    root, ref, row = source_fixture(tmp_path)
    assert transport.is_bundle_eligible_path(ref["relative_path"]) is False
    with pytest.raises(transport.BundleTransportError, match="not bundle eligible"):
        transport.build_bundle(generation(), [row], root, tmp_path / "out")


@pytest.mark.parametrize("path", ["../v3/signal_snapshots_v1/", "v3/signal_snapshots_v1/../",
                                 "v3/signal_snapshots_v1/arbitrary.json", "v3/other/file.json",
                                 "./v3/signal_snapshots_v1/", "v3//signal_snapshots_v1/",
                                 "v3\\signal_snapshots_v1\\"])
def test_noncanonical_and_arbitrary_paths_refused(path):
    assert not transport.is_bundle_eligible_path(path)


def test_canonical_snapshot_digest_path_is_still_package_ineligible():
    digest = hashlib.sha256(b"{}").hexdigest()
    assert transport.is_bundle_eligible_path(
        f"v3/signal_snapshots_v1/{digest}.json"
    ) is False
