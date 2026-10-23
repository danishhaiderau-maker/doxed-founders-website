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
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

import tile_fill

log = logging.getLogger("live_funding_ledger")

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
NONCE_MS_SCALE = 10_000      # scripts/self_aware/fees.py: never ahead of the shared nonce scale
TIE_TOL_S = 120.0            # ledger MTS vs settlement instant (observed +3..+5 s)
POSITION_TOL_S = 10.0        # positions/hist create/update vs the real entry/exit fill ts
EXIT_FILL_SOURCE = "exit_fill_record_at_ts"   # HM R4 F4: the ONLY countable exit ts source (no near-settlement window)
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
ZERO_POSITION_FUNDING_TOL = 1e-8  # = SIGNED_MATCH_TOL_USD (RK 04:07 + 04:09, HM R4 F2: one 1e-8 tolerance everywhere)
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


# ---- RK ruling 24 Oct 04:09 + HM R4 F1/F2 + RK 04:12: the SIGNED match and its sign pin --------------------------
# A size-only (abs(pf) vs abs(led)) match is a VIOLATION (R17). The sign relation between positions/hist [4] FUNDING
# ("Funding amount", docs silent on sign) and the ledger [5] AMOUNT ("Amount changed", a signed balance delta) is
# UNPROVEN: POSITION_FUNDING_SIGN = None. A row's FUNDING cross-check passes iff (signed, no abs on either side)
#   (i)  |pf| <= 1e-8 AND |led| <= 1e-8 (both zero: no sign needed), or
#   (ii) the sign is ACTIVE (+1 / -1, from the persisted pin state) AND |pf - sign * led| <= 1e-8.
# Otherwise UNVERIFIED_POSITION_FUNDING_SIGN_UNPROVEN (sign unset, led != 0) or UNVERIFIED_LEDGER_POSITION_MISMATCH /
# UNVERIFIED_ZERO_RATE_POSITION_CHECK (sign set, or led == 0 so no sign can match). Same rule on the zero-rate path and
# the tied-only path, same 1e-8 tolerance (F2: no relative tolerance).
#
# The pin is NOT circular. The background job PROPOSES it (never activates it) from ONE real closed live position with a
# SINGLE tied non-zero category-29 entry, only when three INDEPENDENT checks agree:
#   (a) the entry's sign == the sign of that ledger row's BALANCE change: BALANCE [6] minus the previous BALANCE of the
#       same currency + wallet, both from the ledger itself (an unfiltered read ending at the entry MTS);
#   (b) the entry's sign == who pays whom from the PUBLISHED settlement rate (deriv status hist, the zero-rate read) and
#       the position side. Bitfinex: "a positive funding rate requires contract buyers - aka long traders - to pay the
#       contract sellers - or short traders. The opposite is true for a negative funding rate"
#       (https://blog.bitfinex.com/education/why-choose-bitfinex-to-trade-crypto-derivatives-futures/ , section
#       "Perpetual settlements"). So rate > 0: LONG pays (entry < 0), SHORT receives (entry > 0); rate < 0: reversed;
#       a non-zero entry at a published rate of exactly 0 contradicts the rate.
#   (c) positions/hist [4] FUNDING == sign * the ledger amount within 1e-8 for exactly one sign in {+1, -1}.
# All three agree -> a PROPOSED pin (position_funding_sign, ledger id, pid, both raw values, BALANCE, rate, side,
# timestamps) is persisted and logged for the tracker. It becomes ACTIVE only through the explicit step
# ``python live_funding_ledger.py sign-pin confirm --rk-ref <RK ruling ref> --hm-ref <HM confirmation ref>``
# (``activate_sign_pin``), which records both refs; nothing activates it automatically. Any check that disagrees ->
# nothing is set, the row stays UNVERIFIED and is flagged SIGN_PIN_DISAGREEMENT, and the state records the
# disagreement (no further proposals until RK/HM clear it with ``sign-pin reject --ref``).
POSITION_FUNDING_SIGN = None   # unproven; the effective sign is only ever an ACTIVE persisted pin (+1 / -1)
SIGNED_MATCH_TOL_USD = 1e-8
SIGN_PIN_ENV = "LIVE_FUNDING_SIGN_PIN_PATH"
SIGN_PIN_FILENAME = "live_funding_sign_pin.json"
SIGN_PIN_STATUSES = ("NONE", "PROPOSED", "ACTIVE", "DISAGREEMENT", "UNREADABLE")
SIGN_PIN_DISAGREEMENT = "SIGN_PIN_DISAGREEMENT"
LEDGER_BALANCE_IDX = 6
WALLET_READ_LIMIT = 100


