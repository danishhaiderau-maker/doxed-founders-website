import hashlib
import io
import tarfile

import pytest

import data_sync_bundle_transport as transport
from collector_signal_snapshot import freeze_signal_snapshot, load_signal_snapshot
from test_collector_signal_snapshot import event, SIGNAL
from test_data_sync_bundle_transport import generation, make_row, GEN


def source_fixture(tmp_path):
    root = tmp_path / "source"
    ref = freeze_signal_snapshot(event(), data_dir=root, captured_at=SIGNAL + 1)
    path = root / ref["relative_path"]
    return root, ref, make_row(root, ref["relative_path"], path.read_bytes())


def test_real_snapshot_roundtrip_preserves_content_address(tmp_path):
    root, ref, row = source_fixture(tmp_path)
    descriptor = transport.build_bundle(generation(), [row], root, tmp_path / "out")
    result = transport.extract_verified_bundle(descriptor["package_path"], descriptor, GEN, tmp_path / "stage")
    snapshot = load_signal_snapshot(ref, data_dir=result["staging_path"],
                                    event_id="snapshot-1", epoch_id="epoch-1", signal_ts=SIGNAL)
    assert snapshot["evidence"]["rsi_at_signal"] == 42
    assert descriptor["members"][0]["sha256"] == ref["sha256"]


@pytest.mark.parametrize("path", ["../v3/signal_snapshots_v1/", "v3/signal_snapshots_v1/../",
                                 "v3/signal_snapshots_v1/arbitrary.json", "v3/other/file.json",
                                 "./v3/signal_snapshots_v1/", "v3//signal_snapshots_v1/",
                                 "v3\\signal_snapshots_v1\\"])
def test_noncanonical_and_arbitrary_paths_refused(path):
    assert not transport.is_bundle_eligible_path(path)


def test_filename_digest_mismatch_refused(tmp_path):
    root, ref, row = source_fixture(tmp_path)
    wrong = make_row(root, "v3/signal_snapshots_v1/" + "0" * 64 + ".json",
                     (root / ref["relative_path"]).read_bytes())
    with pytest.raises(transport.BundleTransportError, match="filename checksum"):
        transport.build_bundle(generation(), [wrong], root, tmp_path / "out")


def test_arbitrary_json_with_own_digest_still_refused(tmp_path):
    root = tmp_path / "source"
    payload = b'{}'
    row = make_row(root, "v3/signal_snapshots_v1/" + hashlib.sha256(payload).hexdigest() + ".json", payload)
    with pytest.raises(transport.BundleTransportError, match="schema mismatch"):
        transport.build_bundle(generation(), [row], root, tmp_path / "out")


def test_source_mutation_after_inventory_refused(tmp_path):
    root, ref, row = source_fixture(tmp_path)
    with (root / ref["relative_path"]).open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(transport.BundleTransportError, match="source generation"):
        transport.build_bundle(generation(), [row], root, tmp_path / "out")


def test_snapshot_obeys_package_and_object_limits(tmp_path, monkeypatch):
    root, ref, row = source_fixture(tmp_path)
    with pytest.raises(transport.BundleTransportError, match="payload budget"):
        transport.build_bundle(generation(), [row], root, tmp_path / "out", max_payload_bytes=row["size"] - 1)
    monkeypatch.setattr("collector_signal_snapshot.MAX_SNAPSHOT_BYTES", row["size"] - 1)
    with pytest.raises(transport.BundleTransportError, match="snapshot exceeds hard limit"):
        transport.build_bundle(generation(), [row], root, tmp_path / "out")


def test_extraction_rejects_wrong_filename_even_with_recomputed_package_hashes(tmp_path):
    root, ref, row = source_fixture(tmp_path)
    descriptor = transport.build_bundle(generation(), [row], root, tmp_path / "out")
    payload = (root / ref["relative_path"]).read_bytes()
    wrong = "v3/signal_snapshots_v1/" + "0" * 64 + ".json"
    descriptor["members"][0]["path"] = wrong
    descriptor["member_tree_sha256"] = hashlib.sha256(transport._canonical_json(descriptor["members"])).hexdigest()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w", format=tarfile.USTAR_FORMAT) as handle:
        info = tarfile.TarInfo(wrong)
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
    packaged = archive.getvalue()
    path = tmp_path / "wrong.tar"
    path.write_bytes(packaged)
    descriptor["package_sha256"] = hashlib.sha256(packaged).hexdigest()
    descriptor["package_size"] = len(packaged)
    with pytest.raises(transport.BundleTransportError, match="filename checksum"):
        transport.extract_verified_bundle(path, descriptor, GEN, tmp_path / "stage")
    assert list((tmp_path / "stage").glob("b-*")) == []
