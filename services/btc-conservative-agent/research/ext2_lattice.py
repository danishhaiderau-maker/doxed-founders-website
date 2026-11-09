"""Batched composite-exit lattice (A10/A11, HM B12). Paper only. Live is off.

The 54 exits are TP x TS x TC x HS. For every entry event the lattice is
reduced to first-passage indices on the global trade-print path:

    start  first print with mts > entry_mts
    cens   first print with mts >= the active censor boundary
    liq    first print at/beyond the derived 100x liquidation level
    hs[3]  first print at/beyond each hard-stop level (price space)
    tp[3]  first print at/beyond each take-profit level
    ts[3]  first print with mts - entry_mts >= TS minutes
    th     first print at/after the close of the first closed bar whose
           feature crossed back through its trailing p50 (thesis)

Each exit is then the minimum of those indices with the per-print priority
LIQUIDATION > HARD_STOP > TAKE_PROFIT > THESIS > TIME, censored at `cens`.
First passages use a block sparse table (max/min over 64-print blocks) and
binary lifting, vectorised across events, in fixed-size chunks (bounded
memory; each chunk is timed against the 120 s GPU unit cap). The same code
runs on numpy (reference/parity only) and on torch (CUDA for a real run).
Hit tests are in price space, identical in the per-print reference scan.
"""
from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

import numpy as np

from research.ext2_costs import LIQUIDATION_ADVERSE_BP, Ext2Stop
from research.ext2_rules import liquidation_fill_price

TAKE_PROFIT_BP = (20.0, 40.0, 80.0)
TIME_STOP_MIN = (30, 120, 480)
THESIS_CUT = (0, 1)
HARD_STOP_BP = (15.0, 30.0, 40.0)
EXIT_GRID_N = 54
BLOCK = 64
CHUNK_EVENTS = 65_536
GPU_CHUNK_CAP_SEC = 120.0
REASON = {1: "TIME", 2: "THESIS", 3: "TAKE_PROFIT", 4: "HARD_STOP", 5: "LIQUIDATION",
          6: "EMBARGO_FORCE_CLOSE", 7: "DROPPED", 8: "PATH_END"}


def exit_table() -> dict[str, np.ndarray]:
    """Exit index x = ((tp*3 + ts)*2 + tc)*3 + hs (A7 nesting TP, TS, TC, HS)."""
    tp, ts, tc, hs = np.meshgrid(np.arange(3), np.arange(3), np.arange(2), np.arange(3), indexing="ij")
    return {"tp": tp.reshape(-1), "ts": ts.reshape(-1), "tc": tc.reshape(-1), "hs": hs.reshape(-1)}


class NumpyBackend:
    name = "numpy"
    device = "cpu"

    def asarray(self, value, dtype=None):
        return np.asarray(value, dtype=dtype)

    def to_numpy(self, value):
        return np.asarray(value)

    def searchsorted(self, sorted_arr, values, right=False):
        return np.searchsorted(sorted_arr, values, side="right" if right else "left").astype(np.int64)

    def arange(self, n):
        return np.arange(n, dtype=np.int64)

    def where(self, cond, a, b):
        return np.where(cond, a, b)

    def minimum(self, a, b):
        return np.minimum(a, b)

    def maximum(self, a, b):
        return np.maximum(a, b)

    def full(self, shape, value, dtype):
        return np.full(shape, value, dtype=dtype)

    def argmax_axis1(self, mask):
        return np.argmax(mask, axis=1).astype(np.int64)

    def any_axis1(self, mask):
        return mask.any(axis=1)

    def clip(self, value, lo, hi):
        return np.clip(value, lo, hi)

    def float_dtype(self):
        return np.float64

    def int_dtype(self):
        return np.int64

    def matmul(self, a, b):
        return a @ b

    def measured_dtype(self, value) -> str:
        return str(np.asarray(value).dtype)

    def sync(self):
        return None


