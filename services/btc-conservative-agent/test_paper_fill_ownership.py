import copy
import threading
import pytest
from paper_fill_ownership import FillOwnership, wrap_fill, wrap_fill_batch


@pytest.mark.parametrize("failure", [False, True])
def test_direct_fill_owner_duplicate_does_not_resimulate_and_finally_releases(failure):
    owner, lock = FillOwnership(), threading.RLock()
    order = {"trade_id": "a", "status": "PENDING"}
    calls = []
    def body(row):
        calls.append(1)
        assert guarded(row) is None
        if failure:
            raise RuntimeError("real conflicting accounting")
        return "done"
    guarded = wrap_fill(body, owner, lambda: lock)
    if failure:
        with pytest.raises(RuntimeError, match="conflicting accounting"):
            guarded(order)
    else:
        assert guarded(order) == "done"
    assert calls == [1] and owner.claims == {}


def test_batch_exception_releases_all_new_claims_not_preexisting():
    owner, lock = FillOwnership(), threading.RLock()
    prior = owner.claim({"trade_id": "prior"}, lock)
    def body():
        owner.claim({"trade_id": "a"}, lock)
        owner.claim({"trade_id": "b"}, lock)
        raise OSError("simulation failed")
    with pytest.raises(OSError):
        wrap_fill_batch(body, owner, lambda: lock)()
    assert list(owner.claims) == ["prior"]
    owner.release(prior, lock)


def test_foreign_order_same_id_is_not_silently_accepted():
    owner, lock = FillOwnership(), threading.RLock()
    order = {"trade_id": "a", "qty": 1}
    token = owner.claim(order, lock)
    with pytest.raises(RuntimeError, match="IDENTITY_CONFLICT"):
        owner.claim(dict(order, qty=2), lock)
    with pytest.raises(RuntimeError, match="TOKEN_INVALID"):
        owner.verify(copy.deepcopy(order), token, lock)
    owner.release(dict(token), lock)
    assert owner.claims
    owner.release(token, lock)
    assert not owner.claims


def test_other_thread_cannot_use_handoff_token():
    owner, lock = FillOwnership(), threading.RLock()
    order = {"trade_id": "a"}
    token = owner.claim(order, lock)
    errors = []
    def run():
        try:
            owner.verify(order, token, lock)
        except RuntimeError as error:
            errors.append(str(error))
        try:
            owner.release(token, lock)
        except RuntimeError as error:
            errors.append(str(error))
    thread = threading.Thread(target=run)
    thread.start()
    thread.join(2)
    assert errors == ["FILL_OWNERSHIP_TOKEN_INVALID", "FILL_OWNERSHIP_RELEASE_OWNER_INVALID"]
    assert owner.claims["a"] is token
    owner.release(token, lock)
