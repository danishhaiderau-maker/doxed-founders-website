"""GS-FADE3-LOOSE PAPER tile (key gf3l, cid code 9). Owner Danish yes 2026-10-27; mid-epoch sample-density sibling of gf3.

PAPER ONLY / relay-ineligible / live refused. Same engine family as tile_fade3 (shared compute_features,
Fade3PaperEngine, tile_fill / paper_fill). Looser entry gates + TP 25 bp + 30 s cooldown for denser samples —
not an edge claim (R14).
"""
from __future__ import annotations

from typing import Callable, Iterable, Mapping, Optional

import paper_fill
import tile_fade3 as base
import tile_fill

TILE_KEY = "gf3l"
TILE_KEY_CODE = 9
DISPLAY_SLOT = 6
LANE = "FAMILY_GS_FADE3_LOOSE_TP25"
POLICY_ID = "GS_FADE3_E8L_FADE_ALL_W3_Z1P75_MOM1P25ATR_IMB0P1_BZ2|TP25_HARD25_XSTOP35_TIME1200_CAP1"
LEDGER_FILE = "gf3l_paper_ledger.jsonl"
TILE_NAME = "GS-FADE3-LOOSE"
SIGNAL_ID = "E8L-FADE-ALL-W3"

Z_ENTRY = 1.75
MOM_ATR_MULT = 1.25
IMB_MIN = 0.10
BZ_LIMIT = 2.0
SPREAD_MAX_BP = 3.0
FLOW_W_MIN = 3
TP_BP = 25.0
HARD_STOP_BP = 25.0
EXCHANGE_STOP_BP = 35.0
TIME_EXIT_S = 20 * 60
COOLDOWN_S = 30.0
NOTIONAL_USD = 25.0
ENTRY_CAP_BP = tile_fill.IOC_CAP_BP
A5_VERIFIED_ENV = "GF3L_A5_VERIFIED"

LOOSE_VARIANT = base.Fade3Variant(
    tile_key=TILE_KEY, tile_key_code=TILE_KEY_CODE, display_slot=DISPLAY_SLOT, lane=LANE,
    policy_id=POLICY_ID, ledger_file=LEDGER_FILE, tile_name=TILE_NAME, signal_id=SIGNAL_ID,
    z_entry=Z_ENTRY, mom_atr_mult=MOM_ATR_MULT, imb_min=IMB_MIN, bz_limit=BZ_LIMIT,
    spread_max_bp=SPREAD_MAX_BP, flow_w_min=FLOW_W_MIN, tp_bp=TP_BP, hard_stop_bp=HARD_STOP_BP,
    exchange_stop_bp=EXCHANGE_STOP_BP, time_exit_s=TIME_EXIT_S, cooldown_s=COOLDOWN_S,
    notional_usd=NOTIONAL_USD, a5_verified_env=A5_VERIFIED_ENV,
)

# Shared feature math with E8; only gate thresholds differ.
compute_features = base.compute_features
read_ledger = base.read_ledger
append_jsonl = base.append_jsonl
recount = base.recount
pool_ready = base.pool_ready
countability = base.countability
STEP_CPU_BUDGET_S = base.STEP_CPU_BUDGET_S
LIQ_MODEL = base.LIQ_MODEL
FEE_MODEL = base.FEE_MODEL
FUNDING_MODEL = base.FUNDING_MODEL
MARGIN_BP_NOTE = base.MARGIN_BP_NOTE
PRICE_SOURCES = base.PRICE_SOURCES


def entry_signals_loose(F: Mapping):
    return base.entry_signals(
        F, z_entry=Z_ENTRY, mom_atr_mult=MOM_ATR_MULT, imb_min=IMB_MIN,
        bz_limit=BZ_LIMIT, spread_max_bp=SPREAD_MAX_BP,
    )


def a5_verified(n_rest_trades: int, env: Optional[Mapping[str, str]] = None) -> bool:
    return base.a5_verified(n_rest_trades, env, env_key=A5_VERIFIED_ENV)


def rest_fill_count(rows: Iterable[Mapping]) -> int:
    return base.rest_fill_count(rows)


class LiveFeatureFeed(base.LiveFeatureFeed):
    def __init__(self, root: str) -> None:
        super().__init__(root, signals_fn=entry_signals_loose)


class Fade3LoosePaperEngine(base.Fade3PaperEngine):
    def __init__(self, **kw):
        kw.setdefault("variant", LOOSE_VARIANT)
        super().__init__(**kw)


# Alias expected by bot wiring (same shape as tile_fade3.Fade3PaperEngine).
Fade3PaperEngine = Fade3LoosePaperEngine


def exit_config(analyzer_sync_id: str) -> dict:
    return {
        "raw_policy_id": POLICY_ID,
        "analyzer_sync_id": analyzer_sync_id,
        "family": "GS_FADE3_LOOSE_FIRST_TRIGGER_WINS",
        "take_profit_bp": TP_BP, "hard_stop_bp": HARD_STOP_BP, "exchange_stop_bp": EXCHANGE_STOP_BP,
        "max_duration_sec": TIME_EXIT_S, "max_open_positions": 1,
    }


def dashboard_policy() -> dict:
    return {
        "filter_chips": ["PAPER ONLY", "LIVE REFUSED", f"Cap touch +-{ENTRY_CAP_BP:g} bp", f"TP {TP_BP:g} bp",
                         f"Hard {HARD_STOP_BP:g} bp", f"Exch stop {EXCHANGE_STOP_BP:g} bp",
                         f"{TIME_EXIT_S // 60}m time", f"{COOLDOWN_S:g}s cool", "1 open"],
        "strategy_detail": [
            f"E8L FADE ALL W={FLOW_W_MIN}: z3 >= {Z_ENTRY:g}, mom5 >= {MOM_ATR_MULT:g} ATR30, "
            f"imb5 against >= {IMB_MIN:g}, |BZ| guard {BZ_LIMIT:g}, spread <= {SPREAD_MAX_BP:g} bp",
            f"First trigger wins: TP +{TP_BP:g} / hard -{HARD_STOP_BP:g} / exchange -"
            f"{EXCHANGE_STOP_BP:g} bp / {TIME_EXIT_S // 60}m; {COOLDOWN_S:g} s cooldown, one open, "
            f"${NOTIONAL_USD:g} notional — sample-density sibling of gf3 (not an edge claim)",
        ],
        "exit": {"profile": "GS_FADE3_LOOSE_FIRST_TRIGGER_WINS", "take_profit_bp": TP_BP,
                 "hard_stop_bp": HARD_STOP_BP, "exchange_stop_bp": EXCHANGE_STOP_BP,
                 "time_exit_sec": TIME_EXIT_S},
    }
