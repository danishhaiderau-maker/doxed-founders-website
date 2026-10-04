"""Fee truth: the Bitfinex account's actual fee rates vs every place the repo encodes fees.

Source of truth is the authenticated, read-only ``POST /v2/auth/r/summary``
(the account's own maker/taker rates, derivatives included). If no read-capable
key is configured the public fee schedule is consulted and the result is
``PUBLIC_SCHEDULE_UNVERIFIED`` (never GREEN). The account answer is cached for
``FEE_TTL_SEC``; the static drift scan runs on every job.

Drift scan: ``bitfinex_cost_profile.py`` is the one place fees may be written
down. Every other surface (TS relay/sim constants, Fly's deployed revision,
numeric fee literals in simulation/analyzer/dashboard code) must agree with it,
and the profile must agree with the account.

Credentials are read from ``BITFINEX_READ_API_KEY``/``BITFINEX_READ_API_SECRET``,
then ``BITFINEX_API_KEY``/``BITFINEX_API_SECRET``, then the same names in
``SELF_AWARE_BITFINEX_ENV_FILE``. Their values never leave this module: not in
results, logs, errors or the cache file.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .facts import iso

VENUE = "bitfinex"
SUMMARY_PATH = "v2/auth/r/summary"
API_BASE = "https://api.bitfinex.com/"
PUBLIC_SCHEDULE_URLS = ("https://www.bitfinex.com/fees/", "https://www.bitfinex.com/zero-fee-trading/")
USER_AGENT = "doxxed-self-aware-fees/1 (+read-only)"
FEE_TTL_SEC = 6 * 3600
FEE_RETRY_SEC = 15 * 60
STALE_AFTER_SEC = int(FEE_TTL_SEC * 1.5)
# Bitfinex wants strictly increasing nonces per key. The platform relay and the
# bot share this key at Date.now() * 10_000; staying on that scale (never above
# it) keeps every caller's next nonce valid.
NONCE_MS_SCALE = 10_000
DEFAULT_ENV_FILE = r"C:\DoxxedCrypto\doxedcryptofounder-secrets\vault\home-bot.env"
CACHE_NAME = "fees-truth.json"
PROFILE_REL = "services/btc-conservative-agent/bitfinex_cost_profile.py"
TOLERANCE_BPS = 1e-6

ACCOUNT_SUMMARY, PUBLIC_SCHEDULE_UNVERIFIED, UNAVAILABLE = "ACCOUNT_SUMMARY", "PUBLIC_SCHEDULE_UNVERIFIED", "UNAVAILABLE"

_nonce_lock = threading.Lock()
_last_nonce = 0


class FeeSourceError(RuntimeError):
    """A fee source failed; the message is safe to publish (no credentials)."""


# ------------------------------------------------------------ credentials

def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def load_credentials(env: dict[str, str] | None = None) -> tuple[str, str, str] | None:
    """Return ``(key, secret, origin)``; origin names where they came from, never their value."""
    env = dict(os.environ if env is None else env)
    for k, s in (("BITFINEX_READ_API_KEY", "BITFINEX_READ_API_SECRET"), ("BITFINEX_API_KEY", "BITFINEX_API_SECRET")):
        if env.get(k) and env.get(s):
            return env[k], env[s], f"env:{k}"
    path = Path(env.get("SELF_AWARE_BITFINEX_ENV_FILE") or DEFAULT_ENV_FILE)
    if "onedrive" in str(path).lower():
        return None
    vals = _parse_env_file(path)
    for k, s in (("BITFINEX_READ_API_KEY", "BITFINEX_READ_API_SECRET"), ("BITFINEX_API_KEY", "BITFINEX_API_SECRET")):
        if vals.get(k) and vals.get(s):
            return vals[k], vals[s], f"file:{path.name}:{k}"
    return None


def _nonce() -> str:
    global _last_nonce
    with _nonce_lock:
        _last_nonce = max(int(time.time() * 1000) * NONCE_MS_SCALE, _last_nonce + 1)
        return str(_last_nonce)


def _redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[REDACTED]")
    return text


# ---------------------------------------------------------------- sources

def _post_summary(key: str, secret: str, timeout: float) -> Any:
    body = "{}"
    nonce = _nonce()
    sig = hmac.new(secret.encode(), f"/api/{SUMMARY_PATH}{nonce}{body}".encode(), hashlib.sha384).hexdigest()
    req = urllib.request.Request(API_BASE + SUMMARY_PATH, data=body.encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT,
        "bfx-nonce": nonce, "bfx-apikey": key, "bfx-signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", "replace") if exc.fp else ""
        raise FeeSourceError(_redact(f"HTTP {exc.code}: {detail}", key, secret)) from None
    except (OSError, ValueError) as exc:
        raise FeeSourceError(_redact(f"{type(exc).__name__}: {exc}", key, secret)) from None


def _rate(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_summary(payload: Any) -> dict[str, Any]:
    """Map the documented summary array to fee rates (fractions of notional).

    ``payload[4] == [[MAKER, MAKER, MAKER, _, _, DERIV_REBATE], [TAKER_CRYPTO, TAKER_STABLE, TAKER_FIAT, _, _, DERIV_TAKER]]``.
    ``DERIV_REBATE`` is a rebate: a positive value means the maker is paid, so the fee rate is its negation.
    """
    try:
        maker_row, taker_row = payload[4][0], payload[4][1]
    except (TypeError, IndexError, KeyError):
        raise FeeSourceError("summary payload has no fee block at index 4") from None
    pick = lambda row, i: _rate(row[i]) if isinstance(row, list) and len(row) > i else None  # noqa: E731
    rebate = pick(maker_row, 5)
    out = {
        "maker_rate": pick(maker_row, 0), "taker_rate": pick(taker_row, 0),
        "taker_rate_to_stable": pick(taker_row, 1), "taker_rate_to_fiat": pick(taker_row, 2),
        "derivatives_maker_rate": None if rebate is None else (-rebate if rebate else 0.0),
        "derivatives_taker_rate": pick(taker_row, 5),
        "raw_fee_block": [maker_row, taker_row],
    }
    if out["derivatives_maker_rate"] is None or out["derivatives_taker_rate"] is None:
        raise FeeSourceError("summary fee block lacks derivatives maker/taker fields")
    return out


def fetch_account(creds: tuple[str, str, str], timeout: float = 20.0) -> dict[str, Any]:
    key, secret, origin = creds
    for attempt in range(2):
        try:
            rates = parse_summary(_post_summary(key, secret, timeout))
            return {**rates, "source": ACCOUNT_SUMMARY, "credential_origin": origin, "endpoint": API_BASE + SUMMARY_PATH}
        except FeeSourceError as exc:
            if "nonce" in str(exc).lower() and attempt == 0:
                time.sleep(0.25)
                continue
            raise
    raise FeeSourceError("unreachable")


_ZERO_MARKERS = re.compile(r"zero[\s-]*fee|0(?:\.0+)?\s*%\s*(?:maker|taker)|(?:maker|taker)[^<]{0,40}\bzero\b", re.I)


def fetch_public(timeout: float = 20.0) -> dict[str, Any]:
    errors = []
    for url in PUBLIC_SCHEDULE_URLS:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 " + USER_AGENT, "Accept": "text/html"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                html = r.read(3_000_000).decode("utf-8", "replace")
        except (OSError, ValueError) as exc:
            errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        zero = bool(_ZERO_MARKERS.search(html))
        rate = 0.0 if zero else None
        return {"maker_rate": rate, "taker_rate": rate, "derivatives_maker_rate": rate, "derivatives_taker_rate": rate,
                "source": PUBLIC_SCHEDULE_UNVERIFIED, "endpoint": url, "zero_fee_language_found": zero}
    raise FeeSourceError("; ".join(errors) or "public schedule unreachable")


# ------------------------------------------------------------- drift scan

def _num(text: str) -> float:
    return float(text.replace("_", ""))


def read_profile_text(text: str) -> dict[str, Any]:
    m = re.search(r"^MAKER_FEE_RATE\s*=\s*([-0-9._eE]+)", text, re.M)
    t = re.search(r"^TAKER_FEE_RATE\s*=\s*([-0-9._eE]+)", text, re.M)
    if not (m and t):
        raise ValueError("MAKER_FEE_RATE / TAKER_FEE_RATE not found")
    maker, taker = _num(m.group(1)), _num(t.group(1))
    pid = "BITFINEX_ZERO" if maker == 0.0 and taker == 0.0 else f"BITFINEX_M{maker * 1e4:g}_T{taker * 1e4:g}"
    return {"maker_rate": maker, "taker_rate": taker, "fee_profile_id": pid}


def _bps(rate: float | None) -> float | None:
    return None if rate is None else round(rate * 1e4, 6)


def _same(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) * 1e4 <= TOLERANCE_BPS


# Surfaces outside Python that cannot import the profile; each must restate it exactly.
TS_SURFACES = (
    {"file": "apps/api/src/exchanges/bitfinex-sim-trading.client.ts", "field": "SIM_FEE_BPS",
     "pattern": r"\bSIM_FEE_BPS\s*=\s*([-0-9._]+)", "unit": "bps", "compare": ("maker", "taker")},
    {"file": "apps/api/src/trading-agents/signal-subscriber-execution.service.ts", "field": "stableRelayFeeModel.maker_fee_rate",
     "pattern": r"configured_profile:\s*'[^']*',\s*maker_fee_rate:\s*([-0-9._eE]+)", "unit": "rate", "compare": ("maker",)},
    {"file": "apps/api/src/trading-agents/signal-subscriber-execution.service.ts", "field": "stableRelayFeeModel.taker_fee_rate",
     "pattern": r"configured_profile:\s*'[^']*',\s*maker_fee_rate:\s*[-0-9._eE]+,\s*taker_fee_rate:\s*([-0-9._eE]+)",
     "unit": "rate", "compare": ("taker",)},
    {"file": "apps/api/src/trading-agents/signal-subscriber-execution.service.ts", "field": "stableRelayFeeModel.configured_profile",
     "pattern": r"configured_profile:\s*'([^']*)'", "unit": "id", "compare": ("id",)},
)

# Python/JS/HTML trees whose fee numbers must come from the profile, never a literal.
LITERAL_TREES = ("services/btc-conservative-agent", "scripts")
LITERAL_EXT = (".py", ".js", ".html")
LITERAL_SKIP = re.compile(r"(^|[\\/])(test_[^\\/]*|[^\\/]*_test\.py|conftest\.py|bitfinex_cost_profile\.py|fees\.py)$|"
                          r"[\\/](tests?|fixtures|node_modules|__pycache__|\.venv|venv|v3)[\\/]")
LITERAL_RE = re.compile(
    r"\b([A-Za-z_]*(?:maker|taker)[A-Za-z_]*fee[A-Za-z_]*|[A-Za-z_]*fee_?(?:rate|bps|bp|pct)[A-Za-z_]*)\b[\"']?"
    r"(?:\s*:\s*[A-Za-z][A-Za-z\[\]., |]*?(?==))?\s*(?<![=!<>])[:=](?!=)\s*(-?\d+(?:\.\d+)?(?:[eE]-?\d+)?)"
    r"(?![\w.])(?!\s*[*/+\-%])", re.I)


def scan_literals(root: Path, max_files: int = 4000) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    n = 0
    for tree in LITERAL_TREES:
        base = root / tree
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in LITERAL_EXT or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if LITERAL_SKIP.search(rel):
                continue
            n += 1
            if n > max_files:
                return hits
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "fee" not in text.lower():
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if s.startswith(("#", "//", "*")) or "fee" not in s.lower():
                    continue
                for m in LITERAL_RE.finditer(s):
                    name = m.group(1).lower()
                    if name.endswith("fees") or "usd" in name:
                        continue  # accumulated fee amounts (e.g. maker_fees = 0.0), not rates
                    hits.append({"file": rel, "line": lineno, "field": m.group(1), "value": _num(m.group(2)),
                                 "text": s[:160]})
    return hits


def _git_show(repo: Path, rev: str, rel: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{rel}"], capture_output=True, text=True,
                             timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def drift(root: Path, account: dict[str, Any] | None, *, fly_rev: str | None = None,
          extra_roots: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Compare profile vs account and every other fee surface vs profile. Returns mismatches with file/field."""
    surfaces: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []

    def add(file: str, field: str, value: Any, expected: Any, ok: bool, unit: str, note: str = "") -> None:
        row = {"file": file, "field": field, "value": value, "expected": expected, "unit": unit, "ok": ok}
        if note:
            row["note"] = note
        surfaces.append(row)
        if not ok:
            mismatches.append(row)

    try:
        profile = read_profile_text((root / PROFILE_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"profile": None, "surfaces": [], "mismatches": [{"file": PROFILE_REL, "field": "MAKER/TAKER_FEE_RATE",
                "value": None, "expected": "readable", "unit": "rate", "ok": False, "note": str(exc)[:200]}], "literals": []}
    pm, pt = profile["maker_rate"], profile["taker_rate"]

    if account and account.get("derivatives_maker_rate") is not None:
        add(PROFILE_REL, "MAKER_FEE_RATE", pm, account["derivatives_maker_rate"],
            _same(pm, account["derivatives_maker_rate"]), "rate", f"vs account derivatives maker ({account.get('source')})")
        add(PROFILE_REL, "TAKER_FEE_RATE", pt, account["derivatives_taker_rate"],
            _same(pt, account["derivatives_taker_rate"]), "rate", f"vs account derivatives taker ({account.get('source')})")

    for r in (root, *extra_roots):
        if r == root:
            continue
        try:
            other = read_profile_text((r / PROFILE_REL).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tag = f"{r.name}:{PROFILE_REL}"
        add(tag, "MAKER_FEE_RATE", other["maker_rate"], pm, _same(other["maker_rate"], pm), "rate", "analyzer checkout")
        add(tag, "TAKER_FEE_RATE", other["taker_rate"], pt, _same(other["taker_rate"], pt), "rate", "analyzer checkout")

    if fly_rev:
        text = _git_show(root, fly_rev, PROFILE_REL)
        if text is None:
            surfaces.append({"file": f"fly@{fly_rev[:12]}:{PROFILE_REL}", "field": "MAKER/TAKER_FEE_RATE", "value": None,
                             "expected": pm, "unit": "rate", "ok": None, "note": "deployed revision not in local git"})
        else:
            try:
                fly = read_profile_text(text)
                tag = f"fly@{fly_rev[:12]}:{PROFILE_REL}"
                add(tag, "MAKER_FEE_RATE", fly["maker_rate"], pm, _same(fly["maker_rate"], pm), "rate", "Fly deployed revision")
                add(tag, "TAKER_FEE_RATE", fly["taker_rate"], pt, _same(fly["taker_rate"], pt), "rate", "Fly deployed revision")
            except ValueError as exc:
                add(f"fly@{fly_rev[:12]}:{PROFILE_REL}", "MAKER/TAKER_FEE_RATE", None, pm, False, "rate", str(exc)[:120])

    for spec in TS_SURFACES:
        try:
            text = (root / spec["file"]).read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(spec["pattern"], text)
        if not m:
            add(spec["file"], spec["field"], None, "present", False, spec["unit"], "pattern not found; update fees.TS_SURFACES")
            continue
        raw = m.group(1)
        if spec["unit"] == "id":
            add(spec["file"], spec["field"], raw, profile["fee_profile_id"], raw == profile["fee_profile_id"], "id")
            continue
        rate = _num(raw) / 1e4 if spec["unit"] == "bps" else _num(raw)
        for side in spec["compare"]:
            exp = pm if side == "maker" else pt
            add(spec["file"], f"{spec['field']} (as {side})", _bps(rate), _bps(exp), _same(rate, exp), "bps")

    literals = scan_literals(root)
    for h in literals:
        exp_vals = {pm, pt, _bps(pm), _bps(pt)}
        if h["value"] not in exp_vals:
            add(h["file"], f"{h['field']} (line {h['line']})", h["value"], f"profile maker {pm:g} / taker {pt:g}",
                False, "literal", "hard-coded fee literal disagrees with bitfinex_cost_profile")
    return {"profile": {**profile, "file": PROFILE_REL, "maker_bps": _bps(pm), "taker_bps": _bps(pt)},
            "surfaces": surfaces, "mismatches": mismatches,
            "literals": [{**h, "matches_profile": h["value"] in {pm, pt, _bps(pm), _bps(pt)}} for h in literals]}


# ------------------------------------------------------------------ job

def _cache_path(home: Path) -> Path:
    return home / CACHE_NAME


def _load_cache(home: Path) -> dict[str, Any]:
    try:
        return json.loads(_cache_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(home: Path, doc: dict[str, Any]) -> None:
    p = _cache_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, p)


def refresh_account(home: Path, now: float, *, force: bool = False, creds_env: dict[str, str] | None = None,
                    fetch_account_fn=fetch_account, fetch_public_fn=fetch_public) -> dict[str, Any]:
    """Return the cached account fee record, refreshing it when older than the TTL (or retry window after failure)."""
    cache = _load_cache(home)
    fetched = cache.get("fetched_at_epoch") or 0
    retry_at = cache.get("retry_at_epoch") or 0
    fresh = cache.get("source") == ACCOUNT_SUMMARY and now - fetched < FEE_TTL_SEC
    if not force and (fresh or (cache and now < retry_at)):
        return cache
    creds = load_credentials(creds_env)
    attempt: dict[str, Any] = {"attempted_at": iso(now), "credentials": "configured" if creds else "missing"}
    rec: dict[str, Any] | None = None
    if creds:
        try:
            rec = fetch_account_fn(creds)
        except FeeSourceError as exc:
            attempt["account_error"] = str(exc)[:300]
    if rec is None:
        if cache.get("source") == ACCOUNT_SUMMARY:
            # Keep the last verified account answer; it turns stale rather than being replaced by an unverified one.
            cache.update(last_attempt=attempt, retry_at_epoch=now + FEE_RETRY_SEC)
            _save_cache(home, cache)
            return cache
        try:
            rec = fetch_public_fn()
        except FeeSourceError as exc:
            attempt["public_error"] = str(exc)[:300]
            rec = {"source": UNAVAILABLE, "maker_rate": None, "taker_rate": None,
                   "derivatives_maker_rate": None, "derivatives_taker_rate": None}
    rec = {k: v for k, v in rec.items() if k != "raw_fee_block"} | {"raw_fee_block": rec.get("raw_fee_block")}
    rec.update(fetched_at=iso(now), fetched_at_epoch=now, last_attempt=attempt,
               retry_at_epoch=now + (FEE_TTL_SEC if rec["source"] == ACCOUNT_SUMMARY else FEE_RETRY_SEC))
    _save_cache(home, rec)
    return rec


def build_doc(rec: dict[str, Any], dr: dict[str, Any], now: float) -> dict[str, Any]:
    fetched = rec.get("fetched_at_epoch")
    age = None if not fetched else max(0.0, now - fetched)
    stale = age is None or age > STALE_AFTER_SEC
    source = rec.get("source") or UNAVAILABLE
    mismatches = dr.get("mismatches") or []
    account_mm = [m for m in mismatches if m["file"] == PROFILE_REL and "vs account" in str(m.get("note"))]
    matches = (not account_mm) if source != UNAVAILABLE and rec.get("derivatives_taker_rate") is not None else None
    if mismatches:
        status = "RED"
    elif source == ACCOUNT_SUMMARY and not stale:
        status = "GREEN"
    else:
        status = "AMBER"
    return {
        "schema": "self_aware_fee_truth_v1", "generated_at": iso(now), "status": status, "venue": VENUE,
        "maker_bps": _bps(rec.get("maker_rate")), "taker_bps": _bps(rec.get("taker_rate")),
        "derivatives_maker_bps": _bps(rec.get("derivatives_maker_rate")),
        "derivatives_taker_bps": _bps(rec.get("derivatives_taker_rate")),
        "source": source, "fetched_at": rec.get("fetched_at"), "age_sec": None if age is None else round(age),
        "stale": stale, "ttl_sec": FEE_TTL_SEC, "matches_cost_profile": matches,
        "matches_everywhere": not mismatches and matches is True,
        "cost_profile": dr.get("profile"), "mismatches": mismatches, "surfaces": dr.get("surfaces"),
        "fee_literals": dr.get("literals"), "endpoint": rec.get("endpoint"),
        "credential_origin": rec.get("credential_origin"), "last_attempt": rec.get("last_attempt"),
        "raw_fee_block": rec.get("raw_fee_block"), "zero_fee_language_found": rec.get("zero_fee_language_found"),
    }


def run(home: Path, root: Path, now: float | None = None, *, fly_rev: str | None = None,
        extra_roots: tuple[Path, ...] = (), force: bool = False, **kw) -> dict[str, Any]:
    now = time.time() if now is None else now
    t0 = time.time()
    rec = refresh_account(home, now, force=force, **kw)
    acct = rec if rec.get("derivatives_taker_rate") is not None else None
    dr = drift(root, acct, fly_rev=fly_rev, extra_roots=extra_roots)
    doc = build_doc(rec, dr, now)
    doc["fly_rev"] = fly_rev
    doc["scanned_roots"] = [str(root), *[str(r) for r in extra_roots if r != root]]
    doc["ms"] = int((time.time() - t0) * 1000)
    return doc