class TorchBackend:
    name = "torch"

    def __init__(self, device: str):
        import torch
        self.torch = torch
        self.device = device

    def asarray(self, value, dtype=None):
        torch = self.torch
        mapping = {np.float64: torch.float64, np.int64: torch.int64, bool: torch.bool, np.bool_: torch.bool}
        if isinstance(value, torch.Tensor):
            out = value.to(self.device)
            return out.to(mapping[dtype]) if dtype is not None else out
        arr = np.asarray(value)
        tensor = torch.as_tensor(arr, device=self.device)
        return tensor.to(mapping[dtype]) if dtype is not None else tensor

    def to_numpy(self, value):
        return value.detach().cpu().numpy()

    def searchsorted(self, sorted_arr, values, right=False):
        return self.torch.searchsorted(sorted_arr, values, right=right).to(self.torch.int64)

    def arange(self, n):
        return self.torch.arange(n, dtype=self.torch.int64, device=self.device)

    def where(self, cond, a, b):
        torch = self.torch
        if not isinstance(a, torch.Tensor):
            a = torch.as_tensor(a, device=self.device)
        if not isinstance(b, torch.Tensor):
            b = torch.as_tensor(b, device=self.device)
        return torch.where(cond, a, b)

    def minimum(self, a, b):
        return self.torch.minimum(a, b)

    def maximum(self, a, b):
        return self.torch.maximum(a, b)

    def full(self, shape, value, dtype):
        mapping = {np.float64: self.torch.float64, np.int64: self.torch.int64}
        return self.torch.full(shape, value, dtype=mapping[dtype], device=self.device)

    def argmax_axis1(self, mask):
        return mask.to(self.torch.int64).argmax(dim=1)

    def any_axis1(self, mask):
        return mask.any(dim=1)

    def clip(self, value, lo, hi):
        return value.clamp(lo, hi)

    def float_dtype(self):
        return self.torch.float64

    def int_dtype(self):
        return self.torch.int64

    def matmul(self, a, b):
        torch = self.torch
        out = torch.as_tensor(np.ascontiguousarray(a), dtype=torch.float64, device=self.device) @ \
            torch.as_tensor(np.ascontiguousarray(b), dtype=torch.float64, device=self.device)
        return out.detach().cpu().numpy()

    def measured_dtype(self, value) -> str:
        return str(value.dtype).replace("torch.", "")

    def sync(self):
        if str(self.device).startswith("cuda"):
            self.torch.cuda.synchronize()


def cuda_available() -> bool:
    """True only when torch imports and reports a CUDA device. Never falls back."""
    try:
        import torch
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        raise Ext2Stop("STOP_GPU_MISSING") from exc


def make_backend(kernel: str, device: str):
    if kernel == "numpy":
        if device != "cpu":
            raise Ext2Stop("STOP_KERNEL")
        return NumpyBackend()
    if kernel == "torch":
        try:
            return TorchBackend(device)
        except ImportError as exc:
            raise Ext2Stop("STOP_GPU_MISSING") from exc
    raise Ext2Stop("STOP_KERNEL")