class SignPinError(Exception):
    """An HM admin step on the sign pin was refused (wrong state / missing confirmation ref)."""


def sign_pin_path() -> str:
    """The persisted pin state. Lives on the data volume root (BOT_DATA_DIR, the same root as platform_health_state.json),
    NOT under runtime/ or research/: research / fresh-collection resets delete only ``all_research_wipe_paths`` and the
    epoch quarantine, never this file - a reset must not drop an RK+HM-confirmed sign. Override: LIVE_FUNDING_SIGN_PIN_PATH."""
    raw = (os.getenv(SIGN_PIN_ENV) or "").strip()
    if raw:
        return os.path.abspath(raw)
    root = (os.getenv("BOT_DATA_DIR") or "").strip() or os.getcwd()
    return os.path.abspath(os.path.join(root, SIGN_PIN_FILENAME))


def load_sign_pin(path: str | None = None) -> dict:
    """The pin state (a local file read, never network). Missing -> NONE; corrupt / unknown -> UNREADABLE (no active
    sign and no new proposal: an unreadable state is never overwritten automatically)."""
    path = path or sign_pin_path()
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except FileNotFoundError:
        return {"status": "NONE"}
    except Exception:  # noqa: BLE001
        return {"status": "UNREADABLE"}
    if not isinstance(st, dict) or st.get("status") not in SIGN_PIN_STATUSES:
        return {"status": "UNREADABLE"}
    return st


def _write_sign_pin(state: dict, path: str | None = None) -> None:
    path = path or sign_pin_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True, default=str)
    os.replace(tmp, path)


def position_funding_sign(pin=None):
    """+1 / -1 only for an ACTIVE pin confirmed by BOTH an RK ruling ref and an HM confirmation ref; else
    POSITION_FUNDING_SIGN (None: unproven)."""
    if isinstance(pin, Mapping) and pin.get("status") == "ACTIVE" and pin.get("position_funding_sign") in (1, -1) \
            and str(pin.get("confirmed_by_rk_ref") or "").strip() and str(pin.get("confirmed_by_hm_ref") or "").strip():
        return pin["position_funding_sign"]
    return POSITION_FUNDING_SIGN


def activate_sign_pin(rk_ref: str, hm_ref: str, path: str | None = None, now: float | None = None) -> dict:
    """The explicit RK+HM step: PROPOSED -> ACTIVE, recording both confirmation refs. The only way a sign is set."""
    rk, hm = str(rk_ref or "").strip(), str(hm_ref or "").strip()
    if not rk or not hm:
        raise SignPinError("RK_AND_HM_CONFIRMATION_REFS_REQUIRED")
    st = load_sign_pin(path)
    if st.get("status") != "PROPOSED" or st.get("position_funding_sign") not in (1, -1):
        raise SignPinError(f"NOT_PROPOSED:{st.get('status')}")
    st.update(status="ACTIVE", confirmed_by_rk_ref=rk, confirmed_by_hm_ref=hm,
              confirmed_at_ts=time.time() if now is None else now)
    _write_sign_pin(st, path)
    log.warning("[LIVE FUNDING SIGN PIN ACTIVE] position_funding_sign=%s trade_id=%s pid=%s ledger_id=%s rk_ref=%s hm_ref=%s",
                st.get("position_funding_sign"), st.get("trade_id"), st.get("pid"), st.get("ledger_id"), rk, hm)
    return st


