"""BOX STUB (build evidence only, Route-2 cond. 5). Same module name as the pinned closure file; NOT the real code."""
LAG_BARS = 1


def lag_series(values, lag=LAG_BARS):
    return [None] * lag + list(values)[: max(0, len(values) - lag)]
