"""BOX STUB (build evidence only, Route-2 cond. 5). Same module name as the pinned closure file; NOT the real code."""
import os

import ci_build
import ci_lag
import ci_registry
import gpu_eval
import gpu_kernel
import phase_b_lattice as lat

CODE = os.path.dirname(os.path.abspath(__file__))


def plan_tiles():
    tiles = []
    for ch in range(lat.SEALED_CHANNELS):
        for th in range(lat.SEALED_THRESHOLDS_N):
            for h_i, horizon_s in enumerate(lat.SEALED_HORIZONS):
                dims = {"n_ch": 1, "n_th": 1, "n_h": 1, "n_exits": lat.SEALED_EXITS, "n_rows": lat.SEALED_ROWS}
                tiles.append({"ch": ch, "th": th, "h": h_i, "horizon_s": int(horizon_s),
                              "n": gpu_kernel.combo_count(dims), "dims": dims})
    if len(tiles) != 660 or sum(t["n"] for t in tiles) != lat.POSTED_N:
        lat.amend("tile plan")
    return tiles
