"""C1 GROSS micro builder for the BTC full-N 480,522,240 search (batch B1, C1 parts ONLY).

Binding: plan r3 33f3ec1e0e07 s7 C1; Boss GO r2 3656382c8a5c; RK 2e6b9127bf3e + addendum
22ee55488e0c (B1 SPLIT); HM consult 8b8ec9208448; RULES d8594afbb02e (R61 gross).

What this module does (C1):
- build_gross_micro(base_micro, liq) returns a NEW dict whose cost terms are gross:
  cost_bp = 0.0, every cost/fee/slippage entry of the costs dict = 0.0, fund_rate = zeros,
  fund_off = the pinned funding-off value 10**9 (phase_b_run L224, pin 3ff373b863f4).
- It never calls gpu_eval.build_micro, gpu_eval.realistic_costs or any phase_b_run path that
  refuses a zero cost (phase_b_run L209-L211). It imports no pinned closure module at all.
- Every return / first-hit-time array (R*, T*) is passed through UNCHANGED (same object).
  This module never writes a return value.

What this module does NOT do (C7 / I1 are OUT of scope until plan r4, RK 22ee55488e0c):
- No unpriceable-exit or unpriceable-entry handling, no indicator pass, no I1 encoding.
  R61(c) booking (worst bar extreme for exits, NO_FILL for entries; pin 2ecf4832af48) is
  NOT IMPLEMENTED: every entry point for it raises R61cNotImplemented (exit 78).
- Every micro built here is marked r61c_status = NOT_IMPLEMENTED_AWAITING_PLAN_R4 and
  assert_r61c_ready() refuses it (78), so no C1-only micro can reach a launch.
- Liquidation floor/fill under gross is plan r3 Q3 (OPEN, GS/RK). No value is chosen here:
  the caller must pass a LiqPolicy that cites a ruling sha; liq=None refuses (78).
"""
import math
import re
from dataclasses import dataclass

import numpy as np

from research_fulln480m import pins
from research_fulln480m.fulln_closure import ClosureViolation

R61C_PIN = "2ecf4832af48"
R61C_STATUS_KEY = "r61c_status"
R61C_STATUS_PENDING = "NOT_IMPLEMENTED_AWAITING_PLAN_R4"
# phase_b_run.py L224 (pin 3ff373b863f4...): fund_off = np.full(n_bars, np.int32(10 ** 9), np.int32)
FUND_OFF_SENTINEL = 10 ** 9

# Return / first-hit-time arrays of the pinned micro layout (phase_b_run L262-L266). Pass-through only.
RETURN_KEYS = ("Ttp", "Rtp", "Tsl", "Rsl", "Ttr", "Rtr", "Tth", "Rth", "Tt", "Rt")
# Keys copied through untouched (phase_b_run L244-L274 layout).
PASS_KEYS = ("dims", "feat", "minute", "code", "fold", "thresh", "horizons", "won", "wfl",
             "eng_micro", "feature_source", "production_market_data", "n_combos") + RETURN_KEYS
# Keys this module (re)builds.
REBUILT_KEYS = ("fund_off", "fund_rate", "cost_bp", "liq_floor", "liq_fill", "costs")
# Cost-dict entries known from the pinned code (micro["costs"] from gpu_eval.realistic_costs and
# the kernel launch args p_cost / p_liq_floor / p_liq_fill, gpu_kernel L1194 / L1213).
COST_ZERO_KEYS = ("cost_bp", "p_cost")
COST_LIQ_KEYS = {"liq_floor": "liq_floor", "liq_fill": "liq_fill",
                 "p_liq_floor": "liq_floor", "p_liq_fill": "liq_fill"}
_COSTLIKE = re.compile(r"(cost|fee|fund|slip|spread|commission)", re.IGNORECASE)
_UNPRICED = re.compile(r"(unpric|nofill|no_fill|unfill|hole|worst)", re.IGNORECASE)
_HEX = re.compile(r"^[0-9a-f]{12,64}$")


class R61cNotImplemented(ClosureViolation, NotImplementedError):
    """R61(c) unpriceable handling is not built in B1 (RK 22ee55488e0c). Exit 78."""


def _refuse_r61c(what):
    raise R61cNotImplemented(
        "%s: R61(c) unpriceable handling NOT IMPLEMENTED - pin %s (worst bar extreme exit, "
        "NO_FILL entry) awaiting plan r4 (RK 22ee55488e0c); exit 78" % (what, R61C_PIN))


