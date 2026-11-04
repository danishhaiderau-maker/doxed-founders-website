"""Fee truth: public Bitfinex fee schedule vs bitfinex_cost_profile and every other fee surface."""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import diagnose, fees  # noqa: E402
from self_aware.facts import iso  # noqa: E402

NOW = 1_790_930_000.0
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

def test_load_credentials_never_reads_bitfinex_keys(tmp_path):
    """HM HOLD ec34b9b9d0d9 / 716511e8582d: no Bitfinex creds; signed helpers deleted."""
    env_file = tmp_path / "bot.env"
    env_file.write_text(f'BITFINEX_API_KEY="{KEY}"\nBITFINEX_API_SECRET={SECRET}\n', encoding="utf-8")
    assert fees.load_credentials({"SELF_AWARE_BITFINEX_ENV_FILE": str(env_file)}) is None
    assert fees.load_credentials({
        "BITFINEX_READ_API_KEY": "r",
        "BITFINEX_READ_API_SECRET": "x",
        "BITFINEX_API_KEY": KEY,
        "BITFINEX_API_SECRET": SECRET,
    }) is None
    assert fees.load_credentials(None) is None
    src = Path(fees.__file__).read_text(encoding="utf-8")
    assert "def load_credentials" in src and "return None" in src
    assert "return env[k], env[s]" not in src
    assert "return vals[k], vals[s]" not in src
    assert "Never load Bitfinex API credentials" in src
    # Dead signed helpers removed (HOLD 716511e8582d).
    assert "def fetch_account" not in src
    assert "def _post_summary" not in src
    assert "def _nonce" not in src
    assert "def parse_summary" not in src
    assert "import hmac" not in src


def test_refresh_account_public_only_never_calls_account(tmp_path):
    """Public schedule only — even when BITFINEX_* names are present in creds_env."""
    home = tmp_path / "home"
    home.mkdir()
    public_calls: list[int] = []

    def public():
        public_calls.append(1)
        return {
            "maker_rate": 0.0,
            "taker_rate": 0.0,
            "derivatives_maker_rate": 0.0,
            "derivatives_taker_rate": 0.0,
            "source": fees.PUBLIC_SCHEDULE_UNVERIFIED,
            "endpoint": "https://www.bitfinex.com/fees/",
            "zero_fee_language_found": True,
        }

    rec = fees.refresh_account(
        home,
        NOW,
        creds_env={"BITFINEX_API_KEY": KEY, "BITFINEX_API_SECRET": SECRET},
        fetch_public_fn=public,
    )
    assert len(public_calls) == 1
    assert rec["source"] == fees.PUBLIC_SCHEDULE_UNVERIFIED
    assert rec["last_attempt"]["credentials"] == "disabled_public_only"
    assert KEY not in json.dumps(rec)
    assert SECRET not in json.dumps(rec)


