"""BOX STUB (build evidence only, Route-2 cond. 5). Same module name as the pinned closure file; NOT the real code."""
import ci_registry
import gpu_kernel

POSTED_N = 480_522_240
SEALED_CHANNELS = 22
SEALED_THRESHOLDS_N = 10
SEALED_HORIZONS = (60, 300, 900)
SEALED_EXITS = 768
SEALED_ROWS = 474


def posted_dims():
    return {"n_ch": SEALED_CHANNELS, "n_th": SEALED_THRESHOLDS_N, "n_h": len(SEALED_HORIZONS),
            "n_exits": SEALED_EXITS, "n_rows": SEALED_ROWS}


def amend(msg):
    raise gpu_kernel.FailClosed(msg)
