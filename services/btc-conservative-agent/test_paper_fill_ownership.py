import copy
import ast
from pathlib import Path
from types import SimpleNamespace
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


@pytest.mark.parametrize("failure", [False, True])
def test_actual_fill_runtime_wrapper_releases_on_admin_pause_path(failure):
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fill_order")
    binding = next(n for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "fill_order" for t in n.targets))
    owner, lock = FillOwnership(), threading.RLock()
    order = {"trade_id": "a", "status": "PENDING", "fill_handoff_in_progress": True}
    def cancel(*args, **kwargs):
        assert owner.claims["a"]["order"] is order
        if failure:
            raise OSError("cancel failed")
        return {"finalized": True}
    ns = {"wrap_fill": wrap_fill, "_paper_fill_ownership": owner, "trade_lock": lock,
          "manual_admin_pause_active": lambda: True, "_cancel_pending_order_confirmed": cancel,
          "fill_handoff_trade_ids": {"a"}, "pipeline_state_sync": lambda: None,
          "logger": SimpleNamespace(warning=lambda *a: None)}
    exec(compile(ast.Module(body=[fn, binding], type_ignores=[]), "actual-fill-wrapper", "exec"), ns)
    if failure:
        with pytest.raises(OSError):
            ns["fill_order"](order)
    else:
        assert ns["fill_order"](order) is None
    assert owner.claims == {} and ns["fill_handoff_trade_ids"] == set()
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