def reject_sign_pin(hm_ref: str, path: str | None = None, now: float | None = None) -> dict:
    """RK/HM admin step: clear a PROPOSED / ACTIVE / DISAGREEMENT state (kept in ``history``) so a new proposal can run."""
    ref = str(hm_ref or "").strip()
    if not ref:
        raise SignPinError("HM_CONFIRMATION_REF_REQUIRED")
    st = load_sign_pin(path)
    if st.get("status") in ("NONE", "UNREADABLE"):
        raise SignPinError(f"NOTHING_TO_REJECT:{st.get('status')}")
    hist = list(st.pop("history", []) or [])
    hist.append(dict(st, rejected_by_ref=ref, rejected_at_ts=time.time() if now is None else now))
    new = {"status": "NONE", "history": hist}
    _write_sign_pin(new, path)
    return new


def _sgn(x) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _side_sign(side) -> int:
    s = str(side or "").strip().upper()
    return 1 if s in ("LONG", "BUY") else (-1 if s in ("SHORT", "SELL") else 0)


def read_wallet_ledger(entry_ts: float, creds=None, post=_post) -> list:
    """Every ledger row (no category filter) up to and including the entry MTS: the source of check (a)'s previous
    balance. Same READ-ONLY key, same endpoint; raises ReadError (redacted)."""
    creds = creds or read_credentials()
    if not creds:
        raise ReadError("READ_KEY_MISSING")
    return post(LEDGER_PATH, {"end": int(round(entry_ts * 1000)) + 1, "limit": WALLET_READ_LIMIT}, creds)


def _balance_check(p, rows) -> dict:
    """(a) for one tied entry: sign(amount) vs sign(BALANCE[6] - previous BALANCE of the same currency + wallet)."""
    out = {"ledger_id": p["id"], "amount": p["amount"], "balance": None, "prev_ledger_id": None, "prev_balance": None,
           "balance_change": None, "ok": None, "missing": None}
    if not isinstance(rows, list):
        out["missing"] = "a:WALLET_READ_FAILED:" + str((rows or {}).get("error") if isinstance(rows, Mapping) else "NOT_READ")
        return out
    same = [r for r in rows if isinstance(r, list) and len(r) > LEDGER_BALANCE_IDX and r[1] == p["currency"]
            and r[2] == p["wallet"] and _f(r[3]) is not None]
    me = [r for r in same if r[0] == p["id"]]
    if len(me) != 1:
        out["missing"] = "a:ENTRY_NOT_IN_WALLET_READ"
        return out
    mts = _f(me[0][3])
    if any(r[0] != p["id"] and _f(r[3]) == mts for r in same):
        out["missing"] = "a:SAME_MTS_ROWS"           # balance order inside one ms is not provable
        return out
    older = [r for r in same if _f(r[3]) < mts]
    if not older:
        out["missing"] = "a:NO_PREVIOUS_BALANCE"
        return out
    last = max(_f(r[3]) for r in older)
    prev = [r for r in older if _f(r[3]) == last]
    if len(prev) != 1:
        out["missing"] = "a:PREVIOUS_ROW_AMBIGUOUS"
        return out
    bal, pbal = _f(me[0][LEDGER_BALANCE_IDX]), _f(prev[0][LEDGER_BALANCE_IDX])
    out.update(balance=bal, prev_ledger_id=prev[0][0], prev_balance=pbal)
    if bal is None or pbal is None:
        out["missing"] = "a:BALANCE_MISSING"
        return out
    change = round(bal - pbal, 12)
    out["balance_change"] = change
    out["ok"] = _sgn(change) != 0 and _sgn(change) == _sgn(p["amount"])
    return out


