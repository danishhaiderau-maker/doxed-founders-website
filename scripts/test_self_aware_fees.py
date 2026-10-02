"""Fee truth: Bitfinex account fees (auth/r/summary) vs bitfinex_cost_profile and every other fee surface."""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import diagnose, fees  # noqa: E402
from self_aware.facts import iso  # noqa: E402

NOW = 1_790_930_000.0
ZERO_SUMMARY = [None, None, None, None, [[0, 0, 0, None, None, 0], [0, 0, 0, None, None, 0]], None, None]
PROFILE = 'MAKER_FEE_RATE = {m}\nTAKER_FEE_RATE = {t}\n'
KEY, SECRET = "k" * 43, "s" * 43


def _repo(tmp: Path, maker="0.0", taker="0.0", sim_bps="0", relay="'BITFINEX_ZERO', maker_fee_rate: 0, taker_fee_rate: 0",
          extra: dict[str, str] | None = None) -> Path:
    root = tmp / "repo"
    files = {
        fees.PROFILE_REL: PROFILE.format(m=maker, t=taker),
        "apps/api/src/exchanges/bitfinex-sim-trading.client.ts": f"const SIM_FEE_BPS = {sim_bps};\n",
        "apps/api/src/trading-agents/signal-subscriber-execution.service.ts":
            f"export function f() {{ return {{ configured_profile: {relay}, funding: 1 }}; }}\n",
        **(extra or {}),
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


ACCOUNT_ZERO = {"source": fees.ACCOUNT_SUMMARY, "maker_rate": 0.0, "taker_rate": 0.0,
                "derivatives_maker_rate": 0.0, "derivatives_taker_rate": 0.0}


# ------------------------------------------------------------- source

def test_parse_summary_reads_documented_fee_block():
    r = fees.parse_summary(ZERO_SUMMARY)
    assert (r["maker_rate"], r["taker_rate"], r["derivatives_maker_rate"], r["derivatives_taker_rate"]) == (0, 0, 0, 0)
    paid = [0, 0, 0, 0, [[0.001, 0.001, 0.001, None, None, 0.0002], [0.002, 0.002, 0.002, None, None, 0.00065]]]
    r = fees.parse_summary(paid)
    assert r["maker_rate"] == 0.001 and r["taker_rate"] == 0.002
    assert r["derivatives_maker_rate"] == -0.0002  # a positive rebate pays the maker
    assert r["derivatives_taker_rate"] == 0.00065
    with pytest.raises(fees.FeeSourceError):
        fees.parse_summary([1, 2])
    with pytest.raises(fees.FeeSourceError):
        fees.parse_summary([0, 0, 0, 0, [[0, 0, 0], [0, 0, 0]]])


def test_credentials_prefer_read_key_then_file_and_never_leak(tmp_path, monkeypatch):
    env_file = tmp_path / "bot.env"
    env_file.write_text(f'BITFINEX_API_KEY="{KEY}"\nBITFINEX_API_SECRET={SECRET}\n', encoding="utf-8")
    key, secret, origin = fees.load_credentials({"SELF_AWARE_BITFINEX_ENV_FILE": str(env_file)})
    assert (key, secret) == (KEY, SECRET) and origin == "file:bot.env:BITFINEX_API_KEY"
    assert fees.load_credentials({"BITFINEX_READ_API_KEY": "r", "BITFINEX_READ_API_SECRET": "x",
                                  "BITFINEX_API_KEY": "a", "BITFINEX_API_SECRET": "b"})[2] == "env:BITFINEX_READ_API_KEY"
    assert fees.load_credentials({"SELF_AWARE_BITFINEX_ENV_FILE": str(tmp_path / "missing.env")}) is None

    def leaky(*a, **k):
        raise fees.FeeSourceError(fees._redact(f"HTTP 500: apikey {KEY} invalid", KEY, SECRET))
    monkeypatch.setattr(fees, "_post_summary", leaky)
    rec = fees.refresh_account(tmp_path / "home", NOW, creds_env={"SELF_AWARE_BITFINEX_ENV_FILE": str(env_file)},
                               fetch_public_fn=lambda: {**ACCOUNT_ZERO, "source": fees.PUBLIC_SCHEDULE_UNVERIFIED})
    blob = json.dumps(rec) + (tmp_path / "home" / fees.CACHE_NAME).read_text(encoding="utf-8")
    assert KEY not in blob and SECRET not in blob and "[REDACTED]" in blob


def test_nonce_stays_on_platform_scale_and_is_monotonic():
    a, b = int(fees._nonce()), int(fees._nonce())
    assert b > a
    assert abs(a - int(time.time() * 1000) * fees.NONCE_MS_SCALE) < 60_000 * fees.NONCE_MS_SCALE


def test_signed_request_is_read_only_summary_post(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return json.dumps(ZERO_SUMMARY).encode()

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), headers=dict(req.header_items()))
        return Resp()
    monkeypatch.setattr(fees.urllib.request, "urlopen", fake_urlopen)
    rec = fees.fetch_account((KEY, SECRET, "env:X"))
    assert seen["url"] == "https://api.bitfinex.com/v2/auth/r/summary" and seen["method"] == "POST"
    assert seen["headers"]["User-agent"] == fees.USER_AGENT and len(seen["headers"]["Bfx-signature"]) == 96
    assert rec["source"] == fees.ACCOUNT_SUMMARY and rec["derivatives_taker_rate"] == 0


