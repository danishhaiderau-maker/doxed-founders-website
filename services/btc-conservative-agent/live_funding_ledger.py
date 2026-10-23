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

import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.error
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
    if not isinstance(data, list) or (data and data[0] == "error"):
        raise ReadError("API_ERROR")
    return data


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


def resolve_position_id(entry_fill_ts: float, exit_fill_ts: float, creds=None, post=_post):
    """The ONE Bitfinex position (positions/hist, SYMBOL) whose life matches the real fills; None if not unique."""
    creds = creds or read_credentials()
    if not creds:
        raise ReadError("READ_KEY_MISSING")
    rows = post(POSITIONS_PATH, {"start": int((entry_fill_ts - 3600) * 1000), "end": int((exit_fill_ts + 3600) * 1000),
                                 "limit": 100}, creds)
    return match_position(rows, entry_fill_ts, exit_fill_ts)


def match_position(rows, entry_fill_ts, exit_fill_ts):
    """Pure: (position_id, create_ts, update_ts, position_funding) of the unique SYMBOL position created at the entry fill and last
    updated at the exit fill (+/- POSITION_TOL_S); None when zero or several match."""
    hits = []
    for r in rows or []:
        if not isinstance(r, list) or len(r) < 14 or r[0] != SYMBOL:
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
           paper_funding_usd=None, exit_ts_source="exit_fill_record_at_ts", live_notional_usd=None, key_gate=None) -> dict:
    """Pure decision for one live row. ``position`` = (position_id, create_ts, update_ts[, position_funding]) or None."""
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
           "funding_ts_fallbacks": []}
    if exit_ts_source != "exit_fill_record_at_ts" and b is not None and abs(b - _grid(b)) <= EXIT_TS_FALLBACK_NEAR_S:
        out["funding_ts_fallbacks"] = [f"exit_ts:{exit_ts_source}"]  # a non-fill exit stamp next to a settlement
    if a is None or b is None or b < a:
        out["funding_status"] = "UNVERIFIED_FILL_TS_MISSING"
        return out
    if read_error is not None or ledger_rows is None:
        out["funding_status"] = "UNVERIFIED_LEDGER_READ_FAILED"
        out["funding_readback_error"] = read_error or "NO_READ"
        return out
    parsed = [p for p in (_parse_row(r) for r in ledger_rows) if p]
    fee = [p for p in parsed if p["fee_like"]]
    out["funding_fee_anomaly"] = bool(fee)                     # RK (1): flagged for HM, never applied (R7)
    out["funding_fee_anomaly_ledger_ids"] = [p["id"] for p in fee]
    ev = [p for p in parsed if not p["fee_like"] and p["currency"] == "USTF0" and p["symbol"] == SYMBOL[1:]
          and a - TIE_TOL_S <= p["ts"] <= b + TIE_TOL_S]
    used, statuses, per, problems = [], [], [], []
    for t in crossed:
        near = [p for p in ev if abs(p["ts"] - t) <= TIE_TOL_S]
        if not near:
            statuses.append("NO_ENTRY")
            problems.append("UNVERIFIED_SETTLEMENT_NO_ENTRY")
            continue
        if len(near) > 1:
            statuses.append("AMBIGUOUS")
            problems.append("UNVERIFIED_AMBIGUOUS_ENTRY")
            continue
        p = near[0]
        tied = position is not None and position[1] < t <= position[2] + POSITION_TOL_S
        if tied and live_notional_usd and p["rate"]:
            implied = abs(p["amount"] / p["rate"])                # the notional Bitfinex charged
            tied = 0.8 <= implied / float(live_notional_usd) <= 1.25  # one lot only; a merged position cannot tie
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
               funding_settlement_status=statuses)
    led = sum(p["amount"] for p in used)
    if not problems and position is not None:   # RK 03:38: ledger vs positions/hist must agree, else non-countable
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


def chain_exit_fill(chain: Mapping[str, Any]):
    """(exit ts, source): the Bitfinex exit fill record first; other stamps are fallbacks (funding_ts_fallbacks)."""
    for k in ("exit_fill_record_at_ts", "exit_ack_at_ts", "exit_confirmed_at_ts", "exit_at_ts"):
        v = _f(chain.get(k))
        if v is not None:
            return v, k
    return None, None


