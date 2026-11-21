"""C4 accumulator for the BTC full-N 480,522,240 search (batch B4).

Binding: plan r3 33f3ec1e0e07 s7 C4 / s9 / s12 B4; interfaces.md (B0 a6fc2d873f24) s3/s4 B4;
HM consult 8b8ec9208448 s3(b) flag-bit table (H-8); RK 2e6b9127bf3e item 3 + addendum 22ee55488e0c;
Boss GO r2 3656382c8a5c; review-fix GO c65ece13d3b2; RK e1d778426447; HM R2 b88d1b1e0653; RULES c342ecf8ec9a (header still records pins.RULES_SHA256 from B0 until the B0 interfaces revision); RK c07fa806dfad item 5 (A per-window commit lines).

File RUN_DIR/accum/pooled-stats.bin: a 4,096 B header + 9 struct-of-arrays columns, 51 B per cell,
exactly 4,096 + 51 N bytes (24,506,638,336 B for N = 480,522,240); any other size = STOP 78.
Commit log RUN_DIR/accum/commit.jsonl: append-only, hash-chained JSON lines.

- Disk-resident; explicit seek/read/write; no memory map; never held in RAM (per-tile slices only).
- [B] each tile written ONCE: begin_tile line, column writes, read-back, commit_tile line with
  the column sha256s of that tile slice.
- [A] window-major: begin_window, in-place tile writes (every tile exactly once), commit_window
  line with the sha256 of every full column (RK c07fa item 5).
- A begin line with no matching commit (crash / STOP mid-unit) or a stop line makes the file
  NON-EVIDENTIAL: open() refuses (78) and nothing repairs it. Resume only if the re-hash equals
  the commit lines; otherwise VOID (78) and a new card.
- Flags u8 (field 9), HM 8b8ec9208448 s3(b): bit 0 HARD_STOP_BREACH, 1 THESIS_CUT_BREACH,
  2 DATA_THIN_ANY, 3 BLOCK_NOT_COMPUTED, 4 KC_INELIGIBLE, 5 N_LT_2, 6 V_ZERO,
  7 BREACH_NOT_EVALUATED. DEGENERATE is NOT a bit: degenerate() = bit5 OR bit6.
  NOTE: B0 pins.FLAG_DEGENERATE (bit 5) predates the HM pin and is NOT used here (open question
  for the B0 interfaces revision).
- No placeholders: fields 5-6 are NaN only together with bit 3, 5 or 6; field 2 (n_unpriced)
  and bits 0-1 must name the pass that produced them (sources); a missing source = STOP 78.
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass

import numpy as np

from research_fulln480m import pins
from research_fulln480m.fulln_closure import ClosureViolation

FORMAT_VERSION = 1
MAGIC = b"FULLN480M-ACCUM\n"  # 16 bytes
ACCUM_NAME = "pooled-stats.bin"
COMMIT_NAME = "commit.jsonl"
UNITS = "bp"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX12_64 = re.compile(r"^[0-9a-f]{12,64}$")
HASH_CHUNK = 1 << 22

# ---- flag bits (HM 8b8ec9208448 s3(b)); no spare bit remains ---------------------------
BIT_HARD_STOP_BREACH = 0
BIT_THESIS_CUT_BREACH = 1
BIT_DATA_THIN_ANY = 2
BIT_BLOCK_NOT_COMPUTED = 3
BIT_KC_INELIGIBLE = 4
BIT_N_LT_2 = 5
BIT_V_ZERO = 6
BIT_BREACH_NOT_EVALUATED = 7
FLAG_TABLE = (
    (BIT_HARD_STOP_BREACH, "HARD_STOP_BREACH"),
    (BIT_THESIS_CUT_BREACH, "THESIS_CUT_BREACH"),
    (BIT_DATA_THIN_ANY, "DATA_THIN_ANY"),
    (BIT_BLOCK_NOT_COMPUTED, "BLOCK_NOT_COMPUTED"),
    (BIT_KC_INELIGIBLE, "KC_INELIGIBLE"),
    (BIT_N_LT_2, "N_LT_2"),
    (BIT_V_ZERO, "V_ZERO"),
    (BIT_BREACH_NOT_EVALUATED, "BREACH_NOT_EVALUATED"),
)
FLAG_HARD_STOP_BREACH = 1 << BIT_HARD_STOP_BREACH
FLAG_THESIS_CUT_BREACH = 1 << BIT_THESIS_CUT_BREACH
FLAG_DATA_THIN_ANY = 1 << BIT_DATA_THIN_ANY
FLAG_BLOCK_NOT_COMPUTED = 1 << BIT_BLOCK_NOT_COMPUTED
FLAG_KC_INELIGIBLE = 1 << BIT_KC_INELIGIBLE
FLAG_N_LT_2 = 1 << BIT_N_LT_2
FLAG_V_ZERO = 1 << BIT_V_ZERO
FLAG_BREACH_NOT_EVALUATED = 1 << BIT_BREACH_NOT_EVALUATED
FLAGS_BREACH = FLAG_HARD_STOP_BREACH | FLAG_THESIS_CUT_BREACH
FLAGS_DEGENERATE_DERIVED = FLAG_N_LT_2 | FLAG_V_ZERO  # predicate mask, not a flag bit
T_PARITY_RTOL = 1e-9  # G-B3 t_block parity (plan r3 s8.3)

COLUMNS = tuple(pins.ACCUM_COLUMNS)  # card field order, dtypes (B0 pins, interfaces.md s3)
COLUMN_NAMES = tuple(c[0] for c in COLUMNS)


def degenerate(flags):
    """Derived DEGENERATE predicate = N_LT_2 OR V_ZERO (HM s3(b)); never stored as a bit."""
    return (np.asarray(flags, np.uint8) & np.uint8(FLAGS_DEGENERATE_DERIVED)) != 0


def _stop(msg):
    raise ClosureViolation("accumulator: " + msg)


@dataclass(frozen=True)
class Layout:
    n_cells: int
    tile_cells: int
    label: str

    def __post_init__(self):
        if self.n_cells <= 0 or self.tile_cells <= 0 or self.n_cells % self.tile_cells:
            _stop("layout n_cells must be a positive multiple of tile_cells")
        if self.label == "real":
            if self.n_cells != pins.N_CELLS or self.tile_cells != pins.TILE_CELLS:
                _stop("real layout must be N_CELLS x TILE_CELLS")
            if self.file_bytes != pins.ACCUM_FILE_BYTES:
                _stop("real layout file size is not ACCUM_FILE_BYTES")
        elif self.label != "test" or self.n_cells >= pins.N_CELLS:
            _stop("layout label must be 'real' or a small 'test' layout")

    @property
    def n_tiles(self):
        return self.n_cells // self.tile_cells

    @property
    def file_bytes(self):
        return pins.ACCUM_HEADER_BYTES + pins.ACCUM_RECORD_BYTES * self.n_cells

    def column_offset(self, name):
        off = pins.ACCUM_HEADER_BYTES
        for cname, dt in COLUMNS:
            if cname == name:
                return off
            off += np.dtype(dt).itemsize * self.n_cells
        _stop("unknown column %r" % (name,))


REAL_LAYOUT_ARGS = (pins.N_CELLS, pins.TILE_CELLS, "real")


def real_layout():
    return Layout(*REAL_LAYOUT_ARGS)


@dataclass
class TileRecord:
    n_trades: np.ndarray
    n_unpriced: np.ndarray
    sum_bp: np.ndarray
    sumsq_bp2: np.ndarray
    v_block_bp2: np.ndarray
    t_block: np.ndarray
    max_window_net_bp: np.ndarray
    window_pos_mask: np.ndarray
    flags: np.ndarray

    def columns(self):
        return [(name, getattr(self, name)) for name in COLUMN_NAMES]


def validate_record(rec, tile_cells, method):
    """Field rules (interfaces.md s3, HM s3(b)). Any breach = ClosureViolation (78)."""
    if not isinstance(rec, TileRecord):
        _stop("record must be a TileRecord")
    for (name, dt) in COLUMNS:
        a = getattr(rec, name)
        if not isinstance(a, np.ndarray) or a.dtype != np.dtype(dt) or a.shape != (tile_cells,):
            _stop("column %s must be %s with shape (%d,)" % (name, dt, tile_cells))
    n = rec.n_trades.astype(np.int64)
    fl = rec.flags
    s, q, v, t = rec.sum_bp, rec.sumsq_bp2, rec.v_block_bp2, rec.t_block
    if not (np.all(np.isfinite(s)) and np.all(np.isfinite(q)) and np.all(np.isfinite(rec.max_window_net_bp))):
        _stop("sum/sumsq/max_window_net must be finite")
    if np.any(q < 0):
        _stop("sumsq_bp2 negative")
    zero = n == 0
    if np.any(s[zero] != 0.0) or np.any(q[zero] != 0.0):
        _stop("n_trades == 0 with non-zero sum/sumsq")
    if np.any(rec.window_pos_mask & np.uint16(0xF000)):
        _stop("window_pos_mask bits 12-15 must be 0")
    b = lambda m: (fl & np.uint8(m)) != 0  # noqa: E731
    if np.any(b(FLAG_BREACH_NOT_EVALUATED) & b(FLAGS_BREACH)):
        _stop("BREACH_NOT_EVALUATED set with bit 0/1 set")
    if np.any(b(FLAG_N_LT_2) != (n < 2)):
        _stop("N_LT_2 must be set exactly when n_trades < 2 (computed from data)")
    if np.any(b(FLAG_V_ZERO) & (n < 2)):
        _stop("V_ZERO requires n_trades >= 2")
    if method == "B" and np.any(b(FLAG_BLOCK_NOT_COMPUTED)):
        _stop("BLOCK_NOT_COMPUTED under method B (STOP 78)")
    v_nan, t_nan = np.isnan(v), np.isnan(t)
    if np.any(v_nan != t_nan):
        _stop("fields 5-6 must be NaN together")
    reason = b(FLAG_BLOCK_NOT_COMPUTED) | b(FLAG_N_LT_2) | b(FLAG_V_ZERO)
    if np.any(v_nan != reason):
        _stop("fields 5-6 NaN exactly when BLOCK_NOT_COMPUTED, N_LT_2 or V_ZERO (no placeholder)")
    dv, dt_ = v[~v_nan], t[~t_nan]
    if not (np.all(np.isfinite(dv)) and np.all(np.isfinite(dt_))):
        _stop("Inf in a defined field 5/6 (STOP)")
    if np.any(dv <= 0.0):
        _stop("defined V_block must be > 0 (V == 0 is V_ZERO with NaN fields)")
    ref = s[~v_nan] / np.sqrt(dv)
    if dv.size and not np.allclose(dt_, ref, rtol=T_PARITY_RTOL, atol=0.0):
        _stop("t_block != sum_bp / sqrt(v_block_bp2) within 1e-9")


def _check_sources(rec, sources):
    if not isinstance(sources, dict):
        _stop("sources must be a dict (no placeholder for field 2 / bits 0-1)")
    need = ["n_unpriced"]
    if np.any((rec.flags & np.uint8(FLAG_BREACH_NOT_EVALUATED)) == 0):
        need.append("breach_bits")
    out = {}
    for key in need:
        src = sources.get(key)
        if (not isinstance(src, dict) or not isinstance(src.get("pass"), str) or not src["pass"]
                or not isinstance(src.get("receipt_sha256"), str) or not _HEX64.match(src["receipt_sha256"])):
            _stop("source for %s missing: it must name its producing pass and receipt sha256 "
                  "(no placeholder; R61(c) I1 / Q-GS1 I2 still OPEN)" % key)
        out[key] = {"pass": src["pass"], "receipt_sha256": src["receipt_sha256"]}
    return out


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _line_sha(line):
    return _sha(line.encode("utf-8"))


def encode_header(fields):
    body = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    raw = MAGIC + body
    if len(raw) > pins.ACCUM_HEADER_BYTES:
        _stop("header too long")
    return raw + b"\0" * (pins.ACCUM_HEADER_BYTES - len(raw))


def decode_header(raw):
    if len(raw) != pins.ACCUM_HEADER_BYTES or not raw.startswith(MAGIC):
        _stop("bad header magic/size")
    rest = raw[len(MAGIC):]
    nl = rest.find(b"\n")
    if nl < 0 or any(rest[nl + 1:]):
        _stop("bad header body / padding not zero")
    try:
        return json.loads(rest[:nl].decode("ascii"))
    except ValueError:
        _stop("header JSON unreadable")


def _header_fields(layout, method, lattice_sha256, freeze_sha256, card_sha256, clear_ref):
    if method not in ("A", "B"):
        _stop("method must be 'A' or 'B'")
    for name, v in (("lattice_sha256", lattice_sha256), ("freeze_sha256", freeze_sha256),
                    ("card_sha256", card_sha256)):
        if not isinstance(v, str) or not _HEX64.match(v):
            _stop("%s must be 64 lowercase hex (no default)" % name)
    if layout.label == "real" and (not isinstance(clear_ref, str) or not _HEX12_64.match(clear_ref)):
        _stop("real accumulator is created only after card RK PASS + Boss CLEAR: clear_ref sha required")
    return {
        "magic": MAGIC.decode("ascii").strip(), "format_version": FORMAT_VERSION,
        "n_cells": layout.n_cells, "tile_cells": layout.tile_cells, "n_tiles": layout.n_tiles,
        "layout": layout.label, "units": UNITS, "method": method,
        "lattice_sha256": lattice_sha256, "freeze_sha256": freeze_sha256,
        "rules_sha256": pins.RULES_SHA256, "card_sha256": card_sha256, "clear_ref": clear_ref,
        "file_bytes": layout.file_bytes, "header_bytes": pins.ACCUM_HEADER_BYTES,
        "record_bytes": pins.ACCUM_RECORD_BYTES,
        "columns": [[n, d, layout.column_offset(n)] for n, d in COLUMNS],
        "flag_bits": [[bit, name] for bit, name in FLAG_TABLE],
        "degenerate": "derived N_LT_2|V_ZERO (not a bit)",
    }


class Accumulator(object):
    """Use Accumulator.create(...) or Accumulator.open(...)."""

    def __init__(self, path, layout, header, lines, last_text=None):
        self.path = path
        self.commit_path = os.path.join(os.path.dirname(path), COMMIT_NAME)
        self.layout = layout
        self.header = header
        self.method = header["method"]
        self._lines = lines  # parsed commit lines
        self._last_text = last_text
        self._window_sources = {}
        self._open_window = None
        self._window_tiles = set()

    # ---------------------------------------------------------------- construction
    @staticmethod
    def _check_path(path):
        path = os.path.abspath(path)
        if os.path.basename(path) != ACCUM_NAME or os.path.basename(os.path.dirname(path)) != "accum":
            _stop("accumulator path must be <RUN_DIR>/accum/%s" % ACCUM_NAME)
        return path

    @classmethod
    def create(cls, path, layout, method, lattice_sha256, freeze_sha256, card_sha256, clear_ref=None):
        path = cls._check_path(path)
        commit = os.path.join(os.path.dirname(path), COMMIT_NAME)
        if os.path.lexists(path) or os.path.lexists(commit):
            _stop("refusing existing accumulator or commit log (new file only)")
        fields = _header_fields(layout, method, lattice_sha256, freeze_sha256, card_sha256, clear_ref)
        raw = encode_header(fields)
        with open(path, "xb") as f:
            f.write(raw)
            f.truncate(layout.file_bytes)
            f.flush()
            os.fsync(f.fileno())
        if os.path.getsize(path) != layout.file_bytes:
            _stop("preallocated size mismatch")
        acc = cls(path, layout, fields, [])
        with open(commit, "x", encoding="utf-8", newline="\n"):
            pass
        acc._append({"event": "create", "header_sha256": _sha(raw), "file_bytes": layout.file_bytes})
        return acc

    @classmethod
    def open(cls, path, layout):
        path = cls._check_path(path)
        if not os.path.isfile(path):
            _stop("accumulator missing")
        size = os.path.getsize(path)
        if size != layout.file_bytes:
            _stop("size %d != exact %d (STOP 78)" % (size, layout.file_bytes))
        with open(path, "rb") as f:
            raw = f.read(pins.ACCUM_HEADER_BYTES)
        header = decode_header(raw)
        if header.get("n_cells") != layout.n_cells or header.get("tile_cells") != layout.tile_cells \
                or header.get("layout") != layout.label or header.get("file_bytes") != layout.file_bytes \
                or header.get("format_version") != FORMAT_VERSION or header.get("rules_sha256") != pins.RULES_SHA256 \
                or header.get("method") not in ("A", "B"):
            _stop("header does not match layout / format / rules")
        lines, last_text = cls._read_lines(os.path.join(os.path.dirname(path), COMMIT_NAME))
        if not lines or lines[0].get("event") != "create" or lines[0].get("header_sha256") != _sha(raw):
            _stop("commit log create line missing or header sha mismatch (VOID)")
        acc = cls(path, layout, header, lines, last_text)
        acc._verify_resume()
        return acc

    @staticmethod
    def _read_lines(commit):
        if not os.path.isfile(commit):
            _stop("commit log missing (VOID)")
        out, prev, last = [], None, None
        with open(commit, "r", encoding="utf-8", newline="\n") as f:
            text = f.read()
        if text and not text.endswith("\n"):
            _stop("commit log has a torn last line (NON-EVIDENTIAL)")
        for i, line in enumerate(text.splitlines()):
            try:
                d = json.loads(line)
            except ValueError:
                _stop("commit log line %d unreadable (VOID)" % i)
            if d.get("seq") != i or d.get("prev_sha256") != prev:
                _stop("commit log chain broken at line %d (VOID)" % i)
            prev = _line_sha(line)
            last = line
            out.append(d)
        return out, last

    def _append(self, entry):
        prev = _line_sha(self._last_text) if self._lines else None
        entry = dict(entry, seq=len(self._lines), prev_sha256=prev)
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        with open(self.commit_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._lines.append(entry)
        self._last_text = line

    # ---------------------------------------------------------------- state
    def _events(self, name):
        return [d for d in self._lines if d.get("event") == name]

    def committed_tiles(self):
        return sorted(d["tile"] for d in self._events("commit_tile"))

    def committed_windows(self):
        return [d["window"] for d in self._events("commit_window")]

    def _verify_resume(self):
        if self._events("stop"):
            _stop("file was STOPPED (NON-EVIDENTIAL; no repair)")
        last = self._lines[-1]
        if last.get("event") in ("begin_tile", "begin_window"):
            _stop("crash/STOP mid-unit: %s with no commit (NON-EVIDENTIAL; no repair)" % last["event"])
        for d in self._lines[1:]:
            if d.get("method") != self.method:
                _stop("commit line method mismatch (VOID)")
        if self.method == "B":
            seen = set()
            for d in self._events("commit_tile"):
                if d["tile"] in seen:
                    _stop("tile committed twice (VOID)")
                seen.add(d["tile"])
                if self._tile_hashes(d["tile"]) != d["columns"]:
                    _stop("re-hash of tile %d != commit line (VOID; new card)" % d["tile"])
        else:
            wins = self._events("commit_window")
            if wins:
                if self._full_hashes() != wins[-1]["columns"]:
                    _stop("re-hash != last commit_window line (VOID; new card)")
            elif self._full_hashes() != self._zero_hashes():
                _stop("data present with no committed window (VOID)")

    # ---------------------------------------------------------------- hashing / io
    def _slice(self, name, tile):
        dt = np.dtype(dict(COLUMNS)[name])
        off = self.layout.column_offset(name) + tile * self.layout.tile_cells * dt.itemsize
        return off, self.layout.tile_cells * dt.itemsize, dt

    def _tile_hashes(self, tile):
        out = {}
        with open(self.path, "rb") as f:
            for name in COLUMN_NAMES:
                off, nbytes, _ = self._slice(name, tile)
                f.seek(off)
                out[name] = _sha(f.read(nbytes))
        return out

    def _full_hashes(self):
        out = {}
        with open(self.path, "rb") as f:
            for name, dt in COLUMNS:
                off = self.layout.column_offset(name)
                left = np.dtype(dt).itemsize * self.layout.n_cells
                h = hashlib.sha256()
                f.seek(off)
                while left:
                    chunk = f.read(min(HASH_CHUNK, left))
                    if not chunk:
                        _stop("short read while hashing")
                    h.update(chunk)
                    left -= len(chunk)
                out[name] = h.hexdigest()
        return out

    def _zero_hashes(self):
        out = {}
        for name, dt in COLUMNS:
            left = np.dtype(dt).itemsize * self.layout.n_cells
            h = hashlib.sha256()
            while left:
                k = min(HASH_CHUNK, left)
                h.update(b"\0" * k)
                left -= k
            out[name] = h.hexdigest()
        return out

    def _write_columns(self, tile, rec):
        shas = {}
        with open(self.path, "r+b") as f:
            for name, arr in rec.columns():
                off, nbytes, dt = self._slice(name, tile)
                data = np.ascontiguousarray(arr, dtype=dt).tobytes()
                if len(data) != nbytes:
                    _stop("slice size mismatch")
                f.seek(off)
                f.write(data)
                shas[name] = _sha(data)
            f.flush()
            os.fsync(f.fileno())
        if os.path.getsize(self.path) != self.layout.file_bytes:
            _stop("file size changed during write (STOP 78)")
        if self._tile_hashes(tile) != shas:
            _stop("read-back of tile %d differs from the bytes written" % tile)
        return shas

    def _check_tile_index(self, tile):
        if isinstance(tile, bool) or not isinstance(tile, int) or not 0 <= tile < self.layout.n_tiles:
            _stop("tile index out of range")

    # ---------------------------------------------------------------- method B
    def write_tile(self, tile, rec, sources):
        """[B] write-once per tile + commit line with column sha256s."""
        if self.method != "B":
            _stop("write_tile is method B only; A uses begin_window/write_tile_inplace/commit_window")
        self._check_tile_index(tile)
        if tile in self.committed_tiles():
            _stop("tile %d already written (write-once)" % tile)
        validate_record(rec, self.layout.tile_cells, "B")
        src = _check_sources(rec, sources)
        self._append({"event": "begin_tile", "tile": tile, "method": "B"})
        shas = self._write_columns(tile, rec)
        self._append({"event": "commit_tile", "tile": tile, "method": "B", "columns": shas, "sources": src})
        return shas

    # ---------------------------------------------------------------- method A
    def begin_window(self, window_id):
        if self.method != "A":
            _stop("begin_window is method A only")
        if self._open_window is not None:
            _stop("window %s still open" % self._open_window)
        done = self.committed_windows()
        ids = pins.DISCOVERY_WINDOW_IDS
        if len(done) >= len(ids) or window_id != ids[len(done)]:
            _stop("windows must be committed in order W01..W12, each once (next %s)"
                  % (ids[len(done)] if len(done) < len(ids) else "none"))
        self._append({"event": "begin_window", "window": window_id, "method": "A"})
        self._open_window = window_id
        self._window_tiles = set()
        self._window_sources = {}

    def write_tile_inplace(self, tile, rec, sources):
        if self.method != "A" or self._open_window is None:
            _stop("write_tile_inplace needs method A and an open window")
        self._check_tile_index(tile)
        if tile in self._window_tiles:
            _stop("tile %d written twice in window %s" % (tile, self._open_window))
        validate_record(rec, self.layout.tile_cells, "A")
        src = _check_sources(rec, sources)
        self._write_columns(tile, rec)
        self._window_tiles.add(tile)
        self._window_sources[str(tile)] = src

    def commit_window(self, window_id):
        if self._open_window is None or window_id != self._open_window:
            _stop("commit_window for a window that is not open")
        if self._window_tiles != set(range(self.layout.n_tiles)):
            _stop("window %s incomplete: %d of %d tiles written"
                  % (window_id, len(self._window_tiles), self.layout.n_tiles))
        self._append({"event": "commit_window", "window": window_id, "method": "A",
                      "columns": self._full_hashes(), "tiles": self.layout.n_tiles,
                      "sources": self._window_sources})
        self._open_window = None
        self._window_tiles = set()
        self._window_sources = {}

    # ---------------------------------------------------------------- shared
    def read_tile(self, tile):
        self._check_tile_index(tile)
        if self.method == "B" and tile not in self.committed_tiles():
            _stop("tile %d not committed (no read of unwritten bytes)" % tile)
        cols = {}
        with open(self.path, "rb") as f:
            for name in COLUMN_NAMES:
                off, nbytes, dt = self._slice(name, tile)
                f.seek(off)
                b = f.read(nbytes)
                if len(b) != nbytes:
                    _stop("short read")
                cols[name] = np.frombuffer(b, dtype=dt).copy()
        return TileRecord(**cols)

    def stop(self, reason):
        """Record a STOP: the file becomes NON-EVIDENTIAL for good (no repair, no resume)."""
        self._append({"event": "stop", "reason": str(reason), "method": self.method})
        self._open_window = None
        _stop("STOP recorded: %s (file NON-EVIDENTIAL)" % reason)
