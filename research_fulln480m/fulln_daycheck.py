"""C5b DAYCHECK TSV producer (batch B3c).

Binding: Coder plan r3 33f3ec1e0e07 s7 C5b / s12 B3c ("per-window daycheck rows (path, bytes, sha12, rows,
first/last ts, hours covered) for the freeze pack from P1 receipts + the loader's row pass; ext2_search Inputs
is fed only the bundle's own TSV; tests: TSV of a synthetic store, mismatch -> 78"); RK 22ee55488e0c (no
hard-coded staleness bound before the GS pin - this module has none); HM consult 8b8ec9208448; RULES
d8594afbb02e.

Two outputs, each a NEW file (exclusive create), UTF-8, LF, never under C:\\DoxxedCrypto\\external:
- the ext2_search-compatible daycheck TSV: the header the pinned reader expects (ext2_search load_manifests
  L563-L570 reads day_utc, trades_file_sha256_12, candles1m_file_sha256_12; the other columns follow the
  pinned A2 producer make_daycheck_a2.py a5f16e00e694), one row per Bitfinex UTC day;
- the coverage TSV (window, channel, day, path, bytes, sha12, rows, first/last ts, hours covered) for the
  freeze pack.
This module reads NO data file: sizes/shas come from the P1 receipt, rows/ts/hours from the loader row pass.
Any disagreement between the two = ClosureViolation (exit 78). Box build evidence only (R56). Live OFF.
"""
import hashlib
import math
import ntpath
import os

from research_fulln480m import pins
from research_fulln480m.fulln_closure import ClosureViolation

# ext2_search reader + pinned A2 producer column order (make_daycheck_a2.py hdr, verbatim).
DAYCHECK_HEADER = ("day_utc", "trades_file_sha256_12", "trade_rows", "dup_ids", "ts_monotonic",
                   "max_trade_gap_min", "candles1m_file_sha256_12", "bars", "bar_minutes_match_trade_minutes",
                   "status")
COVERAGE_HEADER = ("window_id", "channel", "day_utc", "path", "bytes", "sha256_12", "rows", "first_ts_ms",
                   "last_ts_ms", "hours_covered")
MAX_GAP_S = 3600  # ext2_search bfx_day / make_daycheck_a2: gaps.max() <= 3600 s for PASS (pinned literal)
BFX_DIR = "C:\\DoxxedCrypto\\external\\ext2\\external-data\\bitfinex\\tBTCF0USTF0"
P1_RECEIPT_SCHEMA = "fulln-p1-preflight-receipt-v1"

_ROW_KEYS = ("path", "bytes", "sha256", "rows", "first_ts_ms", "last_ts_ms", "hours_covered")
_BFX_KEYS = ("day_utc", "trades", "candles1m", "dup_ids", "ts_monotonic", "inside_day", "max_trade_gap_s",
             "bars", "bar_minutes_match", "status")


def _norm(p):
    if not isinstance(p, str) or not p:
        raise ClosureViolation("path must be a non-empty string")
    if "\\" in p or (len(p) >= 2 and p[1] == ":"):
        return ntpath.normpath(p).casefold()
    return os.path.normpath(p).casefold()


def _hex64(h):
    return isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def files_from_p1_receipt(receipt):
    """{norm path: (length, sha256)} from a P1 receipt dict (BP schema fulln-p1-preflight-receipt-v1)."""
    if not isinstance(receipt, dict) or receipt.get("schema") != P1_RECEIPT_SCHEMA:
        raise ClosureViolation("not a P1 preflight receipt (schema %r)" % (receipt.get("schema") if isinstance(receipt, dict) else None))
    if receipt.get("stops"):
        raise ClosureViolation("P1 receipt records a STOP - not usable")
    out = {}
    for f in receipt.get("files", []):
        p, n, h = f.get("path"), f.get("length"), f.get("sha256")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0 or not _hex64(h):
            raise ClosureViolation("bad P1 file entry: %r" % (f,))
        k = _norm(p)
        if k in out:
            raise ClosureViolation("duplicate P1 file entry: %s" % p)
        out[k] = (n, h)
    if not out:
        raise ClosureViolation("P1 receipt lists no files")
    return out


