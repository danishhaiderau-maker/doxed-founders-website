"""Pins for the BTC full-N 480,522,240 search bundle (batch B0, C8 Route-2).

Literal values only. Changing any value here is a new file sha and needs the
card gates again (RK b0eed943d057 cond. 6). Bound to RULES d8594afbb02e and
GS card r3 0173cd11a090. Card r3 values: K = 728, discovery W01-W12.
"""
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

RULES_SHA256 = "d8594afbb02e1e5451ff5d7b71bea32bed8a3eeea0a5ed433d87400a892376de"
CARD_SHA256 = "0173cd11a090966cab8320d0e6e8fcc79495eae9fe4b48de95e63b334e5f2fb7"
ROUTE2_RULING_SHA256 = "b0eed943d05756cfa8e41db8993079c53416660d20d440bc0dd14b0e45801665"

# ---- family and lattice -------------------------------------------------
N_CHANNELS = 22
N_THRESHOLDS = 10
N_HORIZONS = 3
N_DIRECTIONS = 2
N_EXITS = 768
N_ROWS = 474
N_TILES = 660
TILE_CELLS = 728_064
N_CELLS = 480_522_240

# ---- method pins (card r3 s5) ------------------------------------------
K_DAY_BLOCKS = 728
B_REPS = 10_000
SEED_B_MULTIPLIERS = 20261114
SEED_A_AUDIT = 20261115
CSTAR_ORDER_STAT = 9_551
RW_TOP = 64
ALPHA_FWER = 0.05
ALPHA_BONF_ONE_SIDED = 1.0405e-10
PLAIN_T_PRESCREEN = 3.0
KC_MIN_ELIGIBLE = 30
FOLD_FLOOR = 9
GROSS_COST_BP = 0.0

# ---- accumulator (card r3 s8 C4) --------------------------------------
ACCUM_HEADER_BYTES = 4_096
ACCUM_RECORD_BYTES = 51
ACCUM_FILE_BYTES = 24_506_638_336
ACCUM_COLUMNS = (
    ("n_trades", "<u4"),
    ("n_unpriced", "<u4"),
    ("sum_bp", "<f8"),
    ("sumsq_bp2", "<f8"),
    ("v_block_bp2", "<f8"),  # field 5: V_block under A and B (NaN + FLAG_BLOCK_NOT_COMPUTED if not computed)
    ("t_block", "<f8"),  # field 6: t_block under A and B (NaN + FLAG_BLOCK_NOT_COMPUTED if not computed)
    ("max_window_net_bp", "<f8"),
    ("window_pos_mask", "<u2"),
    ("flags", "u1"),
)

# flags u8 bits (HM 5ce676b48834 B6: bits 0-1 are COMPUTED, never placeholders)
FLAG_HARD_STOP_BREACH = 1 << 0
FLAG_THESIS_CUT_BREACH = 1 << 1
FLAG_DATA_THIN_ANY = 1 << 2
FLAG_BLOCK_NOT_COMPUTED = 1 << 3
FLAG_KC_INELIGIBLE = 1 << 4
FLAG_DEGENERATE = 1 << 5  # n < 2 or V = 0

# per-tile day sums (card r3 s10; HM B4): S_k float64 + n_k uint32, K x TILE_CELLS
DAY_SUM_DTYPE = "<f8"
DAY_COUNT_DTYPE = "<u4"
DAY_SUMS_BYTES_PER_TILE = 6_360_367_104
MULTIPLIER_SHAPE = (728, 10_000)
MULTIPLIER_DTYPE = "<f8"
MULTIPLIER_BYTES = 58_240_000
T_CRIT_DF727 = 6.4468  # t.isf(1.0405e-10, 727), HM VERIFIED; info for tests

# ---- stop spec r3 76dc076cd771 constants -------------------------------
STOPSPEC_SHA256 = "76dc076cd7718618f98bbc3febed740be8684f14dafdc27c1200a422ddbcadae"
CHAIN_WALL_S = 172_800
TILE_WALL_S_B = 1_800
WINDOW_WALL_S_A = 28_800
G_B5_CHAIN_BAR_S = 129_600  # 36 h = 75% of 48 h (card r3 G-B5(ii))
RUN_DIR_MAX_BYTES = 90_004_889_600
OUTPUTS_MAX_BYTES_B = 64_424_509_440
WINDOW_OUTPUT_MAX_BYTES_A = 5_368_709_120
CONTROL_MAX_BYTES = 1_073_741_824
RUN_FILE_COUNT_MAX = 10_000
TREE_WORKINGSET_MAX_BYTES = 25_769_803_776
FREE_SPACE_START_MIN_BYTES = 197_379_072_000
FREE_RAM_START_MIN_BYTES = 17_179_869_184
PRIORITY_CLASS = "BelowNormal"
AFFINITY_MASK = 0xFF0
N_WORKERS = 8
HEARTBEAT_STOP_S = 75
BLOCKED_WAIT_UTC = ("23:55:00", "00:10:00")  # UTC clock, never local time (HM B10)
FORBIDDEN_PORTS = (7002, 9021)
EXIT_STOP = 78


