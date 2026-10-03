import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grokbot_digest as gd  # noqa: E402

# Assembled at runtime so secret scanners do not flag a test fixture.
SECRET = "".join(["fake", "-cred-", "Qz7Lm", "Xw2Rt", "Kp9Vn", "Hs4Jd", "Bc8Fg", "Ty3Ue"])
LANE = "FAMILY_XVENUE_SESSION_FOLLOW_60M"


def _payloads():
    return {
        gd.SOURCES["watcher"]: {
            "verdict": "AMBER", "generated_at": "2026-10-03T13:25:38Z", "age_sec": 120, "stale": False,
            "counts": {"GREEN": 42, "AMBER": 1, "RED": 0, "SKIP": 0},
            "failing": [{"id": "railway.relay", "status": "AMBER",
                         "observed": f"read C:\\DoxxedCrypto\\vault\\home-bot.env token={SECRET}",
                         "threshold": "relay PAUSED", "hint": "private hint", "detail": {"raw": SECRET}}],
            "checks": [{"id": "x", "observed": SECRET}],
            "open_alarms": [{"id": "a"}],
        },
        gd.SOURCES["selfaware"]: {
            "verdict": "RED", "counts": {"RED": 1},
            "findings": [
                {"id": "fees.truth", "severity": "RED", "observed": "SIM_FEE_BPS 4 vs 0",
                 "drill_sql": "SELECT * FROM raw_x", "evidence": {"key": SECRET}},
                {"id": "ok.check", "severity": "GREEN", "observed": "fine"},
            ],
            "engine": {"store": "C:\\DoxxedCrypto\\self-aware\\selfaware.duckdb",
                       "jobs": {"diagnose": {"last_ok": "2026-10-03T13:20:00Z", "result": {"x": 1}}}},
        },
        gd.SOURCES["analyzer_insights"]: {"components": {"analyzer_export": {"data": {"tile_pool": [
            {"key": LANE, "n": 448, "mean_usd": -0.0048, "corrected_verdict": "NEGATIVE_FWER",
             "label": "long label", "rows": [1, 2]}]}}}},
        gd.SOURCES["analyzer_readiness"]: {
            "status": "NO QUALIFIED POLICY", "real_bitfinex_trading_allowed": False,
            "qualification_gate_details": [{"gate": "regime_diversity", "status": "FAIL"},
                                           {"gate": "conservative_execution", "status": "PASS"}]},
        gd.SOURCES["selfaware_fees"]: {"status": "RED", "mismatches": [{"file": "a.ts"}, {"file": "b.ts"}]},
    }


def _fetch(payloads):
    def fetch(url):
        if url in payloads:
            return payloads[url], None
        return None, "UNREACHABLE"
    return fetch


def test_digest_is_allowlisted_and_redacted():
    digest = gd.build(_fetch(_payloads()), now=1791034000.0)
    text = json.dumps(digest)
    assert SECRET not in text
    assert "DoxxedCrypto" not in text and "home-bot.env" not in text
    assert "drill_sql" not in text and "evidence" not in text and "hint" not in text
    assert "checks" not in digest["watcher"]
    failing = digest["watcher"]["failing"][0]
    assert set(failing) <= {"id", "status", "observed", "threshold", "last_good_at"}
    assert "<redacted>" in failing["observed"] and "<path>" in failing["observed"]
    assert digest["watcher"]["open_alarms"] == 1
    assert [f["id"] for f in digest["selfaware"]["findings"]] == ["fees.truth"]
    assert digest["selfaware"]["jobs_last_ok"] == {"diagnose": "2026-10-03T13:20:00Z"}
    assert digest["tile_verdicts"] == [{"key": LANE, "n": 448, "mean_usd": -0.0048,
                                        "corrected_verdict": "NEGATIVE_FWER"}]
    assert digest["decision_readiness"]["failed_gates"] == ["regime_diversity"]
    assert digest["fees"] == {"status": "RED", "mismatch_count": 2}
    assert digest["read_only"] is True and digest["schema"] == gd.SCHEMA


def test_unreachable_sources_fail_closed_not_green():
    digest = gd.build(_fetch({}), now=1791034000.0)
    assert all(not s["ok"] and s["error"] == "UNREACHABLE" for s in digest["sources"].values())
    assert digest["watcher"]["failing"] == [] and "verdict" not in digest["watcher"]
    assert digest["tile_verdicts"] == [] and digest["tiles_24h"] == []


def test_http_json_refuses_non_local_urls():
    for url in ("https://doxed-btc-bot.fly.dev/api/state", "http://example.com:9011/", "http://localhost:9011/"):
        assert gd.http_json(url) == (None, "NON_LOCAL_URL_REFUSED")
    assert all(u.startswith("http://127.0.0.1:") for u in gd.SOURCES.values())


def test_scrub_truncates_and_redacts():
    assert "abc.def" not in gd.scrub("Authorization: Bearer abc.def")
    assert "hunter2" not in gd.scrub("password=hunter2 ok")
    assert gd.scrub("https://x.dev/api?token=abc&u=1") == "https://x.dev/api?<redacted>"
    assert len(gd.scrub("word " * 200)) == gd.MAX_STR
    assert gd.scrub("x" * 64) == "x" * 12 + "…"
    assert gd.scrub(None) is None and gd.scrub(3) == 3 and gd.scrub(True) is True


def test_write_atomic(tmp_path):
    out = tmp_path / "health" / "grokbot-digest.json"
    gd.write_atomic(out, {"schema": gd.SCHEMA})
    assert json.loads(out.read_text(encoding="utf-8")) == {"schema": gd.SCHEMA}
    assert not list(out.parent.glob("*.tmp"))


def test_ai_scorecard_headline_is_bounded_to_allowlisted_cells():
    cell = {"n": 177, "hit_rate": 0.4124, "net_bp": -2.03, "net_lo": -3.96, "net_hi": -0.16, "rows": [SECRET]}
    scorecard = {"headline": {"24h": {"5m": {"AI_ABSTAIN_RESPECTING": cell, "INVERT_AI": cell},
                                      "60m": {"RANDOM": dict(cell, n=1106)}}},
                 "rows": [{"sql": SECRET}], "provenance": {"path": "C:\\DoxxedCrypto\\x.duckdb"}}
    digest = gd.build(_fetch({gd.SOURCES["selfaware_ai"]: scorecard}), now=1791034000.0)
    ai = digest["ai_scorecard"]
    assert SECRET not in json.dumps(digest) and "DoxxedCrypto" not in json.dumps(digest)
    assert ai == {"24h": {"5m": {"AI_ABSTAIN_RESPECTING": {k: cell[k] for k in ("n", "hit_rate", "net_bp",
                                                                                "net_lo", "net_hi")}},
                          "60m": {"RANDOM": {"n": 1106, "hit_rate": 0.4124, "net_bp": -2.03, "net_lo": -3.96,
                                             "net_hi": -0.16}}}}


def test_overrides_replace_a_source_without_fetching_it():
    fetched = []

    def fetch(url):
        fetched.append(url)
        return None, "UNREACHABLE"

    report = {"verdict": "GREEN", "counts": {"GREEN": 3}, "failing": [], "open_alarms": [], "age_sec": 0.0}
    digest = gd.build(fetch, now=1791034000.0, overrides={"watcher": report})
    assert gd.SOURCES["watcher"] not in fetched and len(fetched) == len(gd.SOURCES) - 1
    assert digest["sources"]["watcher"] == {"ok": True, "error": None}
    assert digest["watcher"]["verdict"] == "GREEN"