def test_cache_honours_ttl_and_keeps_last_verified_answer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    calls = []

    def ok(creds):
        calls.append(1)
        return dict(ACCOUNT_ZERO)

    def fail(creds):
        calls.append(1)
        raise fees.FeeSourceError("HTTP 500: timeout")
    env = {"BITFINEX_API_KEY": KEY, "BITFINEX_API_SECRET": SECRET}
    fees.refresh_account(home, NOW, creds_env=env, fetch_account_fn=ok)
    fees.refresh_account(home, NOW + 3600, creds_env=env, fetch_account_fn=ok)
    assert len(calls) == 1  # inside the 6 h TTL
    rec = fees.refresh_account(home, NOW + fees.FEE_TTL_SEC + 1, creds_env=env, fetch_account_fn=fail,
                               fetch_public_fn=lambda: pytest.fail("must not downgrade to the public schedule"))
    assert len(calls) == 2 and rec["source"] == fees.ACCOUNT_SUMMARY and rec["fetched_at_epoch"] == NOW
    assert "timeout" in rec["last_attempt"]["account_error"]
    fees.refresh_account(home, NOW + fees.FEE_TTL_SEC + 60, creds_env=env, fetch_account_fn=fail)
    assert len(calls) == 2  # retry window after a failure


# ------------------------------------------------------------- drift

def test_zero_everywhere_is_green(tmp_path):
    doc = fees.run(tmp_path / "home", _repo(tmp_path), NOW, fetch_account_fn=lambda c: dict(ACCOUNT_ZERO),
                   creds_env={"BITFINEX_API_KEY": KEY, "BITFINEX_API_SECRET": SECRET})
    assert doc["status"] == "GREEN" and doc["matches_cost_profile"] is True and doc["matches_everywhere"] is True
    assert (doc["maker_bps"], doc["taker_bps"], doc["derivatives_maker_bps"], doc["derivatives_taker_bps"]) == (0, 0, 0, 0)
    assert doc["source"] == "ACCOUNT_SUMMARY" and doc["stale"] is False and doc["cost_profile"]["fee_profile_id"] == "BITFINEX_ZERO"


