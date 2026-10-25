"""RK 25 Oct (R5/R17): a pre-cutoff or unstamped relay event is never sent, and old ledger rows are refused on load."""
import json

import pytest

import relay_delivery_guard as guard_mod
from relay_delivery_guard import CUTOFF_TS, RelayDeliveryGuard, is_pre_cutoff

ME = "me"
ARM = {"owner_id": ME, "armed": True, "armed_at_ts": CUTOFF_TS - 10 ** 6}
DISARMED = {"owner_id": ME, "armed": False, "armed_at_ts": None}


def _ev(event_id, created):
    row = {"event_id": event_id, "bot_instance_id": ME}
    if created is not ...:
        row["created_at_unix"] = created
    return row


def test_cutoff_is_the_ruled_value():
    assert CUTOFF_TS == 1792263671.0


@pytest.mark.parametrize("created", [..., None, "", "nan-ish", CUTOFF_TS - 1, 1.0, 0, float("nan")])
@pytest.mark.parametrize("mode", [ARM, DISARMED])
def test_pre_cutoff_or_unstamped_event_is_never_sent(tmp_path, created, mode):
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE, clock=lambda: CUTOFF_TS + 100)
    good = _ev("good", CUTOFF_TS + 5)
    bad = _ev("bad", created)
    assert is_pre_cutoff(bad)
    sent = guard.filter_deliverable([bad, good], **mode)
    assert [r["event_id"] for r in sent] == ["good"]
    # never re-recorded: the rejected event leaves no quarantine row behind
    qpath = tmp_path / guard_mod.QUARANTINE_FILE
    assert not qpath.exists() or "bad" not in qpath.read_text()
    assert guard.status()["pre_cutoff_rejected_since_boot"] == 1


def test_event_at_cutoff_is_deliverable(tmp_path):
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE)
    assert guard.filter_deliverable([_ev("edge", CUTOFF_TS)], **DISARMED) == [_ev("edge", CUTOFF_TS)]


def test_observe_excludes_pre_cutoff_from_pending_and_never_quarantines(tmp_path):
    guard = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE, clock=lambda: CUTOFF_TS + 100)
    old = {"event_id": "old", "bot_instance_id": "other", "created_at_unix": CUTOFF_TS - 50}
    observed = guard.observe([old, _ev("none", ...)], **DISARMED,
                             epoch={"epoch_id": "e", "started_at_ts": CUTOFF_TS})
    assert observed["pre_cutoff_rejected_pending"] == 2 and observed["pending_total"] == 0
    assert observed["unquarantined_held_pending"] == 0 and observed["retired_now"] == 0
    assert not (tmp_path / guard_mod.QUARANTINE_FILE).exists()
    assert not (tmp_path / guard_mod.RETIRED_FILE).exists()
    assert guard.arming_block_reason(CUTOFF_TS + 100) is None


def test_pre_cutoff_ledger_rows_are_rejected_on_load(tmp_path):
    qpath = tmp_path / guard_mod.QUARANTINE_FILE
    rpath = tmp_path / guard_mod.RETIRED_FILE
    qpath.write_text("\n".join(json.dumps(r) for r in [
        {"event_id": "q-old", "reason": "STALE_OWNER", "created_at_unix": CUTOFF_TS - 1},
        {"event_id": "q-none", "reason": "STALE_OWNER"},
        {"event_id": "q-new", "reason": "STALE_OWNER", "created_at_unix": CUTOFF_TS + 1},
    ]) + "\n")
    rpath.write_text(json.dumps({"schema": guard_mod.RETIREMENT_SCHEMA, "event_id": "r-old",
                                 "created_at_unix": CUTOFF_TS - 1}) + "\n")
    guard = RelayDeliveryGuard(qpath)
    status = guard.status()
    assert status["quarantined_events_total"] == 1 and status["retired_pre_epoch_total"] == 0
    assert status["pre_cutoff_rejected_on_load"] == 3
    assert guard.retired_event_ids() == frozenset()
    # the post-cutoff quarantine row still holds its event
    assert guard.filter_deliverable([{"event_id": "q-new", "bot_instance_id": ME,
                                      "created_at_unix": CUTOFF_TS + 1}], **DISARMED) == []


def test_clean_start_has_no_pre_cutoff_state(tmp_path):
    status = RelayDeliveryGuard(tmp_path / guard_mod.QUARANTINE_FILE).status()
    assert status["quarantined_events_total"] == 0 and status["retired_pre_epoch_total"] == 0
    assert status["pre_cutoff_rejected_on_load"] == 0 and status["cutoff_ts"] == CUTOFF_TS
