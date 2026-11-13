# research_fulln480m - INTERFACES (B0) - BTC FULL-N 480,522,240 search, code bundle C1-C9
Author: Coder, batch B0, 14 Nov 2026 AEDT (UTC+11). Contract for parallel batches B1-B8.
Bound to: RULES /workspace/rules-keeper/RULES-R0-R62-20261110.md sha256 d8594afbb02e1e5451ff5d7b71bea32bed8a3eeea0a5ed433d87400a892376de;
GS card r3 0173cd11a090966cab8320d0e6e8fcc79495eae9fe4b48de95e63b334e5f2fb7; RK Route-2 b0eed943d057;
RK card-r3 ruling c07fa806dfad; RK plan-r2 ruling 729f8bc6bd1c (BLOCKER-1 R-a, Q-R1); GS stop spec r3
76dc076cd7718618f98bbc3febed740be8684f14dafdc27c1200a422ddbcadae; HM plan-r2 review 5ce676b48834 (B0 conditions B3/B4/B6); Coder plan r2 42d3c4126dbc.
Card r3 values supersede plan r2 where they differ: K = 728 day blocks, discovery windows W01-W12, fold floor >= 9 of 12,
multipliers (728, 10000) f64. Day sums f64 S_k + u32 n_k = 6,360,367,104 B per tile. 51 B record, fields 5-6 = V_block / t_block under BOTH methods.
STATUS: build evidence only (box stubs, Route-2 cond. 5). Not a PASS, CLEAR, freeze or verdict (R56). Live OFF. Gross only (R61).

## 0. Ground rules every batch obeys
- NEW files only under research_fulln480m/ (shipped to RUN_DIR\code by R62(o)/O7). No existing repo or laptop file is edited.
- Pinned closure modules are imported ONLY through fulln_closure.load_closure(). No `import ci_lag` etc. before that call.
- NO monkey-patching: never assign, delete or wrap an attribute of a pinned module (audit detects replaced
  functions/classes; exit 78). A behaviour change = a new function in a new file (Route-2 cond. 3).
- phase_b_ci_ready, phase_b_launch_20261107d are FORBIDDEN names: never imported (finder + audit -> exit 78).
  phase_b_search and gpu_prod_path are pinned and hashed but NOT in the load set (see Q-B0-1).
- Any violation = raise fulln_closure.ClosureViolation (exit_code 78). Entry points wrap in run_guarded() -> sys.exit(78).
  Catching ClosureViolation to continue is forbidden; the latch (violation_latched()) is re-checked at every boundary.
- No write/append/create/rename/delete under C:\DoxxedCrypto\external\ (audit hook -> 78). Outputs only in RUN_DIR.
- No paper tile can be created or changed: no bundle module may import any repo service / tile / ledger module
  (B8 adds a static import test over every shipped file; see Q-B0-4).
- Box tests: stubs only, no 480M-sized allocation, one bounded pytest per batch, no deletes, no /tmp.