def test_cache_honours_ttl_for_public_schedule(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    calls: list[int] = []

    def public():
        calls.append(1)
        return {**ACCOUNT_ZERO, "source": fees.PUBLIC_SCHEDULE_UNVERIFIED,
                "endpoint": "https://www.bitfinex.com/fees/"}

    fees.refresh_account(home, NOW, fetch_public_fn=public)
    fees.refresh_account(home, NOW + 60, fetch_public_fn=public)
    # Public path uses FEE_RETRY_SEC as retry window (not ACCOUNT TTL).
    assert len(calls) == 1
    fees.refresh_account(home, NOW + fees.FEE_RETRY_SEC + 1, fetch_public_fn=public)
    assert len(calls) == 2


def _seed_legacy_account_cache(home: Path, now: float = NOW, *, retry_at: float | None = None) -> None:
    """Seed a pre-rev5 ACCOUNT_SUMMARY cache row (legacy; refresh no longer fetches account)."""
    home.mkdir(parents=True, exist_ok=True)
    rec = {
        **ACCOUNT_ZERO,
        "fetched_at": iso(now),
        "fetched_at_epoch": now,
        "last_attempt": {"credentials": "legacy_seed"},
        "retry_at_epoch": now + fees.FEE_TTL_SEC if retry_at is None else retry_at,
    }
    (home / fees.CACHE_NAME).write_text(json.dumps(rec), encoding="utf-8")


# ------------------------------------------------------------- drift

def test_zero_everywhere_public_is_amber_match(tmp_path):
    doc = fees.run(
        tmp_path / "home",
        _repo(tmp_path),
        NOW,
        fetch_public_fn=lambda: {**ACCOUNT_ZERO, "source": fees.PUBLIC_SCHEDULE_UNVERIFIED},
    )
    assert doc["status"] == "AMBER" and doc["matches_cost_profile"] is True and doc["matches_everywhere"] is True
    assert (doc["maker_bps"], doc["taker_bps"], doc["derivatives_maker_bps"], doc["derivatives_taker_bps"]) == (0, 0, 0, 0)
    assert doc["source"] == fees.PUBLIC_SCHEDULE_UNVERIFIED
    assert doc["cost_profile"]["fee_profile_id"] == "BITFINEX_ZERO"
    assert doc["last_attempt"]["credentials"] == "disabled_public_only"


def test_profile_vs_account_mismatch_is_red_naming_field(tmp_path):
    paid = {**ACCOUNT_ZERO, "derivatives_taker_rate": 0.00065}
    dr = fees.drift(_repo(tmp_path), paid)
    doc = fees.build_doc(paid | {"fetched_at": iso(NOW), "fetched_at_epoch": NOW}, dr, NOW)
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
    doc = fees.run(
        tmp_path / "home",
        _repo(tmp_path),
        NOW,
        fetch_public_fn=lambda: {**ACCOUNT_ZERO, "source": fees.PUBLIC_SCHEDULE_UNVERIFIED},
    )
    assert doc["source"] == "PUBLIC_SCHEDULE_UNVERIFIED" and doc["status"] == "AMBER"
    assert doc["matches_cost_profile"] is True
    assert doc["last_attempt"]["credentials"] == "disabled_public_only"


def test_stale_legacy_account_cache_is_amber(tmp_path):
    home = tmp_path / "home"
    later = NOW + fees.STALE_AFTER_SEC + 10
    _seed_legacy_account_cache(home, NOW, retry_at=later + 3600)
    doc = fees.run(home, _repo(tmp_path), later,
                   fetch_public_fn=lambda: pytest.fail("must keep legacy account cache while retry window holds"))
    assert doc["source"] == "ACCOUNT_SUMMARY" and doc["stale"] is True and doc["status"] == "AMBER"


# ------------------------------------------------------- health + API

def test_health_check_finding(tmp_path):
    # GREEN still requires ACCOUNT_SUMMARY; seed legacy cache for that finding.
    home = tmp_path / "h1"
    _seed_legacy_account_cache(home, NOW)
    green = fees.run(home, _repo(tmp_path), NOW,
                     fetch_public_fn=lambda: pytest.fail("fresh account cache must short-circuit"))
    f = diagnose.check_fees({"now": NOW, "fees": green}, {}, None)
    assert f.id == "fees.truth" and f.severity == "GREEN" and f.runbook.endswith("#fees-truth")
    red = fees.run(
        tmp_path / "h2",
        _repo(tmp_path / "b", sim_bps="4"),
        NOW,
        fetch_public_fn=lambda: {**ACCOUNT_ZERO, "source": fees.PUBLIC_SCHEDULE_UNVERIFIED},
    )
    f = diagnose.check_fees({"now": NOW, "fees": red}, {}, None)
    assert f.severity == "RED" and "bitfinex-sim-trading.client.ts SIM_FEE_BPS" in f.observed and f.causes
    old = {**green, "generated_at": iso(NOW - 3 * 3600)}
    f = diagnose.check_fees({"now": NOW, "fees": old}, {}, None)
    assert f.severity == "SKIP" and f.emit_alarm is False


def test_fees_endpoint(tmp_path):
    pytest.importorskip("pandas")  # server stack pulls optional analytics deps
    from self_aware.server import make_server
    doc = fees.run(
        tmp_path / "home",
        _repo(tmp_path),
        NOW,
        fetch_public_fn=lambda: {**ACCOUNT_ZERO, "source": fees.PUBLIC_SCHEDULE_UNVERIFIED},
    )

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
        assert body["venue"] == "bitfinex" and body["status"] == "AMBER" and "surfaces" not in body
        assert "surfaces" in get("/api/selfaware/fees?detail=1")
    finally:
        srv.shutdown()
        srv.server_close()
