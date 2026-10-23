"""Live-row funding read-back from the Bitfinex FUNDING ledger (#849, RK ruling 24 Oct: PASS with 4 amendments).

The paper twin keeps the settlement-exact model (bot.py ``_paper_funding_v2`` -> ``tile_fill.funding_fields``,
unchanged). A LIVE row's funding is the truth Bitfinex booked: this module reads it back and decides whether it is
proven.

RK (1)  Only category-29 ("derivatives funding event") ledger rows are read, with the READ-ONLY key
        (BFX_FUNDING_READ_KEY/SECRET, a separate Fly-only key; the relay / trading key is never used). The key and secret
        are never logged or returned. No trading fee is added or modelled (R7): a fee-like row found in the read
        (wrong category / description) is flagged ``funding_fee_anomaly`` with its ledger ids for HM, never applied.
RK (2)  Zero funding is real and countable when no settlement fell between the real entry and exit FILL times, proven
        from the schedule the paper pricer uses (``tile_fill.settlements_crossed``, bot.py:32778). A missing ledger
        entry alone is not FUNDING_UNVERIFIED.
RK (3)  ``UNVERIFIED_*`` (non-countable) only when the ledger read fails, an entry cannot be tied to the position and
        the settlement time, or a settlement fell in the window with no entry. A paper-vs-live mismatch never excludes
        the row: the ledger value is the truth and the gap is stored as ``funding_live_minus_paper_usd``.
RK (4)  Every read-back is stamped with ``funding_readback_at_ts`` and the ledger ids it used.

Real category-29 row (read-only probe 24 Oct): ``[ID, 'USTF0', 'margin', MTS, None, AMOUNT, BALANCE, None,
'Funding Event BTCF0:USTF0 (0.00004827) on wallet margin']`` - amount at [5], description at [8], MTS ~4 s after the
00/08/16 UTC settlement. The description carries no position id, so an entry is tied to this position by: the
position's Bitfinex symbol, MTS within ``TIE_TOL_S`` of a crossed settlement, and the Bitfinex position (positions/hist,
one position per pair) being open across that settlement. Sign: ledger amount > 0 = received; ``funding_usd_raw`` is
COST-positive (= -sum(amounts)), the paper convention.
"""
from __future__ import annotations

import decimal
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

import tile_fill

LIVE_FUNDING_MODEL = "LIVE_LEDGER_V1"
LEDGER_CATEGORY_FUNDING = 29
LEDGER_PATH = "v2/auth/r/ledgers/hist"  # probe-confirmed (24 Oct); rows filtered to USTF0 below
POSITIONS_PATH = "v2/auth/r/positions/hist"
API_BASE = "https://api.bitfinex.com/"
SYMBOL = "tBTCF0:USTF0"
# RK 24 Oct 03:38: a SEPARATE read-only key, Fly secret only, under its own name (never the relay's / trading key)
READ_KEY_ENVS = (("BFX_FUNDING_READ_KEY", "BFX_FUNDING_READ_SECRET"),)
OTHER_KEY_ENVS = ("BITFINEX_API_KEY", "BFX_API_KEY", "BITFINEX_READ_API_KEY", "BFX_READ_API_KEY", "BITFINEX_TRADING_API_KEY",
                  "RELAY_BITFINEX_API_KEY")  # the funding key must differ from every one of these
PERMISSIONS_PATH = "v2/auth/r/permissions"
GLOBAL_MAX_REQ_PER_MIN = 12   # this key's own conservative process-wide cap (Bitfinex REST ~90/min); never the relay's
GATE_RETRY_S = 300.0          # a failed permission READ is retried at most this often; write rights refuse for good
POSITION_FUNDING_TOL_USD = 1e-6
NONCE_MS_SCALE = 10_000      # scripts/self_aware/fees.py: never ahead of the shared nonce scale
TIE_TOL_S = 120.0            # ledger MTS vs settlement instant (observed +3..+5 s)
POSITION_TOL_S = 10.0        # positions/hist create/update vs the real entry/exit fill ts
EXIT_TS_FALLBACK_NEAR_S = 60.0
LEDGER_LIMIT = 500
# RK zero-rate ruling (24 Oct): a crossed settlement with NO ledger entry counts as 0 only when the rate of that EXACT
# settlement, read back from the public derivatives status history, is exactly 0 as returned AND (RK amendment 04:07)
# positions/hist FUNDING for the pid equals the sum of the tied ledger entries within ZERO_POSITION_FUNDING_TOL.
# Read in the background job, same budget, never in a GET.
API_PUB = "https://api-pub.bitfinex.com/"
DERIV_HIST_PATH = "v2/status/deriv/" + SYMBOL + "/hist"
DERIV_NEXT_FUNDING_TS_IDX = 7     # NEXT_FUNDING_EVT_TIMESTAMP_MS
DERIV_CURRENT_FUNDING_IDX = 11    # CURRENT_FUNDING (paper FUNDING_SOURCE_V2: last row before the settlement)
RATE_WINDOW_BEFORE_S = 300
RATE_WINDOW_AFTER_S = 120
ZERO_POSITION_FUNDING_TOL = 1e-8  # |positions/hist FUNDING| vs |sum of tied ledger entries| (RK amendment 04:07)
RATE_RETRY_MAX = 5                # a row whose rate read failed is re-read at most this many times
_FUNDING_DESC = re.compile(r"^Funding Event\s+(\S+)\s+\(([-0-9.eE+]+)\)")
_FEE_DESC = re.compile(r"fee|commission|rebate", re.I)