def _rate_direction_check(p, t, rec, side) -> dict:
    """(b) for one tied entry: sign(amount) vs the published settlement rate x position side (longs pay on rate > 0)."""
    out = {"ledger_id": p["id"], "settlement_ts": t, "rate": None, "rate_row_mts": None, "side": side,
           "expected_sign": None, "ok": None, "missing": None}
    if not isinstance(rec, Mapping) or rec.get("error") or rec.get("rate") is None:
        out["missing"] = "b:RATE_UNREAD:" + str((rec or {}).get("error") if isinstance(rec, Mapping) else "NOT_READ")
        return out
    out.update(rate=_rate_text(rec["rate"]), rate_row_mts=rec.get("row_mts"))
    sd = _side_sign(side)
    if not sd:
        out["missing"] = "b:SIDE_UNKNOWN"
        return out
    try:
        rs = _sgn(decimal.Decimal(str(rec["rate"])))
    except (decimal.InvalidOperation, ValueError):
        out["missing"] = "b:RATE_UNPARSEABLE"
        return out
    expected = -sd * rs                 # rate > 0: long (sd=+1) pays -> entry < 0; short receives -> entry > 0
    out["expected_sign"] = expected
    out["ok"] = expected != 0 and _sgn(p["amount"]) == expected
    return out


