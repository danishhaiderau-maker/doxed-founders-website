"""Single-source literals for the B-R63X Stage A exit layer.

Binding: card B-R63X r2.1 a312e9be2d95 s4.1 (Boss GO 39ecf833b6c2; RK PASS be4ed68cd3de).
Rules c342ecf8ec9a. Live OFF. Paper only. Gross only (R61). N_FULL = 1,922,088,960.

This module imports nothing and is never imported by a test-only oracle or by
tests/ref_ext2_exit_path.py's production path (there is no production import of that file).
The literals stay as written in this card's commits; flips of the two locks are separate
commits under their own GOs.
"""
# EXT2-GRID (ext2 6b5956fdc19f L152-L155): 8 x 8 x 4 x 3 = 768, no column masked (R63-J).
TP_BP = (None, 5, 10, 15, 20, 30, 50, 100)
SL_BP = (None, 5, 10, 15, 20, 30, 50, 100)
TR_BP = (None, 10, 20, 50)
TH_KIND = (None, "sign_reverses", "back_inside_p50")
N_TP, N_SL, N_TR, N_TH = len(TP_BP), len(SL_BP), len(TR_BP), len(TH_KIND)
N_EXITS = N_TP * N_SL * N_TR * N_TH  # 768
N_LEVELS = (N_TP - 1) + (N_SL - 1) + (N_TR - 1)  # 7 + 7 + 3 = 17 (ext2 L1074)
# G.2 / R63-G.2-C1: LIQ floor and fill, gross. 45.0 is disclosure only (R63-J), never a filter.
LIQ_FLOOR_BP = -50.0
LIQ_FILL_BP = -100.0
LIQ_VALID_FLOOR_BP = 45.0  # disclosure only; not applied to output (S-14)
NET435_BP = -43.5  # NET only; not a LIQ floor (E-G2 interval is disclosure / killer set)
G3_ORDER = ("SL", "TP", "TR", "THESIS", "TIME")  # LIQ is in the SL slot and beats a same-row SL
EXIT_CLOCK = "X1"
EXIT_STOP = 78
# Production locks: both None in this batch (s4.3, s4.4). Flips are separate GOs.
R63H_CARD_SHA256 = None
R63I_CARD_SHA256 = None
R63H_ADOPTED_SHA256 = "beeac5878722bb278f7062e6bbac19c54839b0d034ecef66c0623a140e795979"
R63H_R3_SHA256 = "0a5ab1b75ed09f8d4477cefc8cedf53b4bfbdca2ff33380c01c15eeab04082ef"
R63I_ADOPTED_SHA256 = "9b219a019360b5a19f1aa44e2611ce34ff286a02896eb62bb74bd23c4f37120a"
R63I_R2_SHA256 = "0ec820b94dd440d8e59a54be4d038bac564210e6fd5cfed9e30e8367c255dd79"
N_FULL = 1_922_088_960
HORIZONS_12 = (1, 5, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 14400)
# Content pins of read-only decide-layer files at branch base / HEAD (unchanged on this branch).
DECIDE_SHA256 = "ad403308568ae8b59ab65260f536465800d5ce9a5bdbc0f631ee062c16d36060"
DECIDE_PINS_SHA256 = "82225e18ec3561115e95f420270011fdbfe1cd74ce2ebbef9ec93f1e2c3a624f"
CLOSURE_SHA256 = "f5b6464023a20b69dd3725b47d3ec87f09368c4956f293ac46a7bf6c3d0c1eb4"

_HEX64 = set("0123456789abcdef")


def _is_hex64(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def check_literals():
    """Start-up self-check (card s4.1 / S-8). A mismatch is SystemExit(78) at import, or STOP 78 from fulln_exit."""
    reasons = []
    if N_EXITS != 768 or N_TP * N_SL * N_TR * N_TH != 768 or N_LEVELS != 17:
        reasons.append("grid product %r != 768" % (N_EXITS,))
    if TP_BP != (None, 5, 10, 15, 20, 30, 50, 100) or SL_BP != TP_BP:
        reasons.append("TP_BP / SL_BP")
    if TR_BP != (None, 10, 20, 50) or TH_KIND != (None, "sign_reverses", "back_inside_p50"):
        reasons.append("TR_BP / TH_KIND")
    if type(LIQ_FLOOR_BP) is not float or LIQ_FLOOR_BP != -50.0:
        reasons.append("LIQ_FLOOR_BP")
    if type(LIQ_FILL_BP) is not float or LIQ_FILL_BP != -100.0:
        reasons.append("LIQ_FILL_BP")
    if G3_ORDER != ("SL", "TP", "TR", "THESIS", "TIME"):
        reasons.append("G3_ORDER")
    if EXIT_CLOCK != "X1":
        reasons.append("EXIT_CLOCK")
    if type(N_FULL) is not int or N_FULL != 1_922_088_960:
        reasons.append("N_FULL")
    for name, val, banned, adopted in (
        ("R63H_CARD_SHA256", R63H_CARD_SHA256, R63H_R3_SHA256, R63H_ADOPTED_SHA256),
        ("R63I_CARD_SHA256", R63I_CARD_SHA256, R63I_R2_SHA256, R63I_ADOPTED_SHA256),
    ):
        if val is None:
            continue
        if not _is_hex64(val):
            reasons.append("%s is neither None nor 64-hex" % name)
        elif name == "R63H_CARD_SHA256" and val != adopted:
            reasons.append("R63H_CARD_SHA256 is not the adopted r4 sha")
        elif name == "R63I_CARD_SHA256" and val == banned:
            reasons.append("R63I_CARD_SHA256 equals the not-adoptable r2 sha")
        elif name == "R63I_CARD_SHA256" and val != adopted:
            reasons.append("R63I_CARD_SHA256 is not the adopted r3 sha")
    return reasons


_reasons = check_literals()
if _reasons:
    raise SystemExit(EXIT_STOP)