## 1. Shared constants - research_fulln480m/pins.py (import, never redefine)
| Name | Type | Value |
|---|---|---|
| N_CELLS | int | 480_522_240 |
| N_TILES | int | 660 (22 channels x 10 thresholds x 3 horizons; phase_b_run.plan_tiles) |
| TILE_CELLS | int | 728_064 (2 directions x 768 exits x 474 rows) |
| N_CHANNELS, N_THRESHOLDS, N_HORIZONS, N_DIRECTIONS, N_EXITS, N_ROWS | int | 22, 10, 3, 2, 768, 474 |
| K_DAY_BLOCKS | int | 728 (W01-W12 discovery days) |
| WINDOWS | tuple[Window] | WU + W01..W12, half-open UTC (s2) |
| DISCOVERY_WINDOW_IDS | tuple[str] | ("W01",...,"W12") |
| FOLD_FLOOR | int | 9 (of 12) |
| B_REPS | int | 10_000 |
| SEED_B_MULTIPLIERS | int | 20261114 (numpy Generator(PCG64(seed)).standard_normal((728, 10000)) f64) |
| SEED_A_AUDIT | int | 20261115 |
| CSTAR_ORDER_STAT | int | 9_551 (1-based, of the 10,000 rep maxima) |
| RW_TOP | int | 64 |
| ALPHA_FWER | float | 0.05 |
| ALPHA_BONF_ONE_SIDED | float | 1.0405e-10 (= 0.05 / N_CELLS, rounded as in card r3 5.2) |
| PLAIN_T_PRESCREEN | float | 3.0 (A only) |
| KC_MIN_ELIGIBLE | int | 30 |
| ACCUM_HEADER_BYTES | int | 4_096 |
| ACCUM_RECORD_BYTES | int | 51 |
| ACCUM_FILE_BYTES | int | 24_506_638_336 (= 4,096 + N_CELLS x 51) |
| HEARTBEAT_STOP_S | int | 75 |
| DAY_SUM_DTYPE / DAY_COUNT_DTYPE | str | "<f8" / "<u4" (never float32 for values) |
| DAY_SUMS_BYTES_PER_TILE | int | 6_360_367_104 (= 728 x 728,064 x 12) |
| MULTIPLIER_SHAPE / MULTIPLIER_DTYPE / MULTIPLIER_BYTES | | (728, 10000) / "<f8" / 58_240_000 |
| T_CRIT_DF727 | float | 6.4468 (t.isf(1.0405e-10, 727), HM VERIFIED) |
| FLAG_* | int | s3 flags bits |
| STOPSPEC_SHA256 | str | 76dc076cd771... (full 64-hex in pins.py) |
| CHAIN_WALL_S / TILE_WALL_S_B / WINDOW_WALL_S_A | int | 172_800 / 1_800 / 28_800 (time.monotonic, never extended) |
| G_B5_CHAIN_BAR_S | int | 129_600 (36 h = 75% of 48 h; NOT 38.4 h) |
| RUN_DIR_MAX_BYTES | int | 90_004_889_600 |
| OUTPUTS_MAX_BYTES_B / WINDOW_OUTPUT_MAX_BYTES_A / CONTROL_MAX_BYTES | int | 64_424_509_440 / 5_368_709_120 / 1_073_741_824 |
| RUN_FILE_COUNT_MAX | int | 10_000 |
| TREE_WORKINGSET_MAX_BYTES | int | 25_769_803_776 (24 GiB) |
| FREE_SPACE_START_MIN_BYTES / FREE_RAM_START_MIN_BYTES | int | 197_379_072_000 (+ remaining r2c bytes) / 17_179_869_184 (16 GiB) |
| PRIORITY_CLASS / AFFINITY_MASK / N_WORKERS | | "BelowNormal" / 0xFF0 (4080) / 8 |
| BLOCKED_WAIT_UTC | tuple[str,str] | ("23:55:00", "00:10:00") on the UTC clock, never local time (HM B10; in-process wait per RK c07fa item 3) |
| FORBIDDEN_PORTS | tuple | (7002, 9021) |
| EXIT_STOP | int | 78 |
| GROSS_COST_BP | float | 0.0 (fee = funding = slippage = 0, R61) |

## 2. Windows (card r3 s9; half-open UTC; ns since epoch as int64)
`Window(id: str, start_utc: str, end_utc: str, days: int, role: str)`; role in {"warmup","discovery"}.
WU 2024-10-30..2024-11-01 (2, warmup). W01 2024-11-01..2025-01-01 (61), W02 ..2025-03-01 (59), W03 ..2025-05-01 (61),
W04 ..2025-07-01 (61), W05 ..2025-09-01 (62), W06 ..2025-11-01 (61), W07 ..2026-01-01 (61), W08 ..2026-03-01 (59),
W09 ..2026-05-01 (61), W10 ..2026-07-01 (61), W11 ..2026-09-01 (62), W12 ..2026-10-30 (59). Sum W01-W12 = 728 = K.
Day block k (0..727) = UTC day index over the concatenated discovery windows; a trade belongs to the block of its entry bar.