def propose_sign_pin(entries, wallet_rows, rates, side, position_funding) -> dict:
    """Pure: checks (a), (b), (c) on one row. ``entries`` = [(parsed tied ledger entry, settlement ts)] - exactly ONE
    non-zero entry is required (else INSUFFICIENT); ``wallet_rows`` = {ledger id: unfiltered ledger read or
    {"error": code}}; ``rates`` = {settlement ts: read_settlement_rate record}. result AGREE (all three hold; the
    proposed ``position_funding_sign`` is the one sign that satisfies (c)), DISAGREE (a check contradicts:
    SIGN_PIN_DISAGREEMENT) or INSUFFICIENT (evidence missing, nothing contradicts: no pin, no flag)."""
    entries = [(p, t) for p, t in entries if p["amount"] != 0]
    a = [_balance_check(p, wallet_rows.get(p["id"])) for p, _t in entries]
    b = [_rate_direction_check(p, t, rates.get(t), side) for p, t in entries]
    led = sum(p["amount"] for p, _t in entries)
    pf = _f(position_funding)
    c = {"position_funding_raw": position_funding, "ledger_amount_raw": entries[0][0]["amount"] if entries else None,
         "sign": None, "diff": None, "ok": None, "missing": None}
    if pf is None:
        c["missing"] = "c:POSITION_FUNDING_MISSING"
    elif entries:
        fits = [sg for sg in (1, -1) if abs(pf - sg * led) <= SIGNED_MATCH_TOL_USD]
        c["ok"] = len(fits) == 1
        if c["ok"]:
            c.update(sign=fits[0], diff=round(pf - fits[0] * led, 12))
        else:
            c["diff"] = [round(pf - led, 12), round(pf + led, 12)]
    checks = a + b + [c]
    missing = [x["missing"] for x in checks if x["missing"]]
    if len(entries) != 1:
        missing = ["SINGLE_TIED_NONZERO_ENTRY_REQUIRED"] + missing
    if any(x["ok"] is False for x in checks):
        result = "DISAGREE"
    elif missing:
        result = "INSUFFICIENT"
    else:
        result = "AGREE"
    rec = (rates.get(entries[0][1]) or {}) if len(entries) == 1 else {}
    return {"result": result, "position_funding_sign": c["sign"] if result == "AGREE" else None,
            "flag": SIGN_PIN_DISAGREEMENT if result == "DISAGREE" else None, "a": a, "b": b, "c": c,
            "disagree": sorted({k for k, xs in (("a", a), ("b", b), ("c", [c])) if any(x["ok"] is False for x in xs)}),
            "missing": missing, "side": side, "rate": _rate_text(rec.get("rate")) if rec else None}


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
           settlement_rates=None, sign_pin=None, sign_pin_check=None) -> dict:
    """Pure decision for one live row. ``position`` = (position_id, create_ts, update_ts[, position_funding]) or None.
    ``settlement_rates`` = {settlement ts: read_settlement_rate(...) + read_at_ts} for the no-entry settlements.
    ``sign_pin`` = the persisted pin state (only an RK+HM-confirmed ACTIVE pin sets POSITION_FUNDING_SIGN);
    ``sign_pin_check`` = this row's (a)/(b)/(c) proposal check, if the job ran one."""
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
           "funding_ts_fallbacks": [], "funding_settlement_rate_readbacks": [],
           "funding_position_funding_sign": position_funding_sign(sign_pin),
           "funding_sign_pin_status": (sign_pin or {}).get("status") or "NONE",
           "funding_sign_pin_refs": ({"rk": sign_pin.get("confirmed_by_rk_ref"), "hm": sign_pin.get("confirmed_by_hm_ref")}
                                     if position_funding_sign(sign_pin) is not None else None),
           "funding_sign_pin_flag": (sign_pin_check or {}).get("flag") or
                                    (SIGN_PIN_DISAGREEMENT if (sign_pin or {}).get("status") == "DISAGREEMENT" else None),
           "funding_sign_pin_check": sign_pin_check}
    if b is not None and exit_ts_source != EXIT_FILL_SOURCE:
        # HM R4 F4: any exit ts other than the Bitfinex exit fill record is a fallback -> UNVERIFIED, wherever it lies
        out["funding_ts_fallbacks"] = [f"exit_ts:{exit_ts_source or 'unknown'}"]
    if a is None:   # HM (E): no real entry fill ts -> explicit fallback flag, never a guessed window (non-countable)
        out["funding_ts_fallbacks"] = out["funding_ts_fallbacks"] + ["entry_ts:fill_at_ts_missing"]
    if b is None:
        out["funding_ts_fallbacks"] = out["funding_ts_fallbacks"] + ["exit_ts:exit_fill_missing"]
    if a is None or b is None or b < a:
        out["funding_status"] = "UNVERIFIED_FILL_TS_MISSING"
        return out
    if out["funding_ts_fallbacks"]:
        out["funding_status"] = "UNVERIFIED_EXIT_TS_FALLBACK"
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
    pf = position[3] if position is not None and len(position) > 3 else None
    if not problems and position is not None:
        # HM R4 F1/F2 + RK 04:09: one SIGNED rule for the zero-rate path, the tied-only path and no-crossing rows, 1e-8,
        # no abs() on either side. Both zero needs no sign; anything else needs the ACTIVE POSITION_FUNDING_SIGN.
        sign = position_funding_sign(sign_pin)
        if pf is None:
            if zero_idx:
                _zero_rate_position_check(statuses, rate_proof, zero_idx)
                problems.append("UNVERIFIED_ZERO_RATE_POSITION_CHECK")
            elif crossed:
                problems.append("UNVERIFIED_POSITION_FUNDING_MISSING")
        elif abs(pf) <= SIGNED_MATCH_TOL_USD and abs(led) <= SIGNED_MATCH_TOL_USD:
            pass                                                    # (i) both zero
        elif sign is not None and abs(pf - sign * led) <= SIGNED_MATCH_TOL_USD:
            pass                                                    # (ii) signed match under the ACTIVE sign
        elif sign is None and abs(led) > SIGNED_MATCH_TOL_USD:
            problems.append("UNVERIFIED_POSITION_FUNDING_SIGN_UNPROVEN")
        elif zero_idx:
            _zero_rate_position_check(statuses, rate_proof, zero_idx)
            problems.append("UNVERIFIED_ZERO_RATE_POSITION_CHECK")
        else:
            problems.append("UNVERIFIED_LEDGER_POSITION_MISMATCH")
    if problems:
        out["funding_status"] = problems[0]
        return out
    live = round(-led, 10) if used else 0.0   # RK (2): no settlement -> a proven 0
    out.update(funding_status="PRICED" if crossed else "NO_CROSSING", funding_verified=True, funding_usd_raw=live)
    if out["funding_paper_usd"] is not None:                      # RK (3): realism metric, never an exclusion
        out["funding_live_minus_paper_usd"] = round(live - out["funding_paper_usd"], 10)
    return out


