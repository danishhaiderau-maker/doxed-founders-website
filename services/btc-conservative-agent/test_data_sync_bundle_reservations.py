import threading

import pytest

from data_sync_bundle_reservations import ReservationRegistry
from data_sync_bundle_download_pins import DownloadProtection
from data_sync_bundle_maintenance import maintain_capacity
from data_sync_bundle_worker import _singleton_lease
from test_data_sync_bundle_maintenance import setup, IDS


def registry(setup):
    args, protected, _ = setup
    condition = threading.Condition(threading.RLock())
    owner = ReservationRegistry(condition=condition,
        **{key: args[key] for key in ("source_root", "output_root", "pin_root", "receipt_root")},
        current_identity=lambda: dict(args["current_identity"]), protected_generations=lambda: set(protected))
    return args, protected, condition, owner


def test_startup_blocks_publication_until_hydrated(setup):
    _, _, condition, owner = registry(setup)
    with condition:
        assert owner.publication_allowed_locked(IDS[0]) is False
    assert owner.hydrate()["status"] == "READY"
    with condition:
        assert owner.publication_allowed_locked(IDS[0]) is True
    with pytest.raises(ValueError, match="INVENTORY_CONDITION_REQUIRED"):
        owner.publication_allowed_locked(IDS[0])


def test_boundary_releases_mutex_and_preserves_http_lock_order(setup):
    args, _, condition, owner = registry(setup)
    owner.hydrate()
    started, completed = threading.Event(), threading.Event()
    observed = []
    def http():
        try:
            with _singleton_lease(args["output_root"] / ".bundle-worker.lease"):
                started.set()
                with condition:
                    observed.append(owner.publication_allowed_locked(IDS[0]))
                    observed.append(owner.publication_allowed_locked(IDS[1]))
        finally:
            completed.set()
    with owner.protection_boundary(IDS[0]) as snapshot:
        assert not condition._is_owned()
        assert snapshot["current_identity"] == args["current_identity"]
        worker = threading.Thread(target=http)
        worker.start()
        assert started.wait(2) and completed.wait(2)
        worker.join(2)
        assert observed == [False, True]
    with condition:
        assert owner.publication_allowed_locked(IDS[0]) is True


def test_retention_winner_before_boundary_is_preserved(setup):
    _, protected, condition, owner = registry(setup)
    owner.hydrate()
    with condition:
        assert owner.publication_allowed_locked(IDS[0])
        protected.add(IDS[0])
    with owner.protection_boundary(IDS[0]) as snapshot:
        assert IDS[0] in snapshot["protected_generations"]
        with condition:
            assert owner.publication_allowed_locked(IDS[0])  # Never fence retained.


def test_reservation_winner_prevents_concurrent_publication(setup):
    _, protected, condition, owner = registry(setup)
    owner.hydrate()
    done = threading.Event()
    def publisher():
        with condition:
            if owner.publication_allowed_locked(IDS[0]):
                protected.add(IDS[0])
        done.set()
    with owner.protection_boundary(IDS[0]):
        thread = threading.Thread(target=publisher)
        thread.start()
        assert done.wait(2)
        thread.join(2)
        assert IDS[0] not in protected


def test_durable_fence_reserved_after_restart_but_not_global_blacklist(setup):
    args, _, condition, owner = registry(setup)
    pin_owner = DownloadProtection(args["pin_root"], args["output_root"] / ".bundle-worker.lease")
    pin_owner.retirement(IDS[0], fence_token="f" * 64)
    owner.hydrate()
    with condition:
        assert not owner.publication_allowed_locked(IDS[0])
        assert owner.publication_allowed_locked(IDS[1])


def test_outstanding_intent_stays_reserved_after_failed_operation_and_restart(setup, monkeypatch):
    import data_sync_bundle_maintenance as maintenance
    args, _, condition, owner = registry(setup)
    owner.hydrate()
    args["protection_boundary"] = owner.protection_boundary
    def fail(*a, **kw): raise OSError("interrupted after fence")
    monkeypatch.setattr(maintenance, "retire_derivative_generation", fail)
    with pytest.raises(OSError): maintain_capacity(**args)
    with condition:
        assert not owner.publication_allowed_locked(IDS[0])
    restarted = ReservationRegistry(condition=condition,
        **{key: args[key] for key in ("source_root", "output_root", "pin_root", "receipt_root")},
        current_identity=lambda: args["current_identity"], protected_generations=lambda: set())
    restarted.hydrate()
    with condition:
        assert not restarted.publication_allowed_locked(IDS[0])


def test_complete_retirement_releases_reservation_without_permanent_global_deny(setup):
    args, _, condition, owner = registry(setup)
    owner.hydrate()
    args["protection_boundary"] = owner.protection_boundary
    result = maintain_capacity(**args)
    assert result["retired_generation"] == IDS[0]
    with condition:
        assert owner.publication_allowed_locked(IDS[0])
    # This grants only registry admission; API still requires valid retention
    # plus existing generation artifacts. It does not resurrect the derivative.
    assert not (args["output_root"] / ("g-" + IDS[0][:16])).exists()


@pytest.mark.parametrize("artifact", ["intent", "pin", "temp"])
def test_bad_hydration_blocks_all_publication(setup, artifact):
    args, _, condition, owner = registry(setup)
    if artifact == "intent":
        path = args["receipt_root"] / "active-maintenance.json"
    else:
        path = args["pin_root"] / (IDS[0] + (".tmp" if artifact == "temp" else ".json"))
    path.write_text("{}")
    with pytest.raises(ValueError): owner.hydrate()
    with condition:
        assert all(not owner.publication_allowed_locked(g) for g in IDS)


def test_no_inventory_mutex_may_be_held_when_entering_worker_operations(setup):
    _, _, condition, owner = registry(setup)
    with condition:
        with pytest.raises(ValueError, match="HYDRATION_LOCK_ORDER_INVALID"): owner.hydrate()
        with pytest.raises(ValueError, match="BOUNDARY_LOCK_ORDER_INVALID"):
            with owner.protection_boundary(IDS[0]):
                pytest.fail("inverted lock ordering admitted")


def test_pin_unlink_completion_gap_is_reserved_until_exact_intent_finishes(setup, monkeypatch):
    import data_sync_bundle_maintenance as maintenance
    args, _, condition, owner = registry(setup)
    owner.hydrate()
    args["protection_boundary"] = owner.protection_boundary
    original = maintenance._atomic_json
    def fail_completion(path, value):
        if value.get("complete") is True: raise OSError("completion write interrupted")
        return original(path, value)
    monkeypatch.setattr(maintenance, "_atomic_json", fail_completion)
    with pytest.raises(OSError): maintain_capacity(**args)
    assert not (args["pin_root"] / (IDS[0] + ".json")).exists()
    owner.hydrate()  # Restart recovery sees the unfinished intent, not empty pins.
    with condition:
        assert not owner.publication_allowed_locked(IDS[0])
    monkeypatch.setattr(maintenance, "_atomic_json", original)
    assert maintain_capacity(**args)["status"] == "ADMITTED"
    with condition:
        assert owner.publication_allowed_locked(IDS[0])


def test_invalid_snapshot_cannot_install_transient_reservation(setup):
    _, _, condition, owner = registry(setup)
    owner.hydrate()
    owner.identity_provider = lambda: {"inventory_generation_id": "bad"}
    with pytest.raises(ValueError, match="IDENTITY_UNAVAILABLE"):
        with owner.protection_boundary(IDS[0]):
            pytest.fail("bad identity yielded")
    with condition:
        assert owner.publication_allowed_locked(IDS[0]) and not owner.transient
