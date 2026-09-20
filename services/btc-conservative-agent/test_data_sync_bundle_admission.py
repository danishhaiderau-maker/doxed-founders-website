import json
import os

import pytest

from data_sync_bundle_admission import AdmissionStateError, AdmissionStore, MAX_ATTEMPTS


IDENTITY = {
    "generation_id": "a" * 64,
    "page_index_sha256": "b" * 64,
    "source_git_rev": "e3beb705ebd299ffe76d535aa8e9eeddcdf6206e",
    "collection_epoch_id": "epoch-1",
    "tile_registry_signature": "c" * 64,
}


def test_missing_start_is_persisted_before_publication(tmp_path):
    store = AdmissionStore(tmp_path / "admission.json", "1" * 32, clock=lambda: 100.0)
    assert store.observe(IDENTITY) == {"outcome": "MISSING", "retryable": True, "attempt_count": 0}
    result = store.begin(IDENTITY)
    assert result == {"outcome": "STARTING", "started": True, "attempt_count": 1}
    receipt = json.loads((tmp_path / "admission.json").read_text(encoding="utf-8"))
    assert receipt["identity"] == IDENTITY and receipt["process_incarnation"] == "1" * 32


def test_crash_before_publication_is_historical_not_liveness(tmp_path):
    path = tmp_path / "admission.json"
    first = AdmissionStore(path, "1" * 32, clock=lambda: 100.0)
    first.begin(IDENTITY)
    restarted = AdmissionStore(path, "2" * 32, clock=lambda: 101.0)
    observation = restarted.observe(IDENTITY)
    assert observation["outcome"] == "STARTING" and observation["same_process"] is False
    assert restarted.begin(IDENTITY)["attempt_count"] == 2


def test_explicit_transient_has_persisted_backoff(tmp_path):
    now = [100.0]
    store = AdmissionStore(tmp_path / "admission.json", "1" * 32, clock=lambda: now[0])
    store.begin(IDENTITY)
    state = store.publish(IDENTITY, "MAINTENANCE_DEFERRED")
    assert state["next_retry_unix"] == 105.0
    assert store.begin(IDENTITY)["started"] is False
    now[0] = 105.0
    assert store.begin(IDENTITY) == {"outcome": "STARTING", "started": True, "attempt_count": 2}


def test_persisted_restart_attempt_cap(tmp_path):
    path = tmp_path / "admission.json"
    now = [100.0]
    for attempt in range(1, MAX_ATTEMPTS + 1):
        store = AdmissionStore(path, f"{attempt:x}" * 32, clock=lambda: now[0])
        assert store.begin(IDENTITY)["attempt_count"] == attempt
        store.publish(IDENTITY, "THREAD_START_FAILED")
        now[0] += 61
    last = AdmissionStore(path, "f" * 32, clock=lambda: now[0]).begin(IDENTITY)
    assert last == {"outcome": "ATTEMPT_LIMIT", "started": False,
                    "attempt_count": MAX_ATTEMPTS}


@pytest.mark.parametrize("outcome", ["IDENTITY_REJECTED", "MAINTENANCE_FAILED",
                                      "TERMINAL_FAILURE"])
def test_terminal_outcomes_never_retry(tmp_path, outcome):
    store = AdmissionStore(tmp_path / "admission.json", "1" * 32, clock=lambda: 100.0)
    store.begin(IDENTITY)
    store.publish(IDENTITY, outcome)
    assert store.begin(IDENTITY)["outcome"] == outcome
    assert store.begin(IDENTITY)["started"] is False


def test_bootstrap_rejection_can_reconcile_only_after_external_gate_clears(tmp_path):
    store = AdmissionStore(tmp_path / "admission.json", "1" * 32, clock=lambda: 100.0)
    store.publish(IDENTITY, "BOOTSTRAP_REJECTED", attempt_count=0)
    assert store.begin(IDENTITY) == {"outcome": "STARTING", "started": True,
                                     "attempt_count": 1}


def test_corrupt_or_duplicate_json_fails_closed_without_rewrite(tmp_path):
    path = tmp_path / "admission.json"
    path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
    before = path.read_bytes()
    store = AdmissionStore(path, "1" * 32)
    assert store.observe(IDENTITY) == {"outcome": "CORRUPT_STATE", "retryable": False}
    assert store.begin(IDENTITY) == {"outcome": "CORRUPT_STATE", "started": False}
    assert path.read_bytes() == before


def test_linked_receipt_is_rejected(tmp_path):
    path = tmp_path / "admission.json"
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")
    try:
        os.link(other, path)
    except OSError:
        pytest.skip("hard links unavailable")
    store = AdmissionStore(path, "1" * 32)
    with pytest.raises(AdmissionStateError, match="UNSAFE"):
        store.load()


def test_busy_is_observation_only_and_cannot_be_persisted(tmp_path):
    store = AdmissionStore(tmp_path / "admission.json", "1" * 32)
    store.begin(IDENTITY)
    before = (tmp_path / "admission.json").read_bytes()
    with pytest.raises(AdmissionStateError, match="OUTCOME_INVALID"):
        store.publish(IDENTITY, "SINGLETON_BUSY")
    assert (tmp_path / "admission.json").read_bytes() == before


def test_identity_change_starts_independent_current_generation(tmp_path):
    path = tmp_path / "admission.json"
    store = AdmissionStore(path, "1" * 32)
    store.begin(IDENTITY)
    store.publish(IDENTITY, "COMPLETE")
    changed = {**IDENTITY, "generation_id": "d" * 64}
    assert store.begin(changed) == {"outcome": "STARTING", "started": True,
                                    "attempt_count": 1}