def _zero_rate_position_check(statuses, rate_proof, zero_idx):
    for i in zero_idx:
        statuses[i] = "ZERO_RATE_POSITION_CHECK"
    for rp in rate_proof:
        if rp["status"] == "ZERO_RATE":
            rp["status"] = "ZERO_RATE_POSITION_CHECK"


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
    """(exit ts, source): the Bitfinex exit fill record first; any other stamp is a fallback (funding_ts_fallbacks) and
    makes the row UNVERIFIED_EXIT_TS_FALLBACK (HM R4 F4) - it is returned only so the flag names its source."""
    for k in ("exit_fill_record_at_ts", "exit_ack_at_ts", "exit_confirmed_at_ts", "exit_at_ts"):
        v = _f(chain.get(k))
        if v is not None:
            return v, k
    return None, None


def _chain_side(chain: Mapping[str, Any]):
    """The position side for check (b): the chain's ``position_side`` (the live copy mirrors its paper twin's signal side,
    attached by live_copy_monitor_api._closed_live_chains) or ``side``; None when unknown (then no pin is proposed)."""
    v = chain.get("position_side") or chain.get("side")
    return str(v).upper() if v else None


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
                 budget: RateBudget | None = None, chains_fn: Callable | None = None, get=_get_public,
                 sign_pin_path: str | None = None):
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
        self._sign_pin_path = sign_pin_path      # None -> sign_pin_path() (resolved when the owner thread loads it)
        self._pin: dict = {"status": "NONE"}     # in-memory copy; the owner thread reloads the file every tick

    # ---- GET side: pure cache reads, never any I/O -------------------------------------------------------------
    def lookup(self, chain: Mapping[str, Any], paper_funding_usd=None):
        chain = chain or {}
        if not _closed(chain):
            return None
        a, b, src, notional, pid, key = _row_inputs(chain)
        kw = dict(paper_funding_usd=paper_funding_usd, exit_ts_source=src, live_notional_usd=notional)
        if a is None or b is None or b < a or src != EXIT_FILL_SOURCE:
            # HM (E) / R4 F4: missing fill ts or a non-fill exit stamp -> flagged, non-countable, never read (no budget)
            return verify(a, b, read_error="NO_READ", key_gate=self._gate, **kw)
        now = self._clock()
        with self._lock:
            gate, hit = self._gate, self._cache.get(key)
            if gate in (None, "OK") and self._stale_locked(hit, now):
                self._enqueue_locked(key, a, b, pid, hit, notional=notional, side=_chain_side(chain))
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

    def _result(self, hit, a, b, kw):
        return verify(a, b, ledger_rows=hit["rows"], read_error=hit["error"], read_at_ts=hit["read_at_ts"],
                      position=hit["position"], key_gate="OK", settlement_rates=hit.get("rates"),
                      sign_pin=self._pin, sign_pin_check=hit.get("pin_check"), **kw)

    def sign_pin_state(self) -> dict:
        return dict(self._pin)

    def _pin_file(self) -> str:
        return self._sign_pin_path or sign_pin_path()

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
        a, b, src, notional, pid, key = _row_inputs(chain)
        if a is None or b is None or b < a or src != EXIT_FILL_SOURCE:
            return
        with self._lock:
            hit = self._cache.get(key)
            if self._stale_locked(hit, self._clock()):
                self._enqueue_locked(key, a, b, pid, hit, notional=notional, side=_chain_side(chain))
            elif key in self._pending and self._pending[key].get("side") is None:
                self._pending[key]["side"] = _chain_side(chain)

    def _enqueue_locked(self, key, a, b, pid, hit=None, notional=None, side=None):
        if key not in self._pending and len(self._pending) < self.MAX_PENDING:
            self._pending[key] = {"a": a, "b": b, "pid": pid, "rows": None, "read_at_ts": None, "pos_done": False,
                                  "position": None, "rates": None, "attempts": (hit or {}).get("attempts", 0) + 1,
                                  "trade_id": key[0], "notional": notional, "side": side, "pin_done": False,
                                  "pin_wallet": {}, "pin_rates": {}, "pin_check": None}

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
        self._pin = load_sign_pin(self._pin_file())   # local file only; picks up an HM confirm / reject between ticks
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
            if not work.get("pin_done"):
                self._sign_pin_step(work)
                work["pin_done"] = True
            self._finish(key, work, None)
        except ReadError as exc:
            if str(exc) in ("HTTP_429", "NOT_OWNER_THREAD"):
                raise
            self._finish(key, work, str(exc))

    def _sign_pin_step(self, work):
        """RK 04:09 / HM R4 F1: while NO pin state exists, the first closed live row with a SINGLE tied non-zero entry
        gathers the independent evidence for checks (a)/(b) - one unfiltered ledger read and the published rate of that
        settlement, same owner thread, same budget - and proposes a sign (PROPOSED, never ACTIVE) or raises
        SIGN_PIN_DISAGREEMENT. Rows with more than one non-zero entry never propose (and spend no read)."""
        if self._pin.get("status") != "NONE":
            return
        res = verify(work["a"], work["b"], ledger_rows=work["rows"], read_at_ts=work["read_at_ts"],
                     position=work["position"], live_notional_usd=work.get("notional"), settlement_rates=work["rates"])
        if res["funding_status"] != "UNVERIFIED_POSITION_FUNDING_SIGN_UNPROVEN":
            return
        parsed = {p["id"]: p for p in (_parse_row(r) for r in work["rows"] or []) if p}
        entries = [(parsed[x["ledger_id"]], x["settlement_ts"]) for x in res["funding_usd_by_settlement"]
                   if x.get("ledger_id") is not None and parsed[x["ledger_id"]]["amount"] != 0]
        wallet, prates = work.setdefault("pin_wallet", {}), work.setdefault("pin_rates", {})
        for p, t in (entries if len(entries) == 1 else []):
            if p["id"] not in wallet:
                try:
                    wallet[p["id"]] = read_wallet_ledger(p["ts"], creds=self._creds, post=self._post)
                except ReadError as exc:
                    if str(exc) in ("HTTP_429", "NOT_OWNER_THREAD"):
                        raise
                    wallet[p["id"]] = {"error": str(exc)}
                self._backoff_n = 0
            if t not in prates:
                rec = read_settlement_rate(t, get=self._get)
                rec["read_at_ts"] = self._clock()
                prates[t] = rec
                self._backoff_n = 0
        pf = work["position"][3] if work["position"] is not None and len(work["position"]) > 3 else None
        check = propose_sign_pin(entries, wallet, prates, work.get("side"), pf)
        pos = work["position"]
        check.update(trade_id=work.get("trade_id"), pid=pos[0], position_create_ts=pos[1], position_update_ts=pos[2],
                     entry_fill_ts=work["a"], exit_fill_ts=work["b"], ledger_ids=[p["id"] for p, _t in entries],
                     ledger_mts=[p["ts"] for p, _t in entries], settlement_ts=[t for _p, t in entries],
                     checked_at_ts=self._clock())
        work["pin_check"] = check
        self._record_sign_pin(check)

    def _record_sign_pin(self, check):
        if check["result"] == "INSUFFICIENT":
            log.info("[LIVE FUNDING SIGN PIN] evidence insufficient trade_id=%s missing=%s (no pin, no flag)",
                     check.get("trade_id"), check.get("missing"))
            return
        path = self._pin_file()
        cur = load_sign_pin(path)
        if cur.get("status") != "NONE":      # never overwrite an existing / HM-edited / unreadable state
            self._pin = cur
            return
        a0 = (check.get("a") or [{}])[0]
        keep = {k: check.get(k) for k in ("trade_id", "pid", "ledger_ids", "ledger_mts", "settlement_ts",
                                          "position_create_ts", "position_update_ts", "entry_fill_ts", "exit_fill_ts",
                                          "side", "rate")}
        keep.update(ledger_id=(check.get("ledger_ids") or [None])[0],
                    position_funding_raw=check["c"].get("position_funding_raw"),
                    ledger_amount_raw=check["c"].get("ledger_amount_raw"),
                    ledger_balance=a0.get("balance"), ledger_prev_balance=a0.get("prev_balance"),
                    ledger_prev_id=a0.get("prev_ledger_id"))
        if check["result"] == "AGREE":
            state = dict(keep, status="PROPOSED", position_funding_sign=check["position_funding_sign"],
                         proposed_at_ts=check["checked_at_ts"], checks={k: check[k] for k in ("a", "b", "c")},
                         confirmed_by_rk_ref=None, confirmed_by_hm_ref=None, confirmed_at_ts=None,
                         history=cur.get("history") or [])
            tag = ("[LIVE FUNDING SIGN PIN PROPOSED] awaiting RK+HM confirmation "
                   "(sign-pin confirm --rk-ref <RK ref> --hm-ref <HM ref>)")
        else:
            state = {"status": "DISAGREEMENT", "position_funding_sign": None, "flag": SIGN_PIN_DISAGREEMENT,
                     "disagreements": [dict(keep, disagree=check["disagree"], checks={k: check[k] for k in ("a", "b", "c")},
                                            flagged_at_ts=check["checked_at_ts"])],
                     "history": cur.get("history") or []}
            tag = "[LIVE FUNDING SIGN_PIN_DISAGREEMENT] nothing pinned; HM review"
        try:
            _write_sign_pin(state, path)
        except OSError as exc:
            log.warning("[LIVE FUNDING SIGN PIN] state write failed (%s); nothing pinned", type(exc).__name__)
            return
        self._pin = state
        log.warning("%s position_funding_sign=%s trade_id=%s pid=%s ledger_id=%s pf_raw=%s ledger_amount_raw=%s "
                    "ledger_balance=%s rate=%s side=%s disagree=%s path=%s", tag, state.get("position_funding_sign"),
                    keep["trade_id"], keep["pid"], keep["ledger_id"], keep["position_funding_raw"],
                    keep["ledger_amount_raw"], keep["ledger_balance"], keep["rate"], keep["side"],
                    check.get("disagree"), path)

    def _finish(self, key, work, error):
        rates = None if error else (work.get("rates") or {})
        rec = {"rows": None if error else work["rows"], "position": None if error else work["position"],
               "error": error, "read_at_ts": work["read_at_ts"] or self._clock(), "rates": rates,
               "rate_failed": bool(rates) and any(r.get("error") for r in rates.values()),
               "attempts": work.get("attempts", 1), "pin_check": None if error else work.get("pin_check")}
        with self._lock:
            self._cache[key] = rec
            self._pending.pop(key, None)


