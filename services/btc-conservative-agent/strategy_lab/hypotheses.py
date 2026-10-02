"""Pre-registered hypotheses and bounded exploratory families.

This is the only place the strategy lab learns *what* to test. Rules:

* A PREREGISTERED hypothesis is fixed before the data that will judge it.
  ``registered_at`` is the end of the data its originating study saw; the
  engine reports the full current-epoch result *and* the post-registration
  (genuinely unseen) result, and Holm-adjusts the primary family on the
  unseen result.
* CONTROL and STRESS rows are diagnostics of a primary (expected <= 0, or a
  realistic degradation); they are never ranked.
* EXPLORATORY families are small, enumerated grids (<= 64 configs) scored with
  a family-wise null, Benjamini-Hochberg q-values and an anchored
  walk-forward. Brute-force grids (the 96,720 / 28,375-config searches) are
  deliberately not reproduced: their selection noise is what DATA-SUFFICIENCY
  model A measured.

Adding a hypothesis = append a spec here with a new id and a ``registered_at``
not earlier than the commit that adds it. Never edit a registered spec;
retire it and register a new id instead.
"""
from __future__ import annotations

import hashlib
import json
from itertools import product

from strategy_lab.simulator import EntrySpec, ExitSpec

FTF_ENTRY = EntrySpec(kind="TAKER", latency_sec=1, ttl_sec=15, protection_bp=5.0, max_spread_bp=1.68)
FTF_EXIT = ExitSpec(tcap_sec=3600, hard_bp=40.0)


def _xvl(window: int, threshold: float, hold: int, latency: int = 1, mode: str = "both_mean") -> dict:
    return {"family": "XVL", "window_sec": window, "threshold_bp": threshold, "mode": mode,
            "entry": EntrySpec(kind="TAKER", latency_sec=latency), "exit": ExitSpec(tcap_sec=hold)}


def _ai(source: str, entry: EntrySpec = FTF_ENTRY, exit_: ExitSpec = FTF_EXIT) -> dict:
    return {"family": "AI_CALL", "source": source, "entry": entry, "exit": exit_}


HYPOTHESES = (
    {"id": "H_FTF_REPLICA", "kind": "PREREGISTERED", "primary": True,
     "registered_at": "2026-10-01T07:59:00Z",
     "source": "Tile 4 FAMILY_TREND_FADE_60 (PR #246) / TILE2-DESIGN-20261001.md H-60",
     "question": "Does the inverted score-led AI side, taker, 60 min with a 40 bp stop, keep positive after-spread EV?",
     **_ai("INV_LLM")},
    {"id": "H_XVL_10S_8BP_60S", "kind": "PREREGISTERED", "primary": True,
     "registered_at": "2026-10-01T20:45:00Z",
     "source": "NEXT-TILE-RESEARCH-20261002.md section 4.2 (central spec chosen for neighbourhood stability)",
     "question": "When Binance/Bybit lead Bitfinex by >= 8 bp over 10 s, does a 60 s Bitfinex taker follow pay the spread?",
     **_xvl(10, 8.0, 60)},
    {"id": "H_XVL_30S_12BP_60S", "kind": "PREREGISTERED", "primary": True,
     "registered_at": "2026-10-01T20:45:00Z",
     "source": "NEXT-TILE-RESEARCH-20261002.md section 5 (slower sibling)",
     "question": "Same signal with a 30 s window and 12 bp threshold: higher edge per trade, fewer trades?",
     **_xvl(30, 12.0, 60)},
    {"id": "C_XVL_OWN_MOMENTUM", "kind": "CONTROL", "primary": False, "control_of": "H_XVL_10S_8BP_60S",
     "registered_at": "2026-10-01T20:45:00Z", "expect": "<= 0",
     "source": "NEXT-TILE-RESEARCH-20261002.md section 4.3",
     "question": "Bitfinex's own 10 s momentum (no other venue) must not explain XVL.",
     **_xvl(10, 8.0, 60, mode="own_momentum")},
    {"id": "S_XVL_LATENCY_3S", "kind": "STRESS", "primary": False, "control_of": "H_XVL_10S_8BP_60S",
     "registered_at": "2026-10-01T20:45:00Z", "expect": "> 0",
     "source": "NEXT-TILE-RESEARCH-20261002.md section 4.3",
     "question": "XVL central spec with a realistic 3 s order latency.",
     **_xvl(10, 8.0, 60, latency=3)},
    {"id": "S_FTF_SLIPPAGE_1BP", "kind": "STRESS", "primary": False, "control_of": "H_FTF_REPLICA",
     "registered_at": "2026-10-01T20:45:00Z", "expect": "> 0", "taker_slippage_bp": 1.0,
     "source": "DATA-SUFFICIENCY-MODEL-A-20261002.md fill realism",
     "question": "ftf replica with 1 bp extra slippage per taker leg.",
     **_ai("INV_LLM")},
)

