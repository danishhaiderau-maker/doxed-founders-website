from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

import pytest

import data_sync_bundle_maintenance as mod
from data_sync_bundle_download_pins import DownloadProtection
from data_sync_bundle_worker import STATE_SCHEMA, _singleton_lease

IDS = [char * 64 for char in "abcde"]


@pytest.fixture
def setup(tmp_path):
    source, output, pins, receipts = [tmp_path / name for name in ("source", "output", "pins", "receipts")]
    for root in (source, output, pins, receipts): root.mkdir()
    (source / "raw-evidence.json").write_bytes(b"never remove")
    for generation in IDS[:4]:
        folder = output / ("g-" + generation[:16])
        (folder / "packages").mkdir(parents=True)
        (folder / "descriptors").mkdir()
        package = b"exact derivative " + generation.encode()
        digest = hashlib.sha256(package).hexdigest()
        descriptor = b"{}"
        descriptor_rel = "descriptors/d-" + digest[:20] + ".json"
        (folder / "packages" / (digest + ".tar")).write_bytes(package)
        (folder / descriptor_rel).write_bytes(descriptor)
        state = {"schema": STATE_SCHEMA, "generation": {"inventory_generation_id": generation,
            "inventory_sha256": generation, "source_git_rev": "same-source", "collection_epoch_id": "epoch",
            "tile_registry_signature": "tile"}, "package_index": [{"package_sha256": digest,
            "descriptor_sha256": hashlib.sha256(descriptor).hexdigest(), "descriptor_path": descriptor_rel,
            "member_count": 1, "payload_bytes": len(package)}]}
        (folder / "bundle-worker-state.json").write_text(json.dumps(state))
    current = {"inventory_generation_id": IDS[3], "source_git_rev": "same-source",
               "collection_epoch_id": "epoch", "tile_registry_signature": "tile"}
    protected, reserved = set(), set()
    @contextmanager
    def boundary(candidate):
        # Nonblocking reservation: no inventory mutex remains held while yielding.
        with _singleton_lease(output / ".bundle-worker.lease"):
            pass
        reserved.add(candidate)
        try:
            yield {"current_identity": dict(current), "protected_generations": frozenset(protected)}
        finally:
            reserved.remove(candidate)
    args = dict(source_root=source, output_root=output, pin_root=pins, receipt_root=receipts,
                current_identity=current, target_generation=IDS[4], protection_boundary=boundary)
    return args, protected, reserved


def test_cap_pass_retires_one_exact_generation_and_finalizes_fence(setup, monkeypatch):
    args, _, reserved = setup
    original = mod.retire_derivative_generation
    def observe(*a, **kw):
        assert a[2] in reserved
        return original(*a, **kw)  # Acquires lease independently: no nested owner.
    monkeypatch.setattr(mod, "retire_derivative_generation", observe)
    result = mod.maintain_capacity(**args)
    assert result["status"] == "ADMITTED" and result["retired_generation"] == IDS[0]
    assert result["raw_source_deleted"] is False
    assert (args["source_root"] / "raw-evidence.json").read_bytes() == b"never remove"
    assert len(list(args["output_root"].glob("g-*"))) == 3
    assert not list(args["pin_root"].iterdir())  # COMPLETE-proof finalization, not TTL.
    receipts = list(args["receipt_root"].glob("r-*.json"))
    assert len(receipts) == 1 and json.loads(receipts[0].read_text())["status"] == "COMPLETE"
    assert not reserved


def test_retained_and_current_generations_never_fenced_or_deleted(setup):
    args, protected, _ = setup
    protected.update(IDS[:3])
    result = mod.maintain_capacity(**args)
    assert result["reason"] == "ALL_GENERATIONS_PROTECTED"
    assert len(list(args["output_root"].glob("g-*"))) == 4
    assert not list(args["pin_root"].iterdir()) and not list(args["receipt_root"].iterdir())


def test_active_first_candidate_does_not_starve_later_idle_generation(setup):
    args, _, _ = setup
    owner = DownloadProtection(args["pin_root"], args["output_root"] / ".bundle-worker.lease")
    owner.pin(IDS[0], "f" * 64)
    result = mod.maintain_capacity(**args)
    assert result["status"] == "ADMITTED" and result["retired_generation"] == IDS[1]
    pin = json.loads((args["pin_root"] / (IDS[0] + ".json")).read_text())
    assert pin["fence"] is None
    assert (args["output_root"] / ("g-" + IDS[0][:16])).exists()
    owner.release(IDS[0], "f" * 64)
    assert mod.maintain_capacity(**args)["status"] == "ADMITTED"


def test_no_new_maintenance_when_admission_already_available(setup):
    args, _, _ = setup
    args["target_generation"] = IDS[3]
    def forbidden(*a): pytest.fail("unneeded maintenance")
    args["protection_boundary"] = forbidden
    result = mod.maintain_capacity(**args)
    assert result["status"] == "ADMITTED" and result["cleanup_performed"] is False
    assert not list(args["receipt_root"].iterdir()) and not list(args["pin_root"].iterdir())


