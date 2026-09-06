import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

import data_sync_bundle_transport as transport


GEN = "a" * 64


def generation(**updates):
    value = {
        "inventory_generation_id": GEN,
        "inventory_sha256": GEN,
        "ack_eligible": True,
        "source_git_rev": "abc1234",
        "collection_epoch_id": "epoch-test",
        "tile_registry_signature": "tile-test",
    }
    value.update(updates)
    return value


def make_row(root: Path, rel: str, payload: bytes = b"{}\n"):
    if rel.startswith("v3/receipts/emergency_record_idempotency_v1/") and payload == b"{}\n":
        payload = b'{"schema":"emergency_record_idempotency_v1","state":"COMMITTED"}\n'
    target = root.joinpath(*rel.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    stat = target.stat()
    return {
        "path": rel,
        "size": len(payload),
        "physical_size": len(payload),
        "mtime_ns": stat.st_mtime_ns,
        "inode": getattr(stat, "st_ino", 0) or 0,
        "consistency_mode": "strict_generation_v1",
    }


def segment(digit="1"):
    digest = digit * 64
    return f"v3/market_segments/{digest[:2]}/{digest}.json"


def test_noncanonical_aliases_are_not_bundle_members():
    for path in ("./" + segment(), segment().replace("/", "//", 1), segment() + "/"):
        assert transport.is_bundle_eligible_path(path) is False


def test_maximum_payload_package_roundtrips_with_tar_record_padding(tmp_path):
    source = tmp_path / "source"
    row = make_row(source, segment(), b"x" * transport.MAX_PAYLOAD_BYTES)
    descriptor = transport.build_bundle(generation(), [row], source, tmp_path / "out")
    assert descriptor["package_size"] <= transport.MAX_PACKAGE_BYTES
    result = transport.extract_verified_bundle(
        descriptor["package_path"], descriptor, GEN, tmp_path / "staging")
    assert Path(result["members"][0]["staged_path"]).stat().st_size == transport.MAX_PAYLOAD_BYTES


def receipt(digit="2"):
    return f"v3/receipts/emergency_record_idempotency_v1/decision/{digit * 64}.json"


def build(tmp_path, rows):
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    return transport.build_bundle(generation(), rows, source, tmp_path / "out")


def repack(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, payload, kind in entries:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mtime = 0
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "target"
                info.size = 0
                archive.addfile(info)
            else:
                archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def with_package(descriptor, path, raw):
    path.write_bytes(raw)
    return {
        **descriptor,
        "package_sha256": hashlib.sha256(raw).hexdigest(),
        "package_size": len(raw),
    }


def test_deterministic_content_addressed_tar_and_verified_extract(tmp_path):
    source = tmp_path / "source"
    rows = [make_row(source, receipt()), make_row(source, segment(), b'{"x":1}\n')]
    first = build(tmp_path, rows)
    second = build(tmp_path, list(reversed(rows)))
    assert first["package_sha256"] == second["package_sha256"]
    assert Path(first["package_path"]).name == first["package_sha256"] + ".tar"
    result = transport.extract_verified_bundle(
        first["package_path"], first, GEN, tmp_path / "stage"
    )
    assert [row["path"] for row in result["members"]] == sorted(row["path"] for row in rows)
    for row in result["members"]:
        assert Path(row["staged_path"]).read_bytes() == source.joinpath(*row["path"].split("/")).read_bytes()


@pytest.mark.parametrize(
    "rel",
    ["../escape.json", "/absolute.json", "v3/market_segments/aa/x:ads.json", "v3\\bad.json"],
)
def test_rejects_unsafe_or_non_allowlisted_source_paths(tmp_path, rel):
    row = {"path": rel, "size": 0, "mtime_ns": 0, "inode": 0, "consistency_mode": "strict_generation_v1"}
    with pytest.raises(transport.BundleTransportError):
        build(tmp_path, [row])


def test_rejects_hot_ledger_and_non_strict_rows(tmp_path):
    source = tmp_path / "source"
    hot = make_row(source, "v3/ledgers/decision.jsonl")
    with pytest.raises(transport.BundleTransportError, match="not bundle eligible"):
        build(tmp_path, [hot])
    row = make_row(source, segment())
    row["consistency_mode"] = "append_prefix_v1"
    with pytest.raises(transport.BundleTransportError, match="only strict"):
        build(tmp_path, [row])


def test_semantic_committed_validator_still_rejects_prepared_receipt(tmp_path):
    source = tmp_path / "source"
    row = make_row(
        source,
        receipt(),
        b'{"schema":"emergency_record_idempotency_v1","state":"PREPARED"}\n',
    )
    with pytest.raises(transport.BundleTransportError, match="not COMMITTED"):
        transport._validate_committed_receipt(row["path"], (source / row["path"]).read_bytes())


def test_generation_identity_and_ack_eligibility_fail_closed(tmp_path):
    source = tmp_path / "source"
    row = make_row(source, segment())
    with pytest.raises(transport.BundleTransportError, match="generation"):
        transport.build_bundle(generation(inventory_sha256="b" * 64), [row], source, tmp_path / "out")
    with pytest.raises(transport.BundleTransportError, match="not acknowledgement"):
        transport.build_bundle(generation(ack_eligible=False), [row], source, tmp_path / "out")


def test_source_manifest_and_mid_read_mutation_are_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source"
    row = make_row(source, segment(), b"before")
    row["size"] += 1
    with pytest.raises(transport.BundleTransportError, match="differs"):
        build(tmp_path, [row])

    row = make_row(source, segment(), b"before")
    original = transport._read_source_bytes

    def mutate(path, limit):
        payload = original(path, limit)
        path.write_bytes(b"changed-size")
        return payload

    monkeypatch.setattr(transport, "_read_source_bytes", mutate)
    with pytest.raises(transport.BundleTransportError, match="changed while"):
        build(tmp_path, [row])


def test_member_and_payload_budgets_are_hard_capped(tmp_path):
    source = tmp_path / "source"
    rows = [make_row(source, segment("1")), make_row(source, segment("2"))]
    with pytest.raises(transport.BundleTransportError, match="member budget exceeded"):
        transport.build_bundle(generation(), rows, source, tmp_path / "out", max_members=1)
    large = make_row(source, segment("3"), b"12345")
    with pytest.raises(transport.BundleTransportError, match="payload budget exceeded"):
        transport.build_bundle(generation(), [large], source, tmp_path / "out", max_payload_bytes=4)
    with pytest.raises(transport.BundleTransportError, match="hard limit"):
        transport.build_bundle(generation(), [large], source, tmp_path / "out", max_payload_bytes=transport.MAX_PAYLOAD_BYTES + 1)


def test_oversized_manifest_is_rejected_before_source_read(tmp_path, monkeypatch):
    source = tmp_path / "source"
    row = make_row(source, segment(), b"x")
    row["size"] = 5
    called = False

    def forbidden(_path, _limit):
        nonlocal called
        called = True
        raise AssertionError("source must not be read")

    monkeypatch.setattr(transport, "_read_source_bytes", forbidden)
    with pytest.raises(transport.BundleTransportError, match="payload budget"):
        transport.build_bundle(generation(), [row], source, tmp_path / "out", max_payload_bytes=4)
    assert called is False


def test_unbounded_row_iterable_stops_at_member_ceiling(tmp_path):
    consumed = 0

    def rows():
        nonlocal consumed
        while True:
            consumed += 1
            yield {"path": segment("1"), "size": 0, "mtime_ns": 0, "inode": 0, "consistency_mode": "strict_generation_v1"}

    (tmp_path / "source").mkdir()
    with pytest.raises(transport.BundleTransportError):
        transport.build_bundle(generation(), rows(), tmp_path / "source", tmp_path / "out", max_members=1)
    assert consumed == 2


def test_rejects_symlink_component_before_read(tmp_path, monkeypatch):
    source = tmp_path / "source"
    real = tmp_path / "real"
    real.mkdir()
    source.mkdir()
    try:
        (source / "v3").symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    called = False

    def forbidden(_path, _limit):
        nonlocal called
        called = True
        return b""

    monkeypatch.setattr(transport, "_read_source_bytes", forbidden)
    row = {"path": segment(), "size": 0, "mtime_ns": 0, "inode": 0, "consistency_mode": "strict_generation_v1"}
    with pytest.raises(transport.BundleTransportError, match="link or reparse"):
        transport.build_bundle(generation(), [row], source, tmp_path / "out")
    assert called is False


def test_package_generation_descriptor_and_payload_tamper_are_rejected(tmp_path):
    source = tmp_path / "source"
    descriptor = build(tmp_path, [make_row(source, segment(), b"payload")])
    with pytest.raises(transport.BundleTransportError, match="generation mismatch"):
        transport.extract_verified_bundle(descriptor["package_path"], descriptor, "b" * 64, tmp_path / "stage")
    tampered = Path(descriptor["package_path"]).read_bytes() + b"x"
    bad = tmp_path / "tampered.tar"
    bad.write_bytes(tampered)
    with pytest.raises(transport.BundleTransportError, match="size mismatch"):
        transport.extract_verified_bundle(bad, descriptor, GEN, tmp_path / "stage")
    changed_tree = {**descriptor, "members": [{**descriptor["members"][0], "size": 99}]}
    with pytest.raises(transport.BundleTransportError, match="member tree"):
        transport.extract_verified_bundle(descriptor["package_path"], changed_tree, GEN, tmp_path / "stage")


def test_oversized_package_is_rejected_before_read(tmp_path, monkeypatch):
    source = tmp_path / "source"
    descriptor = build(tmp_path, [make_row(source, segment(), b"payload")])
    changed = {**descriptor, "package_size": transport.MAX_PACKAGE_BYTES + 1}
    called = False
    original = transport._read_package_bytes

    def track_read(path, limit):
        nonlocal called
        if path == Path(descriptor["package_path"]):
            called = True
        return original(path, limit)

    monkeypatch.setattr(transport, "_read_package_bytes", track_read)
    with pytest.raises(transport.BundleTransportError, match="hard limit"):
        transport.extract_verified_bundle(descriptor["package_path"], changed, GEN, tmp_path / "stage")
    assert called is False


def test_source_growth_read_is_bounded_and_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source"
    row = make_row(source, segment(), b"x")
    requested = []

    def grow(path, limit):
        requested.append(limit)
        with path.open("ab") as handle:
            handle.write(b"y" * 4096)
        with path.open("rb") as handle:
            return handle.read(limit)

    monkeypatch.setattr(transport, "_read_source_bytes", grow)
    with pytest.raises(transport.BundleTransportError, match="changed while"):
        build(tmp_path, [row])
    assert requested == [row["size"] + 1]


def test_package_growth_read_is_bounded_and_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source"
    descriptor = build(tmp_path, [make_row(source, segment(), b"payload")])
    package = Path(descriptor["package_path"])
    requested = []

    def grow(path, limit):
        requested.append(limit)
        with path.open("ab") as handle:
            handle.write(b"growth" * 1024)
        with path.open("rb") as handle:
            return handle.read(limit)

    monkeypatch.setattr(transport, "_read_package_bytes", grow)
    with pytest.raises(transport.BundleTransportError, match="size mismatch"):
        transport.extract_verified_bundle(package, descriptor, GEN, tmp_path / "stage")
    assert requested == [descriptor["package_size"] + 1]


@pytest.mark.parametrize("field,value", [("size", True), ("mtime_ns", 1.5), ("inode", "3")])
def test_manifest_identity_rejects_bool_fractional_and_string(tmp_path, field, value):
    source = tmp_path / "source"
    row = make_row(source, segment())
    row[field] = value
    with pytest.raises(transport.BundleTransportError, match="must be an integer"):
        build(tmp_path, [row])


def test_windows_reparse_attribute_is_rejected_without_symlink_privilege(tmp_path, monkeypatch):
    source = tmp_path / "source"
    row = make_row(source, segment())
    original = Path.lstat

    class ReparseStat:
        st_file_attributes = 0x400

    def marked(path):
        if path == source / "v3":
            return ReparseStat()
        return original(path)

    monkeypatch.setattr(Path, "lstat", marked)
    with pytest.raises(transport.BundleTransportError, match="link or reparse"):
        build(tmp_path, [row])


def test_extract_rejects_unexpected_duplicate_traversal_ads_and_links(tmp_path):
    source = tmp_path / "source"
    descriptor = build(tmp_path, [make_row(source, segment(), b"payload")])
    package = Path(descriptor["package_path"])
    cases = [
        [(segment(), b"payload", "file"), (segment(), b"payload", "file")],
        [("../escape", b"payload", "file")],
        [(segment() + ":ads", b"payload", "file")],
        [(segment(), b"", "symlink")],
    ]
    for index, entries in enumerate(cases):
        raw = repack(entries)
        candidate = tmp_path / f"bad-{index}.tar"
        changed = with_package(descriptor, candidate, raw)
        with pytest.raises(transport.BundleTransportError):
            transport.extract_verified_bundle(candidate, changed, GEN, tmp_path / f"stage-{index}")
        assert not list((tmp_path / f"stage-{index}").glob("b-*"))


def test_member_hash_tamper_fails_before_any_staging_is_returned(tmp_path):
    source = tmp_path / "source"
    descriptor = build(tmp_path, [make_row(source, segment(), b"payload")])
    raw = repack([(segment(), b"evil!!!", "file")])
    candidate = tmp_path / "member-tamper.tar"
    changed = with_package(descriptor, candidate, raw)
    with pytest.raises(transport.BundleTransportError, match="checksum"):
        transport.extract_verified_bundle(candidate, changed, GEN, tmp_path / "stage")
    assert not list((tmp_path / "stage").glob("b-*"))
