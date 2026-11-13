"""BOX STUB (build evidence only, Route-2 cond. 5). Same module name as the pinned closure file; NOT the real code."""
import ext2_search
import gpu_kernel


def evaluate(dims):
    return gpu_kernel.combo_count(dims) + len(ext2_search.PROD_COSTS)