def test_interrupted_artifact_deletion_resumes_saved_digest_and_token(setup, monkeypatch):
    args, _, _ = setup
    original = Path.unlink
    calls = [0]
    def fail_second(path, *a, **kw):
        if args["output_root"] in path.parents:
            calls[0] += 1
            if calls[0] == 2: raise OSError("interrupted")
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(OSError): mod.maintain_capacity(**args)
    intent = (args["receipt_root"] / "active-maintenance.json").read_bytes()
    monkeypatch.setattr(Path, "unlink", original)
    result = mod.maintain_capacity(**args)
    assert result["status"] == "ADMITTED"
    final = json.loads((args["receipt_root"] / "active-maintenance.json").read_text())
    assert final["fence_token"] == json.loads(intent)["fence_token"] and final["complete"] is True


def test_lost_response_after_fence_removal_recovers_without_new_cap_failure(setup, monkeypatch):
    args, _, _ = setup
    original = mod._atomic_json
    def fail_complete(path, value):
        if value.get("complete") is True: raise OSError("lost completion")
        return original(path, value)
    monkeypatch.setattr(mod, "_atomic_json", fail_complete)
    with pytest.raises(OSError): mod.maintain_capacity(**args)
    assert len(list(args["output_root"].glob("g-*"))) == 3 and not list(args["pin_root"].iterdir())
    monkeypatch.setattr(mod, "_atomic_json", original)
    assert mod.maintain_capacity(**args)["status"] == "ADMITTED"


def test_changed_retention_identity_blocks_without_fence(setup):
    args, _, _ = setup
    @contextmanager
    def changed(_):
        yield {"current_identity": {**args["current_identity"], "collection_epoch_id": "other"},
               "protected_generations": set()}
    args["protection_boundary"] = changed
    with pytest.raises(ValueError, match="PROTECTION_IDENTITY_CHANGED"):
        mod.maintain_capacity(**args)
    assert not list(args["pin_root"].iterdir())


def test_tampered_source_state_after_intent_is_not_relabelled(setup, monkeypatch):
    args, _, _ = setup
    original = mod.retire_derivative_generation
    def fail(*a, **kw): raise OSError("interrupted before retirement")
    monkeypatch.setattr(mod, "retire_derivative_generation", fail)
    with pytest.raises(OSError): mod.maintain_capacity(**args)
    monkeypatch.setattr(mod, "retire_derivative_generation", original)
    path = args["output_root"] / ("g-" + IDS[0][:16]) / "bundle-worker-state.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="STATE_CHANGED"):
        mod.maintain_capacity(**args)
    assert path.exists()


def test_reader_winning_post_intent_race_is_not_fenced_and_next_candidate_used(setup, monkeypatch):
    args, _, _ = setup
    original = DownloadProtection.fence_if_idle_unprotected
    injected = []
    def race(self, generation, **kwargs):
        if generation == IDS[0] and not injected:
            self.pin(generation, "f" * 64)
            injected.append(True)
        return original(self, generation, **kwargs)
    monkeypatch.setattr(DownloadProtection, "fence_if_idle_unprotected", race)
    result = mod.maintain_capacity(**args)
    assert result["retired_generation"] == IDS[1]
    state = json.loads((args["pin_root"] / (IDS[0] + ".json")).read_text())
    assert state["fence"] is None


def test_total_byte_budget_failure_never_triggers_retirement(setup, monkeypatch):
    args, _, _ = setup
    def reject(*a, **kw): raise ValueError("BUNDLE_DERIVATIVE_TOTAL_BUDGET")
    monkeypatch.setattr(mod, "check_derivative_admission", reject)
    with pytest.raises(ValueError, match="TOTAL_BUDGET"):
        mod.maintain_capacity(**args)
    assert not list(args["receipt_root"].iterdir()) and not list(args["pin_root"].iterdir())


def test_active_chunk_lease_prevents_any_retirement(setup):
    args, _, _ = setup
    owner = DownloadProtection(args["pin_root"], args["output_root"] / ".bundle-worker.lease")
    owner.pin(IDS[0], "f" * 64)
    with owner.read_chunk(IDS[0], "f" * 64):
        with pytest.raises(Exception, match="LEASE_HELD"):
            mod.maintain_capacity(**args)
    assert len(list(args["output_root"].glob("g-*"))) == 4


def test_completed_operation_allows_new_current_identity_on_later_noop(setup):
    args, _, _ = setup
    assert mod.maintain_capacity(**args)["status"] == "ADMITTED"
    args["current_identity"] = {**args["current_identity"], "collection_epoch_id": "new-epoch"}
    assert mod.maintain_capacity(**args)["status"] == "ADMITTED"


def test_orphan_artifact_and_tampered_intent_fail_closed(setup):
    args, _, _ = setup
    (args["output_root"] / "unknown-source-file").write_bytes(b"do not delete")
    with pytest.raises(ValueError): mod.maintain_capacity(**args)
    assert (args["output_root"] / "unknown-source-file").read_bytes() == b"do not delete"
    (args["receipt_root"] / "active-maintenance.json").write_text('{}')
    with pytest.raises(ValueError, match="INTENT_INVALID"):
        mod.maintain_capacity(**args)


def test_deadline_prevents_candidate_fencing(setup):
    args, _, _ = setup
    values = iter([0, 121])
    with pytest.raises(ValueError, match="DEADLINE_OR_CLOCK_INVALID"):
        mod.maintain_capacity(**args, clock=lambda: next(values))
    assert not list(args["receipt_root"].iterdir()) and not list(args["pin_root"].iterdir())