## 3. Accumulator record (C4, B4) - RUN_DIR\accum\pooled-stats.bin, little-endian, struct-of-arrays
Header 4,096 B at offset 0 (layout owned by B4; must carry magic, format version, N 480,522,240, units "bp", lattice sha,
freeze-pack sha, method A|B, rules sha, card sha; zero-padded; stop spec r3 S0). Columns follow, each N_CELLS entries in lattice-index order:
| # (card) | Field | dtype | Offset (bytes) | Content (SAME under A and B) |
|---|---|---|---|---|
| 1 | n_trades | <u4 | 4,096 | trade count |
| 2 | n_unpriced | <u4 | 4,096 + 4N | per-cell unpriceable count, R61(c) pin e9c534ffafc1, COMPUTED (no placeholder) |
| 3 | sum_bp | <f8 | 4,096 + 8N | S |
| 4 | sumsq_bp2 | <f8 | 4,096 + 16N | Q |
| 5 | v_block_bp2 | <f8 | 4,096 + 24N | V_block = sum_k (S_k - n_k S/n)^2; NaN + FLAG_BLOCK_NOT_COMPUTED where not computed (A non-survivor/non-audit) |
| 6 | t_block | <f8 | 4,096 + 32N | t_block = S / sqrt(V_block); NaN + FLAG_BLOCK_NOT_COMPUTED where not computed |
| 7 | max_window_net_bp | <f8 | 4,096 + 40N | max over W01-W12 of window net (gross) |
| 8 | window_pos_mask | <u2 | 4,096 + 48N | bits 0..11 = W01..W12 positive; bits 12-15 = 0 |
| 9 | flags | u1 | 4,096 + 50N | bits below |
End = 4,096 + 51N = 24,506,638,336 B exactly; any other size = STOP 78. Units: bp, bp^2. float64 values, uint32 counts;
variance never from float32. Disk-resident, explicit seek/read/write, no memmap. B: each tile written once + commit line;
A: per-window in-place tile update + per-window commit line with column sha256s (RK c07fa item 5).
flags bits (pins.FLAG_*): 0 HARD_STOP_BREACH and 1 THESIS_CUT_BREACH (COMPUTED per cell - they feed an R59 floor; NO placeholder;
computation route OPEN, HM B6), 2 DATA_THIN_ANY, 3 BLOCK_NOT_COMPUTED, 4 KC_INELIGIBLE (K_c < 30; stays in N and in the max),
5 DEGENERATE (n < 2 or V = 0), 6-7 reserved = 0.
NO SENTINEL is allowed for fields 2, 9 bit 0, 9 bit 1. If the unchanged kernel cannot produce them, the batch STOPs and the question
goes to RK/GS (HM B6, R61(c) Q8); B0 does not define a fallback value.
Tile slice for tile i: entries [i*TILE_CELLS, (i+1)*TILE_CELLS) of every column (TileRecord, s4 B4).
Per-tile day sums (C9, A survivors): S_k <f8 [K, T] + n_k <u4 [K, T] = 6,360,367,104 B per tile (2 resident tiles = 11.85 GiB);
never written to disk beyond one tile (G-B5(iv)).

## 4. Module signatures (numpy arrays; shapes use T = TILE_CELLS, K = K_DAY_BLOCKS, B = B_REPS)
### B0 fulln_closure.py (this batch, built)
- `class ClosureViolation(Exception)`: `.exit_code == 78`, `.reason: str`.
- `violation_latched() -> str | None`; `assert_no_latch() -> None` (raises if latched).
- `run_guarded(fn: Callable[[], T]) -> T` - ClosureViolation or a latch -> `sys.exit(78)`.
- `sha256_bytes(b: bytes) -> str`; `read_pinned(path: str, size: int, sha256: str) -> bytes` (one read-only open; size+sha check).
- `third_party_report() -> dict[str, str]` (normalized dist name -> version, importlib.metadata);
  `freeze_text() -> str`; `freeze_sha256() -> str`; `check_third_party(pinset, require_pinned: bool) -> dict`.