@dataclass(frozen=True)
class LiqPolicy:
    """Liquidation clamp values for the gross lattice. Plan r3 Q3 is OPEN: the values and the
    ruling sha must come from a GS/RK pin; this module supplies no default."""
    liq_floor: float
    liq_fill: float
    ruling_sha: str

    def __post_init__(self):
        if not isinstance(self.ruling_sha, str) or not _HEX.match(self.ruling_sha):
            raise ClosureViolation("LiqPolicy.ruling_sha must be 12-64 lowercase hex (Q3 pin)")
        for name in ("liq_floor", "liq_fill"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(float(v)):
                raise ClosureViolation("LiqPolicy.%s must be a real number, got %r" % (name, v))


def _n_bars(base):
    minute = base.get("minute")
    if minute is None:
        raise ClosureViolation("base micro has no 'minute' array (n_bars unknown)")
    n = int(np.asarray(minute).shape[0])
    if n <= 0:
        raise ClosureViolation("base micro has zero bars")
    return n


def build_gross_micro(base_micro, liq=None):
    """C1: gross micro (fee = funding = slippage = 0, R61). See module docstring."""
    if not isinstance(base_micro, dict):
        raise ClosureViolation("base micro must be a dict")
    if liq is None:
        raise ClosureViolation("liq_floor/liq_fill under gross is plan r3 Q3 (OPEN, GS/RK): "
                               "no LiqPolicy given; refusing (78)")
    if not isinstance(liq, LiqPolicy):
        raise ClosureViolation("liq must be a LiqPolicy")
    for key in base_micro:
        if _UNPRICED.search(str(key)):
            _refuse_r61c("base micro key %r" % (key,))
    if R61C_STATUS_KEY in base_micro:
        _refuse_r61c("base micro already carries %s" % R61C_STATUS_KEY)
    known = set(PASS_KEYS) | set(REBUILT_KEYS)
    unknown = sorted(str(k) for k in base_micro if k not in known)
    if unknown:
        raise ClosureViolation("base micro has keys this C1 builder does not know: %s" % unknown)
    for key in RETURN_KEYS:
        if key not in base_micro:
            raise ClosureViolation("base micro is missing return array %s" % key)
    costs_in = base_micro.get("costs", {})
    if not isinstance(costs_in, dict):
        raise ClosureViolation("base micro 'costs' must be a dict")
    bad = sorted(str(k) for k in costs_in if k not in COST_ZERO_KEYS and k not in COST_LIQ_KEYS)
    if bad:
        raise ClosureViolation("costs dict has entries not known to be zeroable or liq: %s" % bad)

    n_bars = _n_bars(base_micro)
    out = {}
    for key in PASS_KEYS:
        if key in base_micro:
            out[key] = base_micro[key]  # same object: returns and features are never rewritten
    out["fund_off"] = np.full(n_bars, np.int32(FUND_OFF_SENTINEL), np.int32)
    out["fund_rate"] = np.zeros(n_bars, np.float64)
    out["cost_bp"] = float(pins.GROSS_COST_BP)
    out["liq_floor"] = float(liq.liq_floor)
    out["liq_fill"] = float(liq.liq_fill)
    costs = {k: float(pins.GROSS_COST_BP) for k in COST_ZERO_KEYS}
    for k, src in COST_LIQ_KEYS.items():
        costs[k] = float(getattr(liq, src))
    out["costs"] = costs
    out["gross"] = True
    out["liq_ruling_sha"] = liq.ruling_sha
    out[R61C_STATUS_KEY] = R61C_STATUS_PENDING
    assert_gross(out)
    return out


def assert_gross(micro):
    """Any non-zero cost, fee, slippage or funding term -> ClosureViolation (78)."""
    if not isinstance(micro, dict) or micro.get("gross") is not True:
        raise ClosureViolation("micro is not marked gross")
    cb = micro.get("cost_bp")
    if isinstance(cb, bool) or not isinstance(cb, (int, float)) or float(cb) != 0.0:
        raise ClosureViolation("cost_bp is not exactly 0.0 (R61 gross): %r" % (cb,))
    costs = micro.get("costs")
    if not isinstance(costs, dict):
        raise ClosureViolation("costs dict missing")
    for k, v in costs.items():
        if k in COST_LIQ_KEYS:
            continue
        if k not in COST_ZERO_KEYS:
            raise ClosureViolation("unknown cost entry %r" % (k,))
        if isinstance(v, bool) or not isinstance(v, (int, float)) or float(v) != 0.0:
            raise ClosureViolation("cost entry %s is not 0.0: %r" % (k, v))
    for k in COST_ZERO_KEYS:
        if k not in costs:
            raise ClosureViolation("cost entry %s missing" % k)
    for k, v in micro.items():
        if k in ("cost_bp", "costs", "fund_off", "fund_rate"):
            continue
        if _COSTLIKE.search(str(k)):
            raise ClosureViolation("unexpected cost-like micro key %r" % (k,))
    fr = micro.get("fund_rate")
    fo = micro.get("fund_off")
    if not isinstance(fr, np.ndarray) or fr.dtype != np.float64:
        raise ClosureViolation("fund_rate must be a float64 array")
    if not np.all(fr == 0.0):
        raise ClosureViolation("fund_rate has non-zero entries (funding must be 0 under gross)")
    if not isinstance(fo, np.ndarray) or fo.dtype != np.int32:
        raise ClosureViolation("fund_off must be an int32 array")
    if fo.shape != fr.shape or not np.all(fo == np.int32(FUND_OFF_SENTINEL)):
        raise ClosureViolation("fund_off is not the pinned funding-off value everywhere")


def assert_r61c_ready(micro):
    """Launch-side check. C7 is not built, so every micro is refused (78) until plan r4."""
    if not isinstance(micro, dict) or micro.get(R61C_STATUS_KEY) != "IMPLEMENTED":
        _refuse_r61c("assert_r61c_ready")
    _refuse_r61c("assert_r61c_ready (no C7 implementation exists in this module)")


def unpriced_counts(*args, **kwargs):
    """Interfaces.md B1 name. R61(c) counting is C7 -> NOT IMPLEMENTED (78)."""
    _refuse_r61c("unpriced_counts")


def unpriceable_exit_returns(*args, **kwargs):
    """Unpriceable EXIT booking (pin 2ecf4832af48 (b)/(c): worst bar extreme). NOT IMPLEMENTED (78)."""
    _refuse_r61c("unpriceable_exit_returns")


def unpriceable_entry_nofill(*args, **kwargs):
    """Unpriceable ENTRY = NO_FILL (pin 2ecf4832af48 (a)). NOT IMPLEMENTED (78)."""
    _refuse_r61c("unpriceable_entry_nofill")