class PrintPath:
    """Global print path (sorted by mts, id) with block sparse tables."""

    def __init__(self, mts: np.ndarray, price: np.ndarray, backend, block: int = BLOCK):
        self.backend = backend
        self.block = int(block)
        mts = np.asarray(mts, dtype=np.int64)
        price = np.asarray(price, dtype=np.float64)
        self.n = int(mts.shape[0])
        nblocks = self.n // self.block + 2
        levels = max(1, int(math.ceil(math.log2(nblocks))) + 1)
        padded = nblocks * self.block
        up = np.full(padded, -np.inf)
        down = np.full(padded, np.inf)
        up[: self.n] = price
        down[: self.n] = price
        bmax = up.reshape(nblocks, self.block).max(axis=1)
        bmin = down.reshape(nblocks, self.block).min(axis=1)
        tmax = [bmax]
        tmin = [bmin]
        for k in range(1, levels):
            span = 1 << (k - 1)
            prev_max, prev_min = tmax[-1], tmin[-1]
            nxt_max = np.full(nblocks, -np.inf)
            nxt_min = np.full(nblocks, np.inf)
            nxt_max[: nblocks - span] = np.maximum(prev_max[: nblocks - span], prev_max[span:])
            nxt_min[: nblocks - span] = np.minimum(prev_min[: nblocks - span], prev_min[span:])
            nxt_max[nblocks - span:] = prev_max[nblocks - span:]
            nxt_min[nblocks - span:] = prev_min[nblocks - span:]
            tmax.append(nxt_max)
            tmin.append(nxt_min)
        self.nblocks = nblocks
        self.levels = levels
        b = backend
        self.mts = b.asarray(mts, np.int64)
        self.price = b.asarray(price, np.float64)
        self.up = b.asarray(up, np.float64)
        self.down = b.asarray(down, np.float64)
        self.tmax = [b.asarray(t, np.float64) for t in tmax]
        self.tmin = [b.asarray(t, np.float64) for t in tmin]
        self.offs = b.arange(self.block)

    def first_passage(self, start, level, upward: bool):
        """First print index i >= start with price >= level (upward) or <= level.
        Returns n when none. Vectorised over events."""
        b = self.backend
        blk = self.block
        values = self.up if upward else self.down
        table = self.tmax if upward else self.tmin
        start = b.clip(start, 0, self.n)
        b0 = start // blk
        cand = b0[:, None] * blk + self.offs[None, :]
        vals = values[cand]
        lvl = level[:, None]
        hit = (vals >= lvl) if upward else (vals <= lvl)
        hit = hit & (cand >= start[:, None])
        found1 = b.any_axis1(hit)
        i1 = b0 * blk + b.argmax_axis1(hit)
        cur = b0 + 1
        for k in range(self.levels - 1, -1, -1):
            span = 1 << k
            room = (cur + span) <= self.nblocks
            safe = b.clip(cur, 0, self.nblocks - 1)
            tv = table[k][safe]
            skip = room & ((tv < level) if upward else (tv > level))
            cur = b.where(skip, cur + span, cur)
        inside = cur < self.nblocks
        cur_safe = b.clip(cur, 0, self.nblocks - 1)
        cand2 = cur_safe[:, None] * blk + self.offs[None, :]
        vals2 = values[cand2]
        hit2 = (vals2 >= lvl) if upward else (vals2 <= lvl)
        found2 = inside & b.any_axis1(hit2)
        i2 = cur_safe * blk + b.argmax_axis1(hit2)
        none = b.full(start.shape, self.n, np.int64)
        out = b.where(found1, i1, b.where(found2, i2, none))
        return b.minimum(out, none)