EXPLORATORY_FAMILIES = (
    {"id": "X_XVL_NEIGHBOURHOOD", "registered_at": "2026-10-01T20:45:00Z",
     "null": "BLOCK_SIGN_15M", "source": "NEXT-TILE-RESEARCH-20261002.md section 4.2 neighbourhood",
     "configs": tuple({"id": f"XVL_W{w}_T{t:g}_H{h}", **_xvl(w, t, h)}
                      for w, t, h in product((5, 10, 30), (6.0, 8.0, 10.0, 12.0), (30, 60, 120)))},
    {"id": "X_AI_SIDE_HORIZON", "registered_at": "2026-10-01T20:45:00Z",
     "null": "CIRCULAR_SHIFT", "source": "TILE2-DESIGN-20261001.md / NEXT-TILE section 3 (bounded)",
     "configs": tuple({"id": f"AI_{src}_T{tc}", **_ai(src, exit_=ExitSpec(tcap_sec=tc, hard_bp=40.0))}
                      for src, tc in product(("LLM_SCORE", "INV_LLM"), (900, 1800, 3600, 5400)))},
)

MAX_EXPLORATORY_CONFIGS = 64


def _jsonable(spec: dict) -> dict:
    out = {}
    for k, v in spec.items():
        if isinstance(v, (EntrySpec, ExitSpec)):
            from strategy_lab.simulator import spec_dict
            out[k] = spec_dict(v)
        elif isinstance(v, tuple):
            out[k] = [(_jsonable(x) if isinstance(x, dict) else x) for x in v]
        else:
            out[k] = v
    return out


def spec_hash(spec: dict) -> str:
    body = {k: v for k, v in _jsonable(spec).items() if k not in ("question", "source")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def registry_signature() -> str:
    parts = [spec_hash(h) for h in HYPOTHESES] + [spec_hash(f) for f in EXPLORATORY_FAMILIES]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def validate() -> list:
    """Registry defects (empty list = valid)."""
    defects, seen = [], set()
    for h in HYPOTHESES:
        if h["id"] in seen:
            defects.append(f"DUPLICATE_ID:{h['id']}")
        seen.add(h["id"])
        if h["kind"] not in ("PREREGISTERED", "CONTROL", "STRESS"):
            defects.append(f"BAD_KIND:{h['id']}")
        if h["kind"] != "PREREGISTERED" and h.get("control_of") not in {x["id"] for x in HYPOTHESES}:
            defects.append(f"ORPHAN_CONTROL:{h['id']}")
    for fam in EXPLORATORY_FAMILIES:
        if len(fam["configs"]) > MAX_EXPLORATORY_CONFIGS:
            defects.append(f"EXPLORATORY_FAMILY_TOO_LARGE:{fam['id']}:{len(fam['configs'])}")
        ids = [c["id"] for c in fam["configs"]]
        if len(ids) != len(set(ids)):
            defects.append(f"DUPLICATE_CONFIG:{fam['id']}")
    return defects