_nonce_lock = threading.Lock()
_last_nonce = 0


class ReadError(Exception):
    """A redacted, value-free read failure code (never carries key, secret, signature or response body)."""


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def credential_status(env: Mapping[str, str] | None = None):
    """``((key, secret, env_name), None)`` from BFX_FUNDING_READ_KEY/SECRET only, else ``(None, code)``:
    READ_KEY_MISSING, or READ_KEY_NOT_SEPARATE when it equals the relay / trading / any other configured key."""
    env = os.environ if env is None else env
    for k, s in READ_KEY_ENVS:
        if env.get(k) and env.get(s):
            if any(env.get(o) and hmac.compare_digest(str(env.get(o)), str(env[k])) for o in OTHER_KEY_ENVS):
                return None, "READ_KEY_NOT_SEPARATE"
            return (env[k], env[s], k), None
    return None, "READ_KEY_MISSING"


def read_credentials(env: Mapping[str, str] | None = None):
    return credential_status(env)[0]


def check_permissions(creds, post=None):
    """RK 03:38: the key's own permission read-back (v2/auth/r/permissions -> [[scope, read, write], ...]). Returns
    None when it has NO write right on any scope (orders, wallets/transfer, withdraw, positions, funding, settings ...),
    else ``KEY_HAS_WRITE:<scopes>``. An unreadable / malformed answer raises ReadError (-> refuse)."""
    rows = (post or _post)(PERMISSIONS_PATH, {}, creds)
    if not rows or not all(isinstance(r, list) and len(r) >= 3 and isinstance(r[0], str) for r in rows):
        raise ReadError("PERMISSIONS_UNREADABLE")
    writes = sorted(r[0] for r in rows if r[2] not in (0, False, "0", None))
    return ("KEY_HAS_WRITE:" + ",".join(writes)) if writes else None


class RateBudget:
    """Process-wide sliding-minute cap for THIS key only (the relay runs on Railway with its own key and budget)."""

    def __init__(self, per_min: int = GLOBAL_MAX_REQ_PER_MIN, clock=time.monotonic):
        self.per_min, self._clock, self._hits, self._lock = per_min, clock, [], threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            now = self._clock()
            self._hits = [t for t in self._hits if now - t < 60.0]
            if len(self._hits) >= self.per_min:
                return False
            self._hits.append(now)
            return True


GLOBAL_BUDGET = RateBudget()


def _nonce() -> str:
    global _last_nonce
    with _nonce_lock:
        _last_nonce = max(int(time.time() * 1000) * NONCE_MS_SCALE, _last_nonce + 1)
        return str(_last_nonce)