class Reader:
    """I/O wrapper. Key gate first (RK 03:38): separate key + permission read-back with no write right, else EVERY row
    is UNVERIFIED (``KEY_GATE:<code>``). A verified read-back is cached for good; a failed one is retried after RETRY_S.
    Requests draw on this key's own GLOBAL_BUDGET; at most MAX_NEW_READS uncached rows per ``begin()``."""
    RETRY_S = 60.0
    MAX_NEW_READS = 5

    def __init__(self, creds_status_fn: Callable = credential_status, post=_post, clock=time.time,
                 budget: RateBudget | None = None):
        self._creds_status_fn, self._raw_post, self._clock = creds_status_fn, post, clock
        self._budget_rl = budget or GLOBAL_BUDGET
        self._cache: dict = {}
        self._budget = self.MAX_NEW_READS
        self._lock = threading.Lock()
        self._gate, self._gate_at, self._gate_final, self._creds = None, None, False, None

    def _post(self, path, body, creds):
        if not self._budget_rl.acquire():
            raise ReadError("RATE_BUDGET_EXHAUSTED")
        return self._raw_post(path, body, creds)

    def startup(self):
        """The key gate. Returns None (OK) or the refusal code; write rights refuse for the process lifetime."""
        now = self._clock()
        if self._gate == "OK":
            return None
        if self._gate_final or (self._gate and self._gate_at is not None and now - self._gate_at < GATE_RETRY_S):
            return self._gate
        creds, code = self._creds_status_fn()
        if creds is not None:
            try:
                code = check_permissions(creds, post=self._post)
                self._gate_final = code is not None
            except ReadError as exc:
                code = "KEY_PERMISSIONS_READ_FAILED:" + str(exc)
        self._gate, self._gate_at, self._creds = (code or "OK"), now, (creds if code is None else None)
        return code

    def begin(self):
        self._budget = self.MAX_NEW_READS
        self.startup()
        return self

    def __call__(self, chain: Mapping[str, Any], paper_funding_usd=None):
        chain = chain or {}
        if not (chain.get("closed") or chain.get("exit_ack_at_ts")):
            return None
        a = _f(chain.get("fill_at_ts"))
        b, src = chain_exit_fill(chain)
        qty, px = _f(chain.get("fill_qty") or chain.get("qty")), _f(chain.get("fill_price"))
        notional = abs(qty * px) if qty and px else None
        kw = dict(paper_funding_usd=paper_funding_usd, exit_ts_source=src, live_notional_usd=notional)
        gate = self.startup()
        if gate:   # the job refuses to read; the row is stamped UNVERIFIED
            return verify(a, b, read_error="KEY_GATE:" + gate, read_at_ts=None, key_gate=gate, **kw)
        key = (str(chain.get("correlation_id") or ""), a, b)
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and (hit[1] is None or now - hit[0] < self.RETRY_S):
                rows, err, pos, at = hit[2]
                return verify(a, b, ledger_rows=rows, read_error=err, read_at_ts=at, position=pos, key_gate="OK", **kw)
            if self._budget <= 0:
                return verify(a, b, read_error="READ_DEFERRED", read_at_ts=None, key_gate="OK", **kw)
            self._budget -= 1
        rows, err, pos = None, None, None
        if a is not None and b is not None:
            try:
                rows = read_funding_ledger(a - TIE_TOL_S, b + TIE_TOL_S, creds=self._creds, post=self._post)
                pos = resolve_position_id(a, b, creds=self._creds, post=self._post)
                pid = chain.get("bitfinex_position_id")
                if pos is not None and pid not in (None, "") and str(pid) != str(pos[0]):
                    pos = None   # the chain names another position: cannot be tied
            except ReadError as exc:
                err = str(exc)
        res = verify(a, b, ledger_rows=rows, read_error=err, read_at_ts=now, position=pos, key_gate="OK", **kw)
        with self._lock:
            self._cache[key] = (now, None if res["funding_verified"] else err or res["funding_status"], (rows, err, pos, now))
        return res


DEFAULT_READER = Reader()