def _check_file(entry, p1_files):
    for k in _ROW_KEYS:
        if k not in entry:
            raise ClosureViolation("row-pass entry missing %s" % k)
    k = _norm(entry["path"])
    if k not in p1_files:
        raise ClosureViolation("row-pass file not in the P1 receipt: %s" % entry["path"])
    size, sha = p1_files[k]
    if entry["bytes"] != size:
        raise ClosureViolation("bytes mismatch %s: row pass %r != P1 %d" % (entry["path"], entry["bytes"], size))
    if entry["sha256"] != sha:
        raise ClosureViolation("sha256 mismatch %s: row pass %s != P1 %s" % (entry["path"], str(entry["sha256"])[:12], sha[:12]))
    for f in ("rows", "first_ts_ms", "last_ts_ms"):
        if not isinstance(entry[f], int) or isinstance(entry[f], bool):
            raise ClosureViolation("%s must be int: %s" % (f, entry["path"]))
    h = entry["hours_covered"]
    if not isinstance(h, float) or not math.isfinite(h) or h < 0.0 or h > 24.0:
        raise ClosureViolation("hours_covered must be a float in [0, 24]: %s" % entry["path"])
    return sha


def _day_epoch_ms(day):
    import calendar
    y, m, d = int(day[0:4]), int(day[5:7]), int(day[8:10])
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        raise ClosureViolation("bad day %r" % day)
    return calendar.timegm((y, m, d, 0, 0, 0, 0, 0, 0)) * 1000


def window_days(window_id):
    w = next((x for x in pins.WINDOWS if x.id == window_id), None)
    if w is None:
        raise ClosureViolation("unknown window %r" % window_id)
    e0 = _day_epoch_ms(w.start_utc[:10])
    import datetime
    return [(datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=e0 + i * 86_400_000)).strftime("%Y-%m-%d")
            for i in range(w.days)]


def _status(r):
    """Pinned PASS rule (ext2_search bfx_day): dup == 0, monotonic, inside day, minutes match, rows > 0, gap <= 3600 s."""
    ok = (r["dup_ids"] == 0 and r["ts_monotonic"] is True and r["inside_day"] is True and r["bar_minutes_match"] is True
          and r["trades"]["rows"] > 0 and r["max_trade_gap_s"] <= MAX_GAP_S)
    return "PASS" if ok else "FAIL"


def _fmt_bool(b):
    return "True" if b else "False"


