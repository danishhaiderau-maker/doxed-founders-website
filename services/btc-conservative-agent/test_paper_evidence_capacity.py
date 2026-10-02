import copy
import hashlib
import pytest
from paper_evidence_capacity import (FIELD, QUEUE, CapacityError, initialize_legacy,
    reserve_entry, enqueue_reserved, acknowledge, release_cancelled, validate)


def row(trade, kind):
    return {"id": hashlib.sha256((trade + kind).encode()).hexdigest(), "kind": kind,
            "position": {"trade_id": trade}}


def test_cancel_retains_terminal_slot_until_exact_ack():
    from paper_evidence_capacity import enqueue_cancel_reserved
    target = reserve_entry(initialize_legacy({}, capacity=2), 'a')
    obligation = row('a', 'PAPER_CANCEL')
    proof = {'trade_id': 'a', 'terminal_state': 'CANCELLED', 'reconciled': True, 'filled_qty': 0}
    result = enqueue_cancel_reserved(target, obligation, accounting_receipt=proof)
    assert validate(result) == {'held': 1, 'queued': 1, 'reserved': 0, 'available': 1}
    assert validate(target)['held'] == 2
    assert validate(acknowledge(result, 'a', 'PAPER_CANCEL', obligation['id']))['held'] == 0
    with pytest.raises(CapacityError, match='ACCOUNTING_UNPROVEN'):
        enqueue_cancel_reserved(target, obligation, accounting_receipt={**proof, 'filled_qty': 0.1})


def test_actual_enqueue_uses_reserved_capacity_at_full_occupancy():
    from paper_research_obligations import enqueue_fill, enqueue_close
    target = reserve_entry(initialize_legacy({'paper_only': True, 'live_armed': False}, capacity=2), 'a')
    position = {'trade_id': 'a', 'qty': 1}
    enqueue_fill(target, {'trade_id': 'a'}, {}, position, epoch_id='e1')
    enqueue_close(target, position, {}, {'net_pnl_usd': 1}, epoch_id='e1')
    assert validate(target) == {'held': 2, 'queued': 2, 'reserved': 0, 'available': 0}
    before = copy.deepcopy(target)
    enqueue_close(target, position, {}, {'net_pnl_usd': 1}, epoch_id='e1')
    assert target == before


def test_frozen_plan_rehash_preserves_reservation_and_exact_ack():
    from paper_evidence_capacity import replace_prepared
    original = row('a', 'PAPER_CLOSE')
    target = enqueue_reserved(reserve_entry(initialize_legacy({}), 'a'), original)
    prepared = dict(original, id='f' * 64, write_plan={'rows': []})
    updated = replace_prepared(target, original, prepared)
    assert validate(updated) == validate(target)
    assert target[QUEUE] == [original]
    with pytest.raises(CapacityError, match='ACK_IDENTITY'):
        acknowledge(updated, 'a', 'PAPER_CLOSE', original['id'])
    assert validate(acknowledge(updated, 'a', 'PAPER_CLOSE', prepared['id']))['queued'] == 0
    with pytest.raises(CapacityError, match='CONTENT_CHANGED'):
        replace_prepared(target, original, dict(prepared, position={'trade_id': 'a', 'qty': 9}))


def test_full_capacity_denies_new_entries_but_reserved_close_completes():
    target = initialize_legacy({}, capacity=2)
    target = reserve_entry(target, "a")
    before = copy.deepcopy(target)
    with pytest.raises(CapacityError, match="NEW_ENTRY"):
        reserve_entry(target, "b")
    assert target == before
    for kind in ("PAPER_FILL", "PAPER_CLOSE"):
        target = enqueue_reserved(target, row("a", kind))
    assert validate(target) == {"held": 2, "queued": 2, "reserved": 0, "available": 0}
    for kind in ("PAPER_FILL", "PAPER_CLOSE"):
        target = acknowledge(target, "a", kind, row("a", kind)["id"])
    assert validate(target)["held"] == 0
    assert validate(reserve_entry(target, "b"))["held"] == 2


def test_legacy_counts_pending_open_and_retained_evidence():
    target = initialize_legacy({"pending_orders": [{"trade_id": "p"}],
        "positions": [{"trade_id": "o"}], QUEUE: [row("o", "PAPER_FILL"), row("closed", "PAPER_CLOSE")]}, capacity=6)
    assert validate(target) == {"held": 5, "queued": 2, "reserved": 3, "available": 1}
    assert initialize_legacy(target) == target
    with pytest.raises(CapacityError, match="NEW_ENTRY"):
        reserve_entry(target, "new")
    assert validate(enqueue_reserved(target, row("o", "PAPER_CLOSE")))["held"] == 5


def test_legacy_overflow_does_not_mutate_or_delete():
    original = {"pending_orders": [{"trade_id": "p"}], "positions": [{"trade_id": "o"}]}
    before = copy.deepcopy(original)
    with pytest.raises(CapacityError, match="OVERCOMMITTED"):
        initialize_legacy(original, capacity=2)
    assert original == before


def test_duplicate_enqueue_is_exact_and_ack_cannot_release_other_trade():
    target = reserve_entry(initialize_legacy({}), "a")
    obligation = row("a", "PAPER_FILL")
    target = enqueue_reserved(target, obligation)
    assert enqueue_reserved(target, obligation) == target
    with pytest.raises(CapacityError, match="CONTENT_CONFLICT"):
        enqueue_reserved(target, dict(obligation, changed=True))
    with pytest.raises(CapacityError, match="ACK_IDENTITY"):
        acknowledge(target, "other", "PAPER_FILL", obligation["id"])
    assert validate(target)["held"] == 2


@pytest.mark.parametrize("change", ["remove_queue", "change_id", "duplicate_queue"])
def test_conservation_detects_missing_or_duplicated_evidence(change):
    target = enqueue_reserved(reserve_entry(initialize_legacy({}), "a"), row("a", "PAPER_FILL"))
    if change == "remove_queue":
        target[QUEUE] = []
    elif change == "change_id":
        target[QUEUE][0]["id"] = "f" * 64
    else:
        target[QUEUE].append(copy.deepcopy(target[QUEUE][0]))
    with pytest.raises(CapacityError):
        validate(target)


def test_cancel_releases_only_unused_slots_with_exact_normal_accounting():
    target = reserve_entry(initialize_legacy({}), "a")
    receipt = {"trade_id": "a", "terminal_state": "CANCELLED", "reconciled": True, "filled_qty": 0}
    for changed in ({"reconciled": False}, {"trade_id": "b"}, {"filled_qty": 0.1}, {"filled_qty": False}):
        with pytest.raises(CapacityError):
            release_cancelled(target, "a", accounting_receipt=dict(receipt, **changed))
    assert validate(release_cancelled(target, "a", accounting_receipt=receipt))["held"] == 0
    queued = enqueue_reserved(target, row("a", "PAPER_FILL"))
    with pytest.raises(CapacityError, match="HAS_EVIDENCE"):
        release_cancelled(queued, "a", accounting_receipt=receipt)


@pytest.mark.parametrize("trade", ["", " ", " a", "a ", None, 1])
def test_exact_trade_ids(trade):
    with pytest.raises(CapacityError, match="TRADE_ID"):
        reserve_entry(initialize_legacy({}), trade)


def test_open_legacy_cancel_cannot_release_reserved_close():
    target = initialize_legacy({"positions": [{"trade_id": "a"}]})
    with pytest.raises(CapacityError, match="HAS_EVIDENCE"):
        release_cancelled(target, "a", accounting_receipt={"trade_id": "a", "terminal_state": "CANCELLED", "reconciled": True, "filled_qty": 0})
