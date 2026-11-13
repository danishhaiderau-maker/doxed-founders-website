"""BOX STUB (build evidence only, Route-2 cond. 5). Same module name as the pinned closure file; NOT the real code."""
LOCKED_IDS = ("c00", "c01")


def require_ids(ids):
    if tuple(ids) != LOCKED_IDS:
        raise ValueError("ids")
    return True