- `install_write_guard(root: str) -> None` (sys.addaudithook; no uninstall API).
- `preflight(pinset, mode: Literal["launch","stub"]) -> dict` - receipt (JSON-able): every module/data pin re-hashed,
  third-party versions + freeze sha, python version, rules/card shas. "launch" refuses closure_complete False or any unpinned third party.
- `load_closure(pinset) -> ClosureState` - meta-path refusing finder first, write guard on, then hash-then-exec each load-set
  module in pins order (spec origin = real path; same bytes hashed and compiled; registered in sys.modules).
- `ClosureState.module(name) -> types.ModuleType`; `assert_kernel_image(state) -> None` (gpu_kernel.kernel_sha256() == pin).
- `audit(state) -> dict` - sys.modules audit + re-hash of every module/data pin + identity check of every pinned function/class + latch.
- `BoundaryAuditor(state)`: `.boundary(label: str) -> dict`, `.count: int`;
  `iter_with_audit(items: Iterable[T], auditor) -> Iterator[T]` (audit before every item and once after the last). B8 MUST drive
  the tile loop through iter_with_audit (or call boundary() at every tile boundary).
- `write_receipt(path: str, receipt: dict, pinset: PinSet | None = None, max_bytes: int = 5_000_000) -> str`
  (new file, exclusive create, refused under the real or pinset external root, returns sha256).
### B1 fulln_gross.py (C1 + C7)
- `build_gross_micro(base_micro: Mapping[str, Any]) -> dict` - cost_bp = 0.0, fund_rate = 0.0, funding-off sentinel; no refusing host function called.
- `assert_gross(micro: Mapping) -> None` - any cost != 0 -> ClosureViolation.
- `unpriced_counts(entry_nofill: np.ndarray[bool], ...) -> np.ndarray[uint32]` - R61(c) pin e9c534ffafc1; unpriceable never a win.
### B2 fulln_correction_a.py (fallback A)
- `plain_t(n: u4[T], s: f8[T], q: f8[T]) -> f8[T]`; `block_t(s_k: f8[K,T], n_k: u4[K,T]) -> tuple[f8[T] t, f8[T] V, u2[T] K_c]`.
- `t_crit(df: int|ndarray, alpha: float = ALPHA_BONF_ONE_SIDED) -> f8` - Student t, df = K_c - 1 ONLY (no z option; HM B13).
  `z_info_column(alpha) -> float` - 6.3552 info column, never a decision input.
- `prescreen(n, s, q, floors) -> bool[T]`; `audit_sample(frame_idx: i8[M], plain_t_frame: f8[M], seed=SEED_A_AUDIT) -> i8[100_000]`;
  `audit_check(sample_stats) -> dict` (STOP rules 1-4 literal, stop spec r3 S1.3; t_block >= 3.0 count = DISCLOSURE column only).
- Method A loop order is window-wise (stop spec r3, HM B11).
### B3a fulln_windows.py (C3 + allowlist)
- `window(id: str) -> Window`; `window_bounds_ns(id) -> tuple[int, int]`; `day_block_index(ts_ns: i8[...]) -> i4[...]` (-1 outside discovery).
- `class AllowlistGate(manifest: Mapping[str, tuple[int, str]])`: `.open(path) -> BinaryIO` (read-only; path+size+sha256 must match; else 78).
- `data_thin(window_id, channel, day_hours: f8[days]) -> bool` (definition PENDING HM/GS; DATA-THIN = non-positive fold, never dropped).
### B3b fulln_loader.py (C5, BLOCKER-1 route R-a, RK 729f8 A1-A6)
- `build_window_panel(window_id: str, gate: AllowlistGate, closure: ClosureState) -> Panel`; `Panel` = dict of numpy arrays, same columns/dtypes as
  pinned build_discovery_panel; line map of re-implemented px/funding readers to the pinned lines; never the runA2 STORE_DIR.