def _post(path: str, body: dict, creds, timeout: float = 8.0):
    key, secret = creds[0], creds[1]
    raw = json.dumps(body, separators=(",", ":"))
    nonce = _nonce()
    sig = hmac.new(secret.encode(), f"/api/{path}{nonce}{raw}".encode(), hashlib.sha384).hexdigest()
    req = urllib.request.Request(API_BASE + path, data=raw.encode(), method="POST", headers={
        "Content-Type": "application/json", "bfx-nonce": nonce, "bfx-apikey": key, "bfx-signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        raise ReadError(f"HTTP_{exc.code}") from None
    except Exception as exc:  # noqa: BLE001 - redacted: only the exception class name leaves this function
        raise ReadError(f"TRANSPORT_{type(exc).__name__}") from None
    if isinstance(data, list) and data and data[0] == "error" and "ratelimit" in str(data[-1]).lower():
        raise ReadError("HTTP_429")   # Bitfinex 'ratelimit: error' body -> the job backs off
    if not isinstance(data, list) or (data and data[0] == "error"):
        raise ReadError("API_ERROR")
    return data


def _get_public(path: str, params: dict, timeout: float = 8.0):
    """Unauthenticated GET on the public API (no key). Floats parse as Decimal so a tiny rate never rounds to 0."""
    req = urllib.request.Request(API_PUB + path + "?" + urllib.parse.urlencode(params), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode(), parse_float=decimal.Decimal)
    except urllib.error.HTTPError as exc:
        raise ReadError(f"HTTP_{exc.code}") from None
    except Exception as exc:  # noqa: BLE001 - redacted
        raise ReadError(f"TRANSPORT_{type(exc).__name__}") from None
    if isinstance(data, list) and data and data[0] == "error" and "ratelimit" in str(data[-1]).lower():
        raise ReadError("HTTP_429")
    if not isinstance(data, list) or (data and data[0] == "error"):
        raise ReadError("API_ERROR")
    return data


def _exact_zero(v) -> bool:
    """True only for a value that IS 0 as returned (int 0 or Decimal 0); a float / string / None is never inferred."""
    return (isinstance(v, decimal.Decimal) or (isinstance(v, int) and not isinstance(v, bool))) and v == 0


def read_settlement_rate(t: float, get=_get_public) -> dict:
    """The funding rate Bitfinex applied at the EXACT settlement ``t`` (epoch s), from the derivatives status history:
    CURRENT_FUNDING of the last row before t whose NEXT_FUNDING_EVT_TIMESTAMP_MS == t, confirmed by a row at/after t
    whose next-funding ts has flipped past t. Never taken from a neighbouring settlement. Returns
    ``{rate, row_mts, endpoint, error}``; ``rate`` is the value as returned (Decimal/int, never rounded). Only HTTP_429
    is raised (the job backs off); other read failures are returned as a redacted error code."""
    t_ms = int(round(float(t) * 1000))
    out = {"rate": None, "row_mts": None, "endpoint": API_PUB + DERIV_HIST_PATH, "error": None}
    try:
        rows = get(DERIV_HIST_PATH, {"start": t_ms - RATE_WINDOW_BEFORE_S * 1000, "end": t_ms + RATE_WINDOW_AFTER_S * 1000,
                                     "limit": 100, "sort": 1})
    except ReadError as exc:
        if str(exc) in ("HTTP_429", "NOT_OWNER_THREAD"):
            raise
        out["error"] = str(exc)
        return out
    good = sorted((r for r in rows if isinstance(r, list) and len(r) > DERIV_CURRENT_FUNDING_IDX
                   and isinstance(r[0], int) and not isinstance(r[0], bool)), key=lambda r: r[0])
    before = [r for r in good if r[0] < t_ms]
    pre = before[-1] if before else None
    if pre is None or pre[DERIV_NEXT_FUNDING_TS_IDX] != t_ms:
        out["error"] = "SETTLEMENT_ROW_MISSING"
        return out
    nxt = pre[DERIV_NEXT_FUNDING_TS_IDX]
    if not any(isinstance(r[DERIV_NEXT_FUNDING_TS_IDX], int) and r[DERIV_NEXT_FUNDING_TS_IDX] > nxt
               for r in good if r[0] >= t_ms):
        out["error"] = "SETTLEMENT_FLIP_MISSING"
        return out
    out["row_mts"] = pre[0]
    rate = pre[DERIV_CURRENT_FUNDING_IDX]
    if rate is None:
        out["error"] = "RATE_NULL"
        return out
    out["rate"] = rate
    return out


def _rate_text(v):
    """The rate as returned, as text (Decimal in fixed notation, so 0.00000000 stays 0.00000000 - never a float)."""
    if v is None:
        return None
    return format(v, "f") if isinstance(v, decimal.Decimal) else str(v)


def _funding_events(ledger_rows, a, b):
    parsed = [p for p in (_parse_row(r) for r in ledger_rows or []) if p]
    ev = [p for p in parsed if not p["fee_like"] and p["currency"] == "USTF0" and p["symbol"] == SYMBOL[1:]
          and a - TIE_TOL_S <= p["ts"] <= b + TIE_TOL_S]
    return parsed, ev


def no_entry_settlements(a, b, ledger_rows) -> list:
    """Crossed settlements in (a, b] with no ledger entry near them: the only ones whose rate is read back."""
    _parsed, ev = _funding_events(ledger_rows, a, b)
    return [t for t in tile_fill.settlements_crossed(a, b) if not any(abs(p["ts"] - t) <= TIE_TOL_S for p in ev)]


def read_funding_ledger(start_ts: float, end_ts: float, creds=None, post=_post) -> list:
    """Category-29 USTF0 ledger rows with MTS in [start_ts, end_ts] (epoch s). Raises ReadError (redacted)."""
    creds = creds or read_credentials()
    if not creds:
        raise ReadError("READ_KEY_MISSING")
    rows = post(LEDGER_PATH, {"category": LEDGER_CATEGORY_FUNDING, "start": int(start_ts * 1000),
                              "end": int(end_ts * 1000) + 1, "limit": LEDGER_LIMIT}, creds)
    if len(rows) >= LEDGER_LIMIT:
        raise ReadError("LEDGER_PAGE_FULL")  # an incomplete read is a failed read
    return rows


def resolve_position_id(entry_fill_ts: float, exit_fill_ts: float, creds=None, post=_post, position_id=None):
    """The ONE Bitfinex position (positions/hist, SYMBOL) whose life matches the real fills; None if not unique.
    With the chain's ``bitfinex_position_id`` that id is required AND its window is still checked (HM A)."""
    creds = creds or read_credentials()
    if not creds:
        raise ReadError("READ_KEY_MISSING")
    rows = post(POSITIONS_PATH, {"start": int((entry_fill_ts - 3600) * 1000), "end": int((exit_fill_ts + 3600) * 1000),
                                 "limit": 100}, creds)
    return match_position(rows, entry_fill_ts, exit_fill_ts, position_id=position_id)


def match_position(rows, entry_fill_ts, exit_fill_ts, position_id=None):
    """Pure: (position_id, create_ts, update_ts, position_funding) of the unique SYMBOL position created at the entry fill and last
    updated at the exit fill (+/- POSITION_TOL_S); None when zero or several match. HM (A): a known
    ``position_id`` (the chain's bitfinex_position_id) narrows the candidates to that id but NEVER skips the window
    check against the row's real fills - an id whose life does not match the fills is unresolved (None)."""
    hits = []
    want = None if position_id in (None, "") else str(position_id)
    for r in rows or []:
        if not isinstance(r, list) or len(r) < 14 or r[0] != SYMBOL:
            continue
        if want is not None and str(r[11]) != want:
            continue
        c, u = _f(r[12]), _f(r[13])
        if c is None or u is None:
            continue
        c, u = c / 1000.0, u / 1000.0
        if abs(c - entry_fill_ts) <= POSITION_TOL_S and abs(u - exit_fill_ts) <= POSITION_TOL_S:
            hits.append((r[11], c, u, _f(r[4])))   # [4] = the position's FUNDING (cross-check vs the ledger)
    return hits[0] if len(hits) == 1 else None


def _parse_row(r):
    if not isinstance(r, list) or len(r) < 9:
        return None
    mts, amt = _f(r[3]), _f(r[5])
    if mts is None or amt is None:
        return None
    desc = str(r[8] or "")
    m = _FUNDING_DESC.match(desc)
    return {"id": r[0], "currency": r[1], "wallet": r[2], "ts": mts / 1000.0, "amount": amt, "desc": desc,
            "symbol": m.group(1) if m else None, "rate": _f(m.group(2)) if m else None,
            "fee_like": bool(_FEE_DESC.search(desc)) or not m}


def _grid(ts: float) -> float:
    """The nearest 00/08/16 UTC settlement instant (same grid as tile_fill.settlements_crossed)."""
    return round(ts / tile_fill.FUNDING_INTERVAL_S) * tile_fill.FUNDING_INTERVAL_S


def verify(entry_fill_ts, exit_fill_ts, *, ledger_rows=None, read_error=None, read_at_ts=None, position=None,
           paper_funding_usd=None, exit_ts_source="exit_fill_record_at_ts", live_notional_usd=None, key_gate=None,
           settlement_rates=None) -> dict:
    """Pure decision for one live row. ``position`` = (position_id, create_ts, update_ts[, position_funding]) or None.
    ``settlement_rates`` = {settlement ts: read_settlement_rate(...) + read_at_ts} for the no-entry settlements."""
    a, b = _f(entry_fill_ts), _f(exit_fill_ts)
    crossed = tile_fill.settlements_crossed(a, b) if a is not None and b is not None else []
    out = {"funding_model": LIVE_FUNDING_MODEL, "funding_status": None, "funding_verified": False,
           "funding_usd_raw": None, "funding_ts_crossed": crossed, "funding_ledger_ids": [], "funding_ledger_mts": [],
           "funding_ledger_amounts": [], "funding_usd_by_settlement": [], "funding_settlement_status": [],
           "funding_position_id": position[0] if position else None, "funding_readback_at_ts": read_at_ts,
           "funding_readback_endpoint": f"{LEDGER_PATH} category={LEDGER_CATEGORY_FUNDING}",
           "funding_readback_error": read_error, "funding_fee_anomaly": False, "funding_fee_anomaly_ledger_ids": [],
           "funding_paper_usd": _f(paper_funding_usd), "funding_live_minus_paper_usd": None,
           "funding_exit_ts_source": exit_ts_source, "funding_key_gate": key_gate,
           "funding_position_funding": (position[3] if position and len(position) > 3 else None),
           "funding_ts_fallbacks": [], "funding_settlement_rate_readbacks": []}
    if exit_ts_source != "exit_fill_record_at_ts" and b is not None and abs(b - _grid(b)) <= EXIT_TS_FALLBACK_NEAR_S:
        out["funding_ts_fallbacks"] = [f"exit_ts:{exit_ts_source}"]  # a non-fill exit stamp next to a settlement
    if a is None:   # HM (E): no real entry fill ts -> explicit fallback flag, never a guessed window (non-countable)
        out["funding_ts_fallbacks"] = out["funding_ts_fallbacks"] + ["entry_ts:fill_at_ts_missing"]
    if b is None:
        out["funding_ts_fallbacks"] = out["funding_ts_fallbacks"] + ["exit_ts:exit_fill_missing"]
    if a is None or b is None or b < a:
        out["funding_status"] = "UNVERIFIED_FILL_TS_MISSING"
        return out
    if read_error is not None or ledger_rows is None:
        out["funding_status"] = "UNVERIFIED_LEDGER_READ_FAILED"
        out["funding_readback_error"] = read_error or "NO_READ"
        return out
    parsed, ev = _funding_events(ledger_rows, a, b)
    fee = [p for p in parsed if p["fee_like"]]
    out["funding_fee_anomaly"] = bool(fee)                     # RK (1): flagged for HM, never applied (R7)
    out["funding_fee_anomaly_ledger_ids"] = [p["id"] for p in fee]
    used, statuses, per, problems, rate_proof = [], [], [], [], []
    rates = settlement_rates or {}
    for t in crossed:
        near = [p for p in ev if abs(p["ts"] - t) <= TIE_TOL_S]
        # RK zero-rate ruling (amended 04:07): every crossed settlement needs EXACTLY ONE proof - a tied ledger entry, or
        # (only when there is no entry) an exact-0 rate read for that settlement. An entry present means the rate read is
        # neither needed nor used (no double count); a rate read can never rescue an untied / ambiguous entry.
        if not near:
            st, prob = _zero_rate_status(t, rates.get(t), position)
            statuses.append(st)
            rec = rates.get(t) or {}
            rate_proof.append({"settlement_ts": t, "rate": _rate_text(rec.get("rate")),
                               "row_mts": rec.get("row_mts"), "read_at_ts": rec.get("read_at_ts"),
                               "endpoint": rec.get("endpoint") or API_PUB + DERIV_HIST_PATH,
                               "error": rec.get("error") if rates.get(t) is not None else "NOT_READ", "status": st})
            if prob:
                problems.append(prob)
            else:
                per.append({"settlement_ts": t, "ledger_id": None, "ledger_mts": None, "funding_usd": 0.0,
                            "zero_rate_row_mts": rec.get("row_mts")})
            continue
        if len(near) > 1:
            statuses.append("AMBIGUOUS")
            problems.append("UNVERIFIED_AMBIGUOUS_ENTRY")
            continue
        p = near[0]
        tied = position is not None and position[1] < t <= position[2] + POSITION_TOL_S
        if tied:   # HM (A): the notional check is mandatory - missing qty / price / rate -> UNTIED, never skipped
            notional, rate = _f(live_notional_usd), p["rate"]
            if not notional or not rate:
                tied = False
            else:
                implied = abs(p["amount"] / rate)                 # the notional Bitfinex charged
                tied = 0.8 <= implied / notional <= 1.25          # one lot only; a merged position cannot tie
        if not tied:
            statuses.append("UNTIED")
            problems.append("UNVERIFIED_ENTRY_UNTIED" if position is not None else "UNVERIFIED_POSITION_UNRESOLVED")
            continue
        used.append(p)
        statuses.append("LEDGER")
        per.append({"settlement_ts": t, "ledger_id": p["id"], "ledger_mts": p["ts"], "funding_usd": round(-p["amount"], 10)})
    # an entry for this symbol inside the hold that sits on no settlement instant cannot be tied (RK 3);
    # entries of settlements outside (entry, exit] belong to another position and are ignored
    stray = [p for p in ev if a < p["ts"] <= b and abs(p["ts"] - _grid(p["ts"])) > TIE_TOL_S]
    if stray:
        problems.append("UNVERIFIED_ENTRY_UNTIED")
    out.update(funding_ledger_ids=[p["id"] for p in used], funding_ledger_mts=[p["ts"] for p in used],
               funding_ledger_amounts=[p["amount"] for p in used], funding_usd_by_settlement=per,
               funding_settlement_status=statuses, funding_settlement_rate_readbacks=rate_proof)
    led = sum(p["amount"] for p in used)
    zero_idx = [i for i, st in enumerate(statuses) if st == "ZERO_RATE"]
    if not problems and zero_idx:
        # RK amended condition 2: positions/hist FUNDING for the pid must EQUAL the sum of the tied ledger entries within
        # 1e-8 (no entries -> ~0). Magnitudes compared: the positions/hist FUNDING sign is not yet observed live.
        pf = position[3] if position is not None and len(position) > 3 else None
        if pf is None or not abs(abs(pf) - abs(led)) <= ZERO_POSITION_FUNDING_TOL:
            for i in zero_idx:
                statuses[i] = "ZERO_RATE_POSITION_CHECK"
            for rp in rate_proof:
                if rp["status"] == "ZERO_RATE":
                    rp["status"] = "ZERO_RATE_POSITION_CHECK"
            problems.append("UNVERIFIED_ZERO_RATE_POSITION_CHECK")
    elif not problems and position is not None:   # RK 03:38: ledger vs positions/hist must agree, else non-countable
        pf = position[3] if len(position) > 3 else None
        if pf is None and crossed:
            problems.append("UNVERIFIED_POSITION_FUNDING_MISSING")
        elif pf is not None and abs(abs(pf) - abs(led)) > POSITION_FUNDING_TOL_USD + 1e-3 * abs(led):
            problems.append("UNVERIFIED_LEDGER_POSITION_MISMATCH")   # magnitudes: positions/hist sign not yet observed
    if problems:
        out["funding_status"] = problems[0]
        return out
    live = round(-led, 10) if used else 0.0   # RK (2): no settlement -> a proven 0
    out.update(funding_status="PRICED" if crossed else "NO_CROSSING", funding_verified=True, funding_usd_raw=live)
    if out["funding_paper_usd"] is not None:                      # RK (3): realism metric, never an exclusion
        out["funding_live_minus_paper_usd"] = round(live - out["funding_paper_usd"], 10)
    return out


def _zero_rate_status(t, rec, position):
    """(settlement status, problem or None) for a crossed settlement with no ledger entry."""
    if rec is None or (rec.get("error") and rec["error"] not in ("SETTLEMENT_ROW_MISSING", "SETTLEMENT_FLIP_MISSING",
                                                                "RATE_NULL")):
        return "RATE_READ_FAILED", "UNVERIFIED_ZERO_RATE_READ_FAILED"
    if rec.get("error") or rec.get("rate") is None:
        return "RATE_MISSING", "UNVERIFIED_ZERO_RATE_MISSING"
    if not _exact_zero(rec["rate"]):
        return "RATE_NONZERO", "UNVERIFIED_SETTLEMENT_NO_ENTRY"      # a non-zero rate must have booked an entry
    if position is None:   # the positions/hist cross-check (sum of tied entries, in verify) needs the pid
        return "ZERO_RATE_POSITION_CHECK", "UNVERIFIED_ZERO_RATE_POSITION_CHECK"
    return "ZERO_RATE", None


def chain_exit_fill(chain: Mapping[str, Any]):
    """(exit ts, source): the Bitfinex exit fill record first; other stamps are fallbacks (funding_ts_fallbacks)."""
    for k in ("exit_fill_record_at_ts", "exit_ack_at_ts", "exit_confirmed_at_ts", "exit_at_ts"):
        v = _f(chain.get(k))
        if v is not None:
            return v, k
    return None, None


def _row_inputs(chain: Mapping[str, Any]):
    """(entry fill ts, exit fill ts, exit source, notional, chain position id, cache key) - pure."""
    a = _f(chain.get("fill_at_ts"))
    b, src = chain_exit_fill(chain)
    qty, px = _f(chain.get("fill_qty") or chain.get("qty")), _f(chain.get("fill_price"))
    notional = abs(qty * px) if qty and px else None
    pid = chain.get("bitfinex_position_id")
    pid = None if pid in (None, "") else str(pid)
    return a, b, src, notional, pid, (str(chain.get("correlation_id") or ""), a, b, pid or "")


def _closed(chain: Mapping[str, Any]) -> bool:
    return bool(chain.get("closed") or chain.get("exit_ack_at_ts"))


class FundingReadbackJob:
    """HM (B)/(C) 24 Oct: the ONLY place this module touches the network. One owner thread runs ``run_once`` every
    TICK_S: key gate (RK 03:38 permission read-back) first, then per queued row the ledger read and the positions/hist
    read, each drawing one slot of this key's process-wide GLOBAL_BUDGET (12/min, thread-safe); an HTTP 429 / Bitfinex
    'ratelimit' answer stops the tick and backs off exponentially (BACKOFF_BASE_S .. BACKOFF_MAX_S). Results are cached
    per row (correlation id, fill window, position id), stamped with the read time and the ledger ids (RK 4).

    GET handlers only call ``lookup`` - a dict read under a lock that never performs I/O: a row not read yet is
    ``PENDING_READBACK`` (non-countable); a refused key gate stamps every row UNVERIFIED (``KEY_GATE:<code>``)."""
    TICK_S = 5.0
    RETRY_S = 60.0              # a failed row read is re-queued after this
    BACKOFF_BASE_S = 60.0
    BACKOFF_MAX_S = 900.0
    MAX_PENDING = 5000

    def __init__(self, creds_status_fn: Callable = credential_status, post=_post, clock=time.time,
                 budget: RateBudget | None = None, chains_fn: Callable | None = None, get=_get_public):
        self._creds_status_fn, self._raw_post, self._clock, self._raw_get = creds_status_fn, post, clock, get
        self._budget_rl = budget or GLOBAL_BUDGET
        self.chains_fn = chains_fn
        self._lock = threading.Lock()
        self._pending: dict = {}     # key -> work record (owner thread fills it)
        self._cache: dict = {}       # key -> finished read-back
        self._gate, self._gate_at, self._gate_final, self._creds = None, None, False, None
        self._backoff_until, self._backoff_n = 0.0, 0
        self._thread, self._owner, self._stop = None, None, threading.Event()
        self.requests_made = 0

    # ---- GET side: pure cache reads, never any I/O -------------------------------------------------------------
    def lookup(self, chain: Mapping[str, Any], paper_funding_usd=None):
        chain = chain or {}
        if not _closed(chain):
            return None
        a, b, src, notional, pid, key = _row_inputs(chain)
        kw = dict(paper_funding_usd=paper_funding_usd, exit_ts_source=src, live_notional_usd=notional)
        if a is None or b is None or b < a:
            return verify(a, b, read_error="NO_READ", key_gate=self._gate, **kw)   # HM (E) flags, non-countable
        now = self._clock()
        with self._lock:
            gate, hit = self._gate, self._cache.get(key)
            if gate in (None, "OK") and self._stale_locked(hit, now):
                self._enqueue_locked(key, a, b, pid, hit)
        if hit is not None and hit["error"] is None:
            return self._result(hit, a, b, kw)
        if gate not in (None, "OK"):     # the job refuses to read: every row is UNVERIFIED
            return verify(a, b, read_error="KEY_GATE:" + gate, read_at_ts=None, key_gate=gate, **kw)
        if hit is not None:
            return self._result(hit, a, b, kw)
        out = verify(a, b, read_error="PENDING_READBACK", read_at_ts=None, key_gate=gate or "PENDING", **kw)
        out["funding_status"] = "PENDING_READBACK"   # not read yet by the background job -> non-countable
        return out

    __call__ = lookup

    @staticmethod
    def _result(hit, a, b, kw):
        return verify(a, b, ledger_rows=hit["rows"], read_error=hit["error"], read_at_ts=hit["read_at_ts"],
                      position=hit["position"], key_gate="OK", settlement_rates=hit.get("rates"), **kw)

    def _stale_locked(self, hit, now) -> bool:
        if hit is None:
            return True
        if now - hit["read_at_ts"] < self.RETRY_S:
            return False
        return bool(hit["error"]) or (hit.get("rate_failed") and hit.get("attempts", 1) <= RATE_RETRY_MAX)

    def submit(self, chain: Mapping[str, Any]) -> None:
        """Queue a closed row for the owner thread (in-memory only)."""
        chain = chain or {}
        if not _closed(chain):
            return
        a, b, _src, _n, pid, key = _row_inputs(chain)
        if a is None or b is None or b < a:
            return
        with self._lock:
            hit = self._cache.get(key)
            if self._stale_locked(hit, self._clock()):
                self._enqueue_locked(key, a, b, pid, hit)

    def _enqueue_locked(self, key, a, b, pid, hit=None):
        if key not in self._pending and len(self._pending) < self.MAX_PENDING:
            self._pending[key] = {"a": a, "b": b, "pid": pid, "rows": None, "read_at_ts": None, "pos_done": False,
                                  "position": None, "rates": None, "attempts": (hit or {}).get("attempts", 0) + 1}

    # ---- owner side: the single thread that performs every network call ----------------------------------------
    def start(self) -> bool:
        """Start the single owner thread (idempotent). Returns True when this call started it."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="live-funding-readback", daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _loop(self):
        self._owner = threading.get_ident()
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - the job must survive; nothing secret is in scope of a log here
                pass
            self._stop.wait(self.TICK_S)

    def _spend(self):
        if self._owner is not None and threading.get_ident() != self._owner:
            raise ReadError("NOT_OWNER_THREAD")   # single owner: no other thread may spend this key's budget
        if not self._budget_rl.acquire():
            raise _BudgetEmpty()
        self.requests_made += 1

    def _post(self, path, body, creds):
        self._spend()
        return self._raw_post(path, body, creds)

    def _get(self, path, params):
        """The public deriv-status read (RK zero-rate ruling): same owner thread, same budget, same 429 backoff."""
        self._spend()
        return self._raw_get(path, params)

    def _backoff(self, now):
        self._backoff_n += 1
        self._backoff_until = now + min(self.BACKOFF_MAX_S, self.BACKOFF_BASE_S * (2 ** (self._backoff_n - 1)))

    def gate_status(self):
        return self._gate

    def run_once(self) -> None:
        now = self._clock()
        if now < self._backoff_until:
            return
        if callable(self.chains_fn):
            try:
                for ch in self.chains_fn() or []:
                    self.submit(ch)
            except Exception:  # noqa: BLE001 - a broken chain source reads nothing this tick
                pass
        try:
            if not self._startup(now):
                return
            while True:
                with self._lock:
                    key = next(iter(self._pending), None)
                    work = self._pending.get(key) if key is not None else None
                if work is None:
                    return
                self._advance(key, work, now)
        except _BudgetEmpty:
            return
        except ReadError as exc:
            if str(exc) == "HTTP_429":
                self._backoff(now)
                return
            raise

    def _startup(self, now) -> bool:
        """The key gate. True when reads may run. Write rights refuse for the process lifetime."""
        if self._gate == "OK":
            return True
        if self._gate_final or (self._gate and self._gate_at is not None and now - self._gate_at < GATE_RETRY_S):
            return False
        creds, code = self._creds_status_fn()
        if creds is not None:
            try:
                code = check_permissions(creds, post=self._post)
                self._gate_final = code is not None
            except ReadError as exc:
                if str(exc) == "HTTP_429":
                    raise
                code = "KEY_PERMISSIONS_READ_FAILED:" + str(exc)
        self._backoff_n = 0
        with self._lock:
            self._gate, self._gate_at, self._creds = (code or "OK"), now, (creds if code is None else None)
        return code is None

    def _advance(self, key, work, now):
        """One row, resumable across ticks (a budget stop keeps what was already read)."""
        try:
            if work["rows"] is None:
                work["rows"] = read_funding_ledger(work["a"] - TIE_TOL_S, work["b"] + TIE_TOL_S, creds=self._creds,
                                                   post=self._post)
                work["read_at_ts"] = self._clock()
                self._backoff_n = 0
            if not work["pos_done"]:
                work["position"] = resolve_position_id(work["a"], work["b"], creds=self._creds, post=self._post,
                                                       position_id=work["pid"])
                work["pos_done"] = True
                self._backoff_n = 0
            if work["rates"] is None:
                work["rates"] = {}
            if work["position"] is not None:   # unresolved position -> the zero-rate cross-check cannot pass: no read
                for t in no_entry_settlements(work["a"], work["b"], work["rows"]):
                    if t not in work["rates"]:
                        rec = read_settlement_rate(t, get=self._get)   # raises only HTTP_429 / budget / owner
                        rec["read_at_ts"] = self._clock()
                        work["rates"][t] = rec
                        self._backoff_n = 0
            self._finish(key, work, None)
        except ReadError as exc:
            if str(exc) in ("HTTP_429", "NOT_OWNER_THREAD"):
                raise
            self._finish(key, work, str(exc))

    def _finish(self, key, work, error):
        rates = None if error else (work.get("rates") or {})
        rec = {"rows": None if error else work["rows"], "position": None if error else work["position"],
               "error": error, "read_at_ts": work["read_at_ts"] or self._clock(), "rates": rates,
               "rate_failed": bool(rates) and any(r.get("error") for r in rates.values()),
               "attempts": work.get("attempts", 1)}
        with self._lock:
            self._cache[key] = rec
            self._pending.pop(key, None)


class _BudgetEmpty(Exception):
    """This key's minute budget is spent: stop the tick, keep the queue (never an error stamped on a row)."""


DEFAULT_JOB = FundingReadbackJob()