def test_profile_vs_account_mismatch_is_red_naming_field(tmp_path):
    paid = {**ACCOUNT_ZERO, "derivatives_taker_rate": 0.00065}
    doc = fees.run(tmp_path / "home", _repo(tmp_path), NOW, fetch_account_fn=lambda c: paid,
                   creds_env={"BITFINEX_API_KEY": KEY, "BITFINEX_API_SECRET": SECRET})
    assert doc["status"] == "RED" and doc["matches_cost_profile"] is False
    assert [(m["file"], m["field"]) for m in doc["mismatches"]] == [(fees.PROFILE_REL, "TAKER_FEE_RATE")]


def test_ts_surface_and_code_literal_drift_is_red(tmp_path):
    root = _repo(tmp_path, sim_bps="4", relay="'BITFINEX_M2_T6.5', maker_fee_rate: 0, taker_fee_rate: 0",
                 extra={"services/btc-conservative-agent/research/sim.py": "TAKER_FEE_PCT = 0.00065\nok = fee_rate\n",
                        "services/btc-conservative-agent/test_sim.py": "TAKER_FEE_PCT = 0.00065\n",
                        "scripts/zero.py": "maker_fee_rate: float = 0.0\n",
                        "scripts/amounts.py": '"maker_fees": 0.0,\n"taker_fees": 0.0,\nentry_fee_usd = 1.5\n'})
    d = fees.drift(root, ACCOUNT_ZERO)
    fields = {(m["file"], m["field"]) for m in d["mismatches"]}
    assert ("apps/api/src/exchanges/bitfinex-sim-trading.client.ts", "SIM_FEE_BPS (as taker)") in fields
    assert ("apps/api/src/trading-agents/signal-subscriber-execution.service.ts",
            "stableRelayFeeModel.configured_profile") in fields
    assert ("services/btc-conservative-agent/research/sim.py", "TAKER_FEE_PCT (line 1)") in fields
    assert not any("test_sim.py" in f for f, _ in fields)  # tests are fixtures, not fee surfaces
    zero = [x for x in d["literals"] if x["file"] == "scripts/zero.py"]
    assert zero and zero[0]["matches_profile"] is True and ("scripts/zero.py", "maker_fee_rate (line 1)") not in fields
    assert not [x for x in d["literals"] if x["file"] == "scripts/amounts.py"]  # USD amounts are not rates


@pytest.mark.parametrize("line,hit", [
    ("TAKER_FEE_PCT = 0.00065", ("TAKER_FEE_PCT", "0.00065")),
    ("maker_fee_rate: float = 0.0002,", ("maker_fee_rate", "0.0002")),
    ('"taker_fee_rate": 0.00055,', ("taker_fee_rate", "0.00055")),
    ("taker_fee: 0.065", ("taker_fee", "0.065")),
    ("const takerFeeBps = 4;", ("takerFeeBps", "4")),
    ("fee_bps = 1e4 * rate", None),
    ("fee_rate=bitfinex_cost_profile.TAKER_FEE_RATE", None),
    ("if fee_rate == 0:", None),
    ("entry_fee_rate=maker_fee,", None),
    ("maker_fee_rate: Optional[float] = None", None),
])
def test_literal_regex(line, hit):
    m = fees.LITERAL_RE.search(line)
    assert ((m.group(1), m.group(2)) if m else None) == hit