### B4 fulln_accum.py (C4)
- `TileRecord` dataclass: the 9 columns of s3 as numpy arrays of length TILE_CELLS with the s3 dtypes.
- `Accumulator.create(path: str) -> Accumulator` (new file, exact ACCUM_FILE_BYTES, refuses existing path);
  `Accumulator.open(path) -> Accumulator` (size != exact -> 78); `.write_tile(i: int, rec: TileRecord) -> dict` (column sha256s);
  `.read_tile(i) -> TileRecord`; `.commit(line: dict) -> None` (append-only accum\commit.jsonl).
### B5 fulln_maxstat.py (C9, method B)
- `multipliers(k=K_DAY_BLOCKS, b=B_REPS, seed=SEED_B_MULTIPLIERS, family: Literal["gaussian","mammen"]="gaussian") -> f8[K,B]`; `matrix_sha256(m) -> str`.
- `tile_stats(s_k: f8[K,T], n_k: u4[K,T]) -> tuple[f8[T] t_block, f8[T] V, u2[T] K_c]` (float64; G-B3 parity 1e-9).
- Bootstrap product precision OPEN (HM B4): (i) f64 product, or (ii) f32 product with T* tolerance <= 1e-5 relative AND f64
  recompute of every rep max within tolerance of c* and every top-64 entry used by Romano-Wolf. Pinned in plan r3, not here.
- `tile_rep_max(s_k, n_k, e: f8[K,B], cell_offset: int) -> tuple[f8[B] rep_max, i8[B,64] top_ids, f8[B,64] top_vals]`.
- `merge_top(a, b) -> same`; `critical_value(rep_max: f8[B]) -> float` (9,551st order statistic); `romano_wolf(...) -> list[int]`.
- Memory note: one tile's s_k + n_k = 6,360,367,104 B; box tests use tiny T only.
### B6 launcher / preflight / watchdog
- `phase_b_fulln_launch_20261114.main(argv) -> int` (H1 self-set priority/affinity/BLAS env BEFORE numpy, then fulln_closure.preflight(mode="launch"),
  load_closure, assert_kernel_image, card r3 S0 gates); `fulln_watchdog.Watchdog(...)`; `fulln_preflight.run(config) -> dict`.
### Survivor alert (R66(g), card r3 s6A; HM B7): RUN_DIR\results\SURVIVOR-ALERT.json/.txt writer with k from the one frozen method,
k = 0 plain report, no alert from a STOPPED/latched/partial or A-audit-BREACH run; NO paper-tile write path (test + mutant). Batch home: B6/B8.
### P1 preflight script (RK c07fa item 2; HM B8): own batch, read-only, literal L101-L126 + Appendices A-C, writes only new files in RUN_DIR\preflight\.
### B7 stoptest/* + PROCEDURE-G24.md; B8 fulln_chain.run(config) -> int (cell-major: tile outer via iter_with_audit, W01-W12 inner).

## 5. Third-party pins (RK 729f8bc6bd1c Q-R1)
pins.THIRD_PARTY: numpy, pandas, scipy, pyyaml - version string each; pins.PIP_FREEZE_SHA256. All None today (card r3 Appendix C C018 not yet run)
-> preflight(mode="launch") STOPs 78 until filled by a new sha of pins.py. Any version mismatch or freeze-sha mismatch = STOP 78.

## 6. Open design questions (B0)
- Q-B0-1: card r3 C1 says the host driver calls the gpu_prod_path production path; plan r2 s5 lists gpu_prod_path as never imported.
  Today it is pinned+hashed but outside the load set (finder refuses it). Adding it = new pins.py sha. GS/HM/RK.
- Q-B0-2: closure incomplete until HM Appendix C (ext2_search imports, kernel image path); pins.CLOSURE_COMPLETE = False blocks launch mode.
- Q-B0-3: freeze text is computed from importlib.metadata (normalized "name==version" lines, sorted), not the pip CLI output. RK to confirm form.
- Q-B0-4: paper-tile no-write proof: B0 refuses nothing tile-specific; B8 static test over shipped files + audit of sys.modules for repo service modules. GS/HM.
- Q-B0-5: SoA column order (card field order) and flags bits 2-5 placement (s3) need HM's pin; bits 0-1 and field 2 are computed (route OPEN, HM B6).