class _BudgetEmpty(Exception):
    """This key's minute budget is spent: stop the tick, keep the queue (never an error stamped on a row)."""


DEFAULT_JOB = FundingReadbackJob()


def main(argv=None) -> int:
    """HM admin CLI (the explicit step; the job never activates a pin):
    python live_funding_ledger.py sign-pin show
    python live_funding_ledger.py sign-pin confirm --rk-ref <RK ruling ref> --hm-ref <HM ref>   # PROPOSED -> ACTIVE
    python live_funding_ledger.py sign-pin reject --ref <RK/HM ref>          # clear PROPOSED/ACTIVE/DISAGREEMENT"""
    import argparse
    ap = argparse.ArgumentParser(prog="live_funding_ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sign-pin")
    sp.add_argument("action", choices=("show", "confirm", "reject"))
    sp.add_argument("--ref", default="")
    sp.add_argument("--rk-ref", default="")
    sp.add_argument("--hm-ref", default="")
    sp.add_argument("--path", default=None)
    args = ap.parse_args(argv)
    try:
        if args.action == "show":
            st = load_sign_pin(args.path)
        elif args.action == "confirm":
            st = activate_sign_pin(args.rk_ref, args.hm_ref, path=args.path)
        else:
            st = reject_sign_pin(args.ref, path=args.path)
    except SignPinError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps({"path": args.path or sign_pin_path(), "state": st}, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