@dataclass(frozen=True)
class Window:
    id: str
    start_utc: str
    end_utc: str
    days: int
    role: str


WINDOWS = (
    Window("WU", "2024-10-30T00:00:00Z", "2024-11-01T00:00:00Z", 2, "warmup"),
    Window("W01", "2024-11-01T00:00:00Z", "2025-01-01T00:00:00Z", 61, "discovery"),
    Window("W02", "2025-01-01T00:00:00Z", "2025-03-01T00:00:00Z", 59, "discovery"),
    Window("W03", "2025-03-01T00:00:00Z", "2025-05-01T00:00:00Z", 61, "discovery"),
    Window("W04", "2025-05-01T00:00:00Z", "2025-07-01T00:00:00Z", 61, "discovery"),
    Window("W05", "2025-07-01T00:00:00Z", "2025-09-01T00:00:00Z", 62, "discovery"),
    Window("W06", "2025-09-01T00:00:00Z", "2025-11-01T00:00:00Z", 61, "discovery"),
    Window("W07", "2025-11-01T00:00:00Z", "2026-01-01T00:00:00Z", 61, "discovery"),
    Window("W08", "2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z", 59, "discovery"),
    Window("W09", "2026-03-01T00:00:00Z", "2026-05-01T00:00:00Z", 61, "discovery"),
    Window("W10", "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z", 61, "discovery"),
    Window("W11", "2026-07-01T00:00:00Z", "2026-09-01T00:00:00Z", 62, "discovery"),
    Window("W12", "2026-09-01T00:00:00Z", "2026-10-30T00:00:00Z", 59, "discovery"),
)
DISCOVERY_WINDOW_IDS = tuple(w.id for w in WINDOWS if w.role == "discovery")

# ---- closure pins (Route-2) --------------------------------------------
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FilePin:
    rel_path: str  # relative to external_root, "/" separated
    size: int
    sha256: str
    provenance: str = ""


@dataclass(frozen=True)
class ModulePin:
    name: str
    rel_path: str
    size: int
    sha256: str
    provenance: str = ""


@dataclass(frozen=True)
class ThirdPartyPin:
    dist: str  # normalized distribution name
    version: Optional[str]  # None = not pinned yet -> launch refuses


@dataclass(frozen=True)
class PinSet:
    external_root: str
    modules: Tuple[ModulePin, ...]  # load set, topological order
    not_loaded: Tuple[ModulePin, ...]  # pinned + hashed, never loaded
    forbidden_names: frozenset
    data_files: Tuple[FilePin, ...]
    kernel_image_sha256: Optional[str]
    third_party: Tuple[ThirdPartyPin, ...]
    pip_freeze_sha256: Optional[str]
    closure_complete: bool
    label: str = field(default="")

    def __post_init__(self):
        names = [m.name for m in self.modules] + [m.name for m in self.not_loaded]
        if len(names) != len(set(names)):
            raise ValueError("duplicate module pin")
        paths = [p.rel_path for p in self.modules + self.not_loaded + self.data_files]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate path pin")
        clash = set(names) & set(self.forbidden_names)
        if clash:
            raise ValueError("forbidden module pinned for loading: %s" % sorted(clash))
        for p in self.modules + self.not_loaded + self.data_files:
            if not _HEX64.match(p.sha256):
                raise ValueError("pin is not 64 lowercase hex: %s" % p.rel_path)
            if p.size <= 0:
                raise ValueError("pin size must be positive: %s" % p.rel_path)
            if p.rel_path.startswith("/") or ".." in p.rel_path.split("/"):
                raise ValueError("pin path must be relative and inside the root")
        if self.kernel_image_sha256 is not None and not _HEX64.match(self.kernel_image_sha256):
            raise ValueError("kernel image pin is not 64 lowercase hex")
        if self.pip_freeze_sha256 is not None and not _HEX64.match(self.pip_freeze_sha256):
            raise ValueError("pip freeze pin is not 64 lowercase hex")

    @property
    def load_names(self):
        return tuple(m.name for m in self.modules)


EXTERNAL_ROOT = "C:\\DoxxedCrypto\\external"
_CODE = "ext2/code/"
_LAPTOP = "laptop Get-FileHash 14 Nov 2026 ~05:55-06:05 AEDT (plan r2 s5)"