def test_fly_revision_and_analyzer_checkout_are_compared(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    v2c = tmp_path / "v2c" / fees.PROFILE_REL
    v2c.parent.mkdir(parents=True)
    v2c.write_text(PROFILE.format(m="0.0002", t="0.00065"), encoding="utf-8")
    monkeypatch.setattr(fees, "_git_show", lambda repo, rev, rel: PROFILE.format(m="0.0", t="0.0"))
    d = fees.drift(root, ACCOUNT_ZERO, fly_rev="29742de53a5c", extra_roots=(tmp_path / "v2c",))
    fly = [s for s in d["surfaces"] if s["file"].startswith("fly@29742de53a5c")]
    assert len(fly) == 2 and all(s["ok"] for s in fly)
    assert {m["field"] for m in d["mismatches"] if m["file"].startswith("v2c:")} == {"MAKER_FEE_RATE", "TAKER_FEE_RATE"}


def test_unverified_public_schedule_is_amber_never_green(tmp_path):
    doc = fees.run(tmp_path / "home", _repo(tmp_path), NOW, creds_env={"SELF_AWARE_BITFINEX_ENV_FILE": str(tmp_path / "none")},
                   fetch_public_fn=lambda: {**ACCOUNT_ZERO, "source": fees.PUBLIC_SCHEDULE_UNVERIFIED})
    assert doc["source"] == "PUBLIC_SCHEDULE_UNVERIFIED" and doc["status"] == "AMBER"
    assert doc["matches_cost_profile"] is True and doc["last_attempt"]["credentials"] == "missing"


def test_stale_account_answer_is_amber(tmp_path):
    env = {"BITFINEX_API_KEY": KEY, "BITFINEX_API_SECRET": SECRET}
    fees.run(tmp_path / "home", _repo(tmp_path), NOW, fetch_account_fn=lambda c: dict(ACCOUNT_ZERO), creds_env=env)

    def fail(c):
        raise fees.FeeSourceError("HTTP 500")
    doc = fees.run(tmp_path / "home", _repo(tmp_path), NOW + fees.STALE_AFTER_SEC + 10, fetch_account_fn=fail, creds_env=env)
    assert doc["source"] == "ACCOUNT_SUMMARY" and doc["stale"] is True and doc["status"] == "AMBER"


# ------------------------------------------------------- health + API

def test_health_check_finding(tmp_path):
    env = {"BITFINEX_API_KEY": KEY, "BITFINEX_API_SECRET": SECRET}
    green = fees.run(tmp_path / "h1", _repo(tmp_path), NOW, fetch_account_fn=lambda c: dict(ACCOUNT_ZERO), creds_env=env)
    f = diagnose.check_fees({"now": NOW, "fees": green}, {}, None)
    assert f.id == "fees.truth" and f.severity == "GREEN" and f.runbook.endswith("#fees-truth")
    red = fees.run(tmp_path / "h2", _repo(tmp_path / "b", sim_bps="4"), NOW,
                   fetch_account_fn=lambda c: dict(ACCOUNT_ZERO), creds_env=env)
    f = diagnose.check_fees({"now": NOW, "fees": red}, {}, None)
    assert f.severity == "RED" and "bitfinex-sim-trading.client.ts SIM_FEE_BPS" in f.observed and f.causes
    old = {**green, "generated_at": iso(NOW - 3 * 3600)}
    f = diagnose.check_fees({"now": NOW, "fees": old}, {}, None)
    assert f.severity == "SKIP" and f.emit_alarm is False


def test_fees_endpoint(tmp_path):
    from self_aware.server import make_server
    env = {"BITFINEX_API_KEY": KEY, "BITFINEX_API_SECRET": SECRET}
    doc = fees.run(tmp_path / "home", _repo(tmp_path), NOW, fetch_account_fn=lambda c: dict(ACCOUNT_ZERO), creds_env=env)

    class FakeEngine:
        docs = {"fees": doc}
        state: dict = {}

        def engine_status(self):
            return {"ok": True}

    srv = make_server(FakeEngine(), 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        get = lambda p: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=10).read())  # noqa: E731
        body = get("/api/selfaware/fees")
        for k in ("venue", "maker_bps", "taker_bps", "derivatives_maker_bps", "derivatives_taker_bps", "source",
                  "fetched_at", "stale", "matches_cost_profile"):
            assert k in body
        assert body["venue"] == "bitfinex" and body["status"] == "GREEN" and "surfaces" not in body
        assert "surfaces" in get("/api/selfaware/fees?detail=1")
    finally:
        srv.shutdown()
        srv.server_close()
