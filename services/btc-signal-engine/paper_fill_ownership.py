"""Single process, exact-object fill ownership under the caller's trade lock."""
import threading


class FillOwnership:
    def __init__(self, on_release=None):
        self.claims = {}
        self.on_release = on_release

    def claim(self, order, lock):
        tid = str(order.get("trade_id") or "")
        if not tid:
            raise ValueError("FILL_TRADE_ID_MISSING")
        with lock:
            old = self.claims.get(tid)
            if old:
                if old["order"] is not order:
                    raise RuntimeError("FILL_OWNERSHIP_IDENTITY_CONFLICT")
                return None
            if str(order.get("status") or "").upper() in {"FILLED", "OPEN", "CLOSED", "CANCELLED", "EXPIRED", "COMPLETE"}:
                return None
            token = {"order": order, "owner": threading.get_ident(), "trade_id": tid}
            self.claims[tid] = token
            return token

    def verify(self, order, token, lock):
        with lock:
            if (not token or token.get("order") is not order
                    or token.get("owner") != threading.get_ident()
                    or self.claims.get(token.get("trade_id")) is not token):
                raise RuntimeError("FILL_OWNERSHIP_TOKEN_INVALID")

    def release(self, token, lock):
        with lock:
            if token and self.claims.get(token.get("trade_id")) is token:
                if token.get("owner") != threading.get_ident():
                    raise RuntimeError("FILL_OWNERSHIP_RELEASE_OWNER_INVALID")
                del self.claims[token["trade_id"]]
                token["order"].pop("fill_handoff_in_progress", None)
                if self.on_release:
                    self.on_release(token["trade_id"])


def wrap_fill(function, ownership, lock_provider):
    def guarded(order, *, _fill_claim=None):
        lock = lock_provider()
        token = _fill_claim if _fill_claim is not None else ownership.claim(order, lock)
        if token is None:
            return None
        ownership.verify(order, token, lock)
        try:
            return function(order)
        finally:
            ownership.release(token, lock)
    return guarded


def wrap_fill_batch(function, ownership, lock_provider):
    def guarded(*args, **kwargs):
        lock = lock_provider()
        owner = threading.get_ident()
        with lock:
            prior = {id(token) for token in ownership.claims.values()}
        try:
            return function(*args, **kwargs)
        finally:
            with lock:
                tokens = [token for token in ownership.claims.values()
                          if token["owner"] == owner and id(token) not in prior]
                for token in tokens:
                    ownership.release(token, lock)
    return guarded