CLOSURE_MODULES = (
    ModulePin("ci_lag", _CODE + "ci_lag.py", 2174,
              "2daa870e5f7e16960f753902a34160debfcc4895e4b8f623822ad5ce5eb133ac", _LAPTOP),
    ModulePin("ci_registry", _CODE + "ci_registry.py", 11773,
              "cdaba7f42376ae9cb8df30c848227641903a973cd1385453a77d3290e847bf60", _LAPTOP),
    ModulePin("gpu_kernel", _CODE + "gpu_kernel.py", 52179,
              "4a693762e9603c9b980ce83773199a822f937fdd77a418fcb34e4af728f67172", _LAPTOP),
    ModulePin("ext2_search", _CODE + "ext2_search.py", 119529,
              "6b5956fdc19f4fbc8061a20ed65e7b04e6b65d2039ed202c72535a15cc09cd61", _LAPTOP),
    ModulePin("ci_build", _CODE + "ci_build.py", 32338,
              "2e9f3fec1259cab96c3f4e308db757019197d03ab9fdbce3abbce5d94eeec997", _LAPTOP),
    ModulePin("gpu_eval", _CODE + "gpu_eval.py", 23697,
              "b79f66ffc8238f6084eee73c903e29e6f17b10948b1a48e767d52feccd2910d4", _LAPTOP),
    ModulePin("phase_b_lattice", _CODE + "phase_b_lattice.py", 16971,
              "3ecad6a7f5914d37bf1c3b6c74e138c4b2994ae3791af941282418115bc69185", _LAPTOP),
    ModulePin("phase_b_run", _CODE + "phase_b_run.py", 27039,
              "3ff373b863f4f57702737d7b1709870ece4f5369b49ead6c93258d8331823c2d",
              _LAPTOP + "; box copy partA-phaseb matches"),
    ModulePin("ci_discovery_scores", _CODE + "ci_discovery_scores.py", 27270,
              "3ffa5ea4edcb4cc4e047862af5413a409c8535eeffde419f10eed5cf3882f5aa",
              _LAPTOP + "; argument-taking functions only (RK 729f8 A1)"),
)

CLOSURE_NOT_LOADED = (
    ModulePin("phase_b_search", _CODE + "phase_b_search.py", 27840,
              "02942ff5f93c12c51ddc4044e3f9333e8f4b57f5439324beb70e56db9dfb266d",
              _LAPTOP + "; box copy partA-phaseb matches; prepare() forces costs"),
    ModulePin("gpu_prod_path", _CODE + "gpu_prod_path.py", 18060,
              "b6e0a60ee4ebca3dd3cfc98c976b29acb963c5ba2c050e61a755381a4e3ff1e4",
              _LAPTOP + "; Q-B0-1 (card r3 C1 vs plan r2 s5)"),
)

# Never imported, never in a load set (RK 729f8bc6bd1c A1; quarantined launcher).
FORBIDDEN_MODULE_NAMES = frozenset({"phase_b_ci_ready", "phase_b_launch_20261107d"})

CLOSURE_DATA_FILES = (
    FilePin(_CODE + "configs/phase_b_search.json", 3450,
            "7d5cc18d21a0b4da80bc0dd0cbc0ada6f49e0e13a17e29d2f1411ad1fa8edfb5", _LAPTOP),
    FilePin(_CODE + "configs/phase_b_ci_registration.json", 12705,
            "a120f973fdb6392d20fbb353fe4dd76953efa39c007af470e57309340005c6f3", _LAPTOP),
    FilePin("ext2/reports/phase-b-ci-registration-smoke.json", 5401,
            "eeb60421bbe96150c04c6b6a0bca0b6f8d8e722a79e48399a47ceee6d394090d", _LAPTOP),
    FilePin("ext2/reports/phase-b-gpu-smoke-20261106T201017Z.json", 3796,
            "b6cd3daef06fbe1f35239aa86b80f2c851831a7f051480ff1accb130c2703a44", _LAPTOP),
)

# Embedded PTX image sha (gpu_kernel.kernel_sha256(); expected value from phase_b_lattice L35).
KERNEL_IMAGE_SHA256 = "d094fbb0bc3c447f146c117fbacebd585ae23d9aa1dd6364602879abbcb77887"

# RK 729f8bc6bd1c Q-R1: version string + pip-freeze sha; mismatch = STOP 78.
# None = not yet read (card r3 Appendix C C018) -> launch mode refuses.
THIRD_PARTY = (
    ThirdPartyPin("numpy", None),
    ThirdPartyPin("pandas", None),
    ThirdPartyPin("scipy", None),
    ThirdPartyPin("pyyaml", None),
)
PIP_FREEZE_SHA256 = None

# Appendix C enumeration (ext2_search imports, kernel image path) is still OPEN.
CLOSURE_COMPLETE = False

REAL_PINSET = PinSet(
    external_root=EXTERNAL_ROOT,
    modules=CLOSURE_MODULES,
    not_loaded=CLOSURE_NOT_LOADED,
    forbidden_names=FORBIDDEN_MODULE_NAMES,
    data_files=CLOSURE_DATA_FILES,
    kernel_image_sha256=KERNEL_IMAGE_SHA256,
    third_party=THIRD_PARTY,
    pip_freeze_sha256=PIP_FREEZE_SHA256,
    closure_complete=CLOSURE_COMPLETE,
    label="laptop-real",
)