def build_rows(window_id, p1_files, bfx_rows, channel_rows=()):
    """Validate the row pass against the P1 receipt and build (daycheck_rows, coverage_rows) for one window.

    bfx_rows: one dict per UTC day of the window (_BFX_KEYS; 'trades'/'candles1m' are file entries with
    _ROW_KEYS). channel_rows: (channel, day_utc, file entry) for every other channel file the loader read.
    Every window day must be present exactly once; any missing day / extra day / mismatch -> 78.
    """
    days = window_days(window_id)
    by_day = {}
    for r in bfx_rows:
        for k in _BFX_KEYS:
            if k not in r:
                raise ClosureViolation("Bitfinex row-pass entry missing %s" % k)
        if r["day_utc"] in by_day:
            raise ClosureViolation("duplicate day %s" % r["day_utc"])
        by_day[r["day_utc"]] = r
    if sorted(by_day) != days:
        missing = sorted(set(days) - set(by_day))
        extra = sorted(set(by_day) - set(days))
        raise ClosureViolation("window %s day set mismatch: missing %s extra %s" % (window_id, missing[:5], extra[:5]))
    dc, cov = [], []
    for d in days:
        r = by_day[d]
        for feed in ("trades", "candles1m"):
            exp = _norm(ntpath.join(BFX_DIR, feed, d + ".jsonl.gz"))
            if _norm(r[feed]["path"]) != exp:
                raise ClosureViolation("unexpected %s path for %s: %s" % (feed, d, r[feed]["path"]))
        st = _status(r)
        if r["status"] != st:
            raise ClosureViolation("row-pass status %r != recomputed %r for %s" % (r["status"], st, d))
        if r["candles1m"]["rows"] != r["bars"]:
            raise ClosureViolation("bars != candles1m rows for %s" % d)
        t_sha = _check_file(r["trades"], p1_files)
        c_sha = _check_file(r["candles1m"], p1_files)
        e0 = _day_epoch_ms(d)
        for feed in ("trades", "candles1m"):
            f = r[feed]
            if f["rows"] > 0 and not (e0 <= f["first_ts_ms"] <= f["last_ts_ms"] < e0 + 86_400_000) and r["inside_day"]:
                raise ClosureViolation("first/last ts outside %s but inside_day claimed" % d)
        gap_min = round(float(r["max_trade_gap_s"]) / 60, 2)
        dc.append((d, t_sha[:12], str(r["trades"]["rows"]), str(r["dup_ids"]), _fmt_bool(r["ts_monotonic"]),
                   repr(gap_min), c_sha[:12], str(r["bars"]), "yes" if r["bar_minutes_match"] else "no", st))
        for feed in ("trades", "candles1m"):
            f = r[feed]
            cov.append((window_id, "bitfinex/" + feed, d, f["path"], str(f["bytes"]), f["sha256"][:12], str(f["rows"]),
                        str(f["first_ts_ms"]), str(f["last_ts_ms"]), repr(f["hours_covered"])))
    for ch, d, f in channel_rows:
        if not isinstance(ch, str) or not ch or d not in days:
            raise ClosureViolation("channel row outside window %s: %r %r" % (window_id, ch, d))
        sha = _check_file(f, p1_files)
        cov.append((window_id, ch, d, f["path"], str(f["bytes"]), sha[:12], str(f["rows"]), str(f["first_ts_ms"]),
                    str(f["last_ts_ms"]), repr(f["hours_covered"])))
    return dc, cov


def tsv_bytes(header, rows):
    for r in rows:
        if len(r) != len(header):
            raise ClosureViolation("row width != header width")
        for v in r:
            if "\t" in v or "\n" in v or "\r" in v:
                raise ClosureViolation("TAB/CR/LF inside a TSV field")
    text = "\t".join(header) + "\n" + "".join("\t".join(r) + "\n" for r in rows)
    return text.encode("utf-8")


def assert_output_path(path):
    """Outputs never go under C:\\DoxxedCrypto\\external (ext2_search is fed only the bundle's own TSV)."""
    if _norm(path).startswith(_norm(pins.EXTERNAL_ROOT)):
        raise ClosureViolation("daycheck output under the external root refused: %s" % path)


def write_new(path, data, max_bytes=5_000_000):
    """Exclusive create (no overwrite), fsync; refused under the external root."""
    assert_output_path(path)
    if len(data) > max_bytes:
        raise ClosureViolation("TSV larger than %d B" % max_bytes)
    try:
        with open(path, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        raise ClosureViolation("output exists; no overwrite: %s" % path)
    return hashlib.sha256(data).hexdigest()


def produce(window_id, p1_receipt, bfx_rows, channel_rows, daycheck_path, coverage_path):
    """Validate and write both TSVs for one window; returns {daycheck_sha256, daycheck_sha12, coverage_sha256, rows}."""
    p1 = files_from_p1_receipt(p1_receipt)
    dc, cov = build_rows(window_id, p1, bfx_rows, channel_rows)
    a = tsv_bytes(DAYCHECK_HEADER, dc)
    b = tsv_bytes(COVERAGE_HEADER, cov)
    ha = write_new(daycheck_path, a)
    hb = write_new(coverage_path, b)
    return {"window_id": window_id, "daycheck_sha256": ha, "daycheck_sha12": ha[:12], "coverage_sha256": hb,
            "daycheck_rows": len(dc), "coverage_rows": len(cov)}


def parse_daycheck(data):
    """Re-read a produced TSV the way ext2_search load_manifests does (header split on TAB, dict per row)."""
    lines = data.decode("utf-8").split("\n")
    if lines[-1] != "":
        raise ClosureViolation("TSV must end with LF")
    hdr = lines[0].split("\t")
    return [dict(zip(hdr, ln.split("\t"))) for ln in lines[1:-1]]