def compute_base(path: PrintPath, entry_mts: np.ndarray, entry_price: np.ndarray, side: np.ndarray,
                 boundary_mts: np.ndarray, *, chunk: int = CHUNK_EVENTS,
                 chunk_cap_sec: float = GPU_CHUNK_CAP_SEC, on_chunk=None) -> dict[str, Any]:
    """First-passage indices for every event (any side). Chunked and timed."""
    b = path.backend
    total = int(np.asarray(entry_mts).shape[0])
    out = {
        "start": np.zeros(total, dtype=np.int64), "cens": np.zeros(total, dtype=np.int64),
        "liq": np.zeros(total, dtype=np.int64), "hs": np.zeros((total, 3), dtype=np.int64),
        "tp": np.zeros((total, 3), dtype=np.int64), "ts": np.zeros((total, 3), dtype=np.int64),
    }
    timings = []
    dtype_seen = None
    for lo in range(0, total, chunk):
        hi = min(total, lo + chunk)
        if on_chunk is not None:
            on_chunk(lo // chunk)
        t0 = time.monotonic()
        em = b.asarray(np.asarray(entry_mts[lo:hi], dtype=np.int64), np.int64)
        ep = b.asarray(np.asarray(entry_price[lo:hi], dtype=np.float64), np.float64)
        sd = b.asarray(np.asarray(side[lo:hi], dtype=np.float64), np.float64)
        bd = b.asarray(np.asarray(boundary_mts[lo:hi], dtype=np.int64), np.int64)
        dtype_seen = b.measured_dtype(ep)
        start = b.searchsorted(path.mts, em, right=True)
        cens = b.searchsorted(path.mts, bd, right=False)
        long_side = sd > 0
        liq_level = ep * (1.0 - sd * (LIQUIDATION_ADVERSE_BP / 10000.0))
        liq_dn = path.first_passage(start, liq_level, upward=False)
        liq_up = path.first_passage(start, liq_level, upward=True)
        liq = b.where(long_side, liq_dn, liq_up)
        hs_cols, tp_cols, ts_cols = [], [], []
        for value in HARD_STOP_BP:
            level = ep * (1.0 - sd * (value / 10000.0))
            hs_cols.append(b.where(long_side, path.first_passage(start, level, upward=False),
                                   path.first_passage(start, level, upward=True)))
        for value in TAKE_PROFIT_BP:
            level = ep * (1.0 + sd * (value / 10000.0))
            tp_cols.append(b.where(long_side, path.first_passage(start, level, upward=True),
                                   path.first_passage(start, level, upward=False)))
        for minutes in TIME_STOP_MIN:
            ts_cols.append(b.searchsorted(path.mts, em + int(minutes) * 60_000, right=False))
        b.sync()
        out["start"][lo:hi] = b.to_numpy(start)
        out["cens"][lo:hi] = b.to_numpy(cens)
        out["liq"][lo:hi] = b.to_numpy(liq)
        for k in range(3):
            out["hs"][lo:hi, k] = b.to_numpy(hs_cols[k])
            out["tp"][lo:hi, k] = b.to_numpy(tp_cols[k])
            out["ts"][lo:hi, k] = b.to_numpy(ts_cols[k])
        elapsed = time.monotonic() - t0
        timings.append(elapsed)
        if elapsed > chunk_cap_sec:
            raise Ext2Stop("STOP_GPU_CHUNK_CAP")
    out["chunk_seconds"] = timings
    out["chunks"] = len(timings)
    out["chunk_events"] = int(chunk)
    out["dtype"] = dtype_seen
    out["kernel"] = b.name
    out["device"] = str(b.device)
    return out


def resolve(base: Mapping[str, np.ndarray], ev: np.ndarray, x: np.ndarray, thesis_idx: np.ndarray,
            mts: np.ndarray, price: np.ndarray, entry_mts: np.ndarray, entry_price: np.ndarray,
            side: np.ndarray, has_boundary: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Exit for event rows `ev` under exit indices `x` (vectorised, CPU)."""
    table = EXIT_TABLE
    ev = np.asarray(ev, dtype=np.int64)
    x = np.asarray(x, dtype=np.int64)
    n = int(mts.shape[0])
    start = base["start"][ev]
    cens = base["cens"][ev]
    liq = base["liq"][ev]
    hs = base["hs"][ev, table["hs"][x]]
    tp = base["tp"][ev, table["tp"][x]]
    ts = base["ts"][ev, table["ts"][x]]
    th = np.where(table["tc"][x] > 0, np.maximum(np.asarray(thesis_idx, dtype=np.int64), start), n)
    big = np.int64(n + 1)
    best = np.full(ev.shape, big)
    code = np.zeros(ev.shape, dtype=np.int64)
    for idx, reason in ((liq, 5), (hs, 4), (tp, 3), (th, 2), (ts, 1)):
        better = idx < best
        best = np.where(better, idx, best)
        code = np.where(better, reason, code)
    limit = np.minimum(cens, n)
    dropped = start >= limit
    late = best >= limit
    # Censored (6) when a print at/after the boundary exists, i.e. the path continues past
    # it; path end (8) when the data ends before the boundary (cens == n).
    bounded = (cens < n) if has_boundary is None else np.asarray(has_boundary, dtype=bool)
    code = np.where(late, np.where(bounded, 6, 8), code)
    best = np.where(late, limit - 1, best)
    code = np.where(dropped, 7, code)
    best = np.where(dropped, -1, best)
    safe = np.clip(best, 0, max(n - 1, 0))
    px = np.where(dropped, np.nan, price[safe] if n else np.nan)
    e_price = np.asarray(entry_price, dtype=np.float64)
    s = np.asarray(side, dtype=np.float64)
    liq_level = e_price * (1.0 - s * LIQUIDATION_ADVERSE_BP / 10000.0)
    worse = np.where(s > 0, np.minimum(liq_level, px), np.maximum(liq_level, px))
    px = np.where(code == 5, worse, px)
    exit_mts = np.where(dropped, -1, mts[safe] if n else -1)
    gross = s * (px / e_price - 1.0) * 10000.0
    return {"code": code, "exit_idx": best, "exit_price": px, "exit_mts": exit_mts, "gross_bp": gross,
            "hold_ms": np.where(dropped, -1, exit_mts - np.asarray(entry_mts, dtype=np.int64))}


EXIT_TABLE = exit_table()


def reference_scan(mts: np.ndarray, price: np.ndarray, thesis: np.ndarray, *, entry_price: float, entry_mts: int,
                   side: float, boundary: int | None) -> dict[str, np.ndarray]:
    """Per-print reference (slow, obviously-correct) for parity tests.

    Prints after entry and before the boundary are walked one by one; for each
    of the 54 exits the first print meeting a condition wins with priority
    LIQUIDATION > HARD_STOP > TAKE_PROFIT > THESIS > TIME."""
    mts = np.asarray(mts, dtype=np.int64)
    price = np.asarray(price, dtype=np.float64)
    keep = mts > int(entry_mts)
    if boundary is not None:
        keep &= mts < int(boundary)
    idxs = np.flatnonzero(keep)
    codes = np.zeros(EXIT_GRID_N, dtype=np.int64)
    exit_px = np.full(EXIT_GRID_N, np.nan)
    exit_i = np.full(EXIT_GRID_N, -1, dtype=np.int64)
    liq_level = entry_price * (1.0 - side * LIQUIDATION_ADVERSE_BP / 10000.0)
    for x in range(EXIT_GRID_N):
        tp_v = TAKE_PROFIT_BP[EXIT_TABLE["tp"][x]]
        ts_v = TIME_STOP_MIN[EXIT_TABLE["ts"][x]]
        tc_v = THESIS_CUT[EXIT_TABLE["tc"][x]]
        hs_v = HARD_STOP_BP[EXIT_TABLE["hs"][x]]
        hs_level = entry_price * (1.0 - side * hs_v / 10000.0)
        tp_level = entry_price * (1.0 + side * tp_v / 10000.0)
        chosen = None
        for i in idxs:
            p = price[i]
            if (side > 0 and p <= liq_level) or (side < 0 and p >= liq_level):
                chosen = (i, 5)
            elif (side > 0 and p <= hs_level) or (side < 0 and p >= hs_level):
                chosen = (i, 4)
            elif (side > 0 and p >= tp_level) or (side < 0 and p <= tp_level):
                chosen = (i, 3)
            elif tc_v and bool(thesis[i]):
                chosen = (i, 2)
            elif mts[i] - entry_mts >= ts_v * 60_000:
                chosen = (i, 1)
            if chosen is not None:
                break
        if chosen is None:
            if idxs.size == 0:
                codes[x] = 7
                continue
            censored = boundary is not None and bool(np.any(mts >= int(boundary)))
            chosen = (idxs[-1], 6 if censored else 8)
        i, code = chosen
        codes[x] = code
        exit_i[x] = i
        px = price[i]
        if code == 5:
            px = liquidation_fill_price(entry_price, side, px)
        exit_px[x] = px
    gross = side * (exit_px / entry_price - 1.0) * 10000.0
    return {"code": codes, "exit_idx": exit_i, "exit_price": exit_px, "gross_bp": gross}
