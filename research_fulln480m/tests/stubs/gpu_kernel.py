"""BOX STUB (build evidence only, Route-2 cond. 5). Same module name as the pinned closure file; NOT the real code."""
import hashlib

PTX_IMAGE = "stub-ptx-image"
N_FOLDS = 4
SMOKE_COMBO_CEILING = 1_000_000


class FailClosed(RuntimeError):
    pass


def kernel_sha256():
    return hashlib.sha256(PTX_IMAGE.encode("ascii")).hexdigest()


def combo_count(dims):
    return 2 * dims["n_ch"] * dims["n_th"] * dims["n_h"] * dims["n_exits"] * dims["n_rows"]
