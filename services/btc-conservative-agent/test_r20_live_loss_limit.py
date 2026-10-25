"""R20 live-loss limit (Danish 25 Oct 14:27 AEDT): $10 per day and $10 in total, across ALL live tiles combined.
Hitting either limit disarms every live tile and forces flat. Bots may not raise or remove it: env can only LOWER it.
Gate tests for RK (R20): $9.99 allowed, $10.00 trips, for both the daily and the total limit."""
import os
import pathlib
import re
import subprocess
import sys

import live_guardian as lg
import live_tile_gate as ltg

HERE = pathlib.Path(__file__).resolve().parent
NOW = 1_800_000_000.0


def _real(pnl, at, lane):
    return {"correlation_id": f"{lane}-{pnl}-{at}", "trade_id": f"{lane}-{pnl}-{at}", "lane": lane, "closed": True,
            "pnl_usd": pnl, "closed_at_ts": at, "exit_fill_at_ts": at, "fill_at_ts": at - 60,
            "fill_price": 100_000.0, "fill_qty": 0.01, "side": "LONG", "exit_fill_price": 100_000.0 + pnl / 0.01,
            "exit_price_source": "exchange_fill", "fill_price_source": "exchange_trades"}


def _codes(v):
    return {x["code"] for x in v}


def test_r20_one_shared_constant_is_ten_dollars():
    assert ltg.R20_LIVE_LOSS_LIMIT_USD == 10.00
    assert ltg.DAILY_LOSS_CAP_TOTAL_USD == ltg.DAILY_LOSS_CAP_PER_TILE_USD == 10.00
    assert lg.DAILY_LOSS_CAP_USD == lg.LIVE_TOTAL_LOSS_CAP_USD == 10.00
    assert "14:27" in ltg.DAILY_LOSS_CAP_SOURCE and "14:27" in lg.LIVE_TOTAL_LOSS_CAP_SOURCE
    # hitting either limit disarms ALL + force-flat (RED), not just an entry block
    for code in (lg.VIOL_LIVE_TOTAL_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP, "DAILY_LOSS_CAP"):
        assert code in lg.RED_CODES and lg.split_violations([{"code": code, "tile": None}])[0], code


def test_r20_daily_limit_999_allowed_1000_trips_combined_across_tiles():
    lanes = ("FAMILY_GS01_XV", "FAMILY_GF3")
    under = [_real(-4.99, NOW - 120, lanes[0]), _real(-5.00, NOW - 60, lanes[1])]     # $9.99 combined
    at = [_real(-5.00, NOW - 120, lanes[0]), _real(-5.00, NOW - 60, lanes[1])]        # $10.00 combined
    assert ltg.VIOL_TOTAL_LOSS_CAP not in _codes(ltg.daily_loss_violations(under, NOW))
    assert ltg.VIOL_TOTAL_LOSS_CAP in _codes(ltg.daily_loss_violations(at, NOW))
    # guardian rolling-24 h daily cap (same constant)
    st = {}
    assert "DAILY_LOSS_CAP" not in _codes(lg.evaluate(under, None, st, NOW))
    assert st["ledger_loss_usd"] == 9.99
    assert "DAILY_LOSS_CAP" in _codes(lg.evaluate(at, None, {}, NOW))
    assert "DAILY_LOSS_CAP" in _codes(lg.evaluate([_real(-10.00, NOW - 60, lanes[0])], None, {}, NOW))


def test_r20_total_limit_999_allowed_1000_trips_across_days():
    days = [_real(-3.33, NOW - 86400 * k - 60, "FAMILY_GS01_XV") for k in (3, 2, 1)]    # $9.99 over 3 days
    assert lg.total_live_loss_violations(days) == []
    v = lg.total_live_loss_violations(days + [_real(-0.01, NOW - 60, "FAMILY_GF3")])   # $10.00
    assert _codes(v) == {lg.VIOL_LIVE_TOTAL_LOSS_CAP}
    assert lg.split_violations(v)[0] == v                                                # disarm ALL + force-flat
    assert lg.total_loss_arm_block(days) is None
    assert lg.total_loss_arm_block(days + [_real(-0.01, NOW, "FAMILY_GF3")]) == lg.VIOL_LIVE_TOTAL_LOSS_CAP


def test_r20_env_can_only_lower_the_limit():
    f = ltg.r20_loss_limit_usd
    assert f({}) == 10.00
    for raise_ in ("10.01", "50", "1e9", "inf"):
        assert f({"LIVE_LOSS_LIMIT_USD": raise_}) == 10.00, raise_
    for bad in ("", "abc", "nan", "-1"):
        assert f({"LIVE_LOSS_LIMIT_USD": bad}) == 10.00, bad
    assert f({"LIVE_LOSS_LIMIT_USD": "2.5"}) == 2.5 and f({"LIVE_LOSS_LIMIT_USD": "0"}) == 0.0


def test_r20_env_cannot_raise_the_loaded_module_constants():
    code = ("import live_guardian as lg, live_tile_gate as ltg;"
            "print(ltg.DAILY_LOSS_CAP_TOTAL_USD, ltg.DAILY_LOSS_CAP_PER_TILE_USD, lg.DAILY_LOSS_CAP_USD, lg.LIVE_TOTAL_LOSS_CAP_USD)")
    env = dict(os.environ, LIVE_LOSS_LIMIT_USD="1000")
    out = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.split() == ["10.0"] * 4


def test_r20_no_other_source_assigns_the_limits():
    names = r"(R20_LIVE_LOSS_LIMIT_USD|LIVE_TOTAL_LOSS_CAP_USD|DAILY_LOSS_CAP_USD|DAILY_LOSS_CAP_TOTAL_USD|DAILY_LOSS_CAP_PER_TILE_USD)"
    pat = re.compile(names + r"\s*(=|\+=|-=)(?!=)|setattr\([^)]*" + names)
    allowed = {"live_tile_gate.py": ["R20_LIVE_LOSS_LIMIT_USD = 10.00", "DAILY_LOSS_CAP_TOTAL_USD = r20_loss_limit_usd()",
                                     "DAILY_LOSS_CAP_PER_TILE_USD = DAILY_LOSS_CAP_TOTAL_USD"],
               "live_guardian.py": ["DAILY_LOSS_CAP_USD = _ltg_caps.DAILY_LOSS_CAP_TOTAL_USD",
                                    "LIVE_TOTAL_LOSS_CAP_USD = _ltg_caps.r20_loss_limit_usd()"]}
    for p in HERE.glob("*.py"):
        if p.name.startswith("test_") or p.name == "conftest.py":
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        hits = [l.strip() for l in src.splitlines() if pat.search(l) and not l.lstrip().startswith("#")]
        assert hits == allowed.get(p.name, []), (p.name, hits)


# ---- RK conditions on #885 ----
def test_r20_env_above_ten_is_clamped_to_exactly_ten():
    # r20_loss_limit_usd has no logger (pure function); the clamp itself is asserted
    for above in ("10.000001", "10.5", "11", "100", "99999"):
        got = ltg.r20_loss_limit_usd({"LIVE_LOSS_LIMIT_USD": above})
        assert got == 10.00 and type(got) is float, (above, got)


def test_r20_pins_exactly_ten_dollars_daily_total_and_guardian_rolling():
    assert ltg.DAILY_LOSS_CAP_TOTAL_USD == 10.00          # daily (combined, UTC day)
    assert lg.LIVE_TOTAL_LOSS_CAP_USD == 10.00            # all-time total
    assert lg.DAILY_LOSS_CAP_USD == 10.00                 # guardian rolling-24 h


def test_r20_small_loss_limit_fixture_is_opt_in_and_test_only():
    import conftest
    fx = conftest.small_loss_limit
    marker = getattr(fx, "_pytestfixturefunction", None) or getattr(fx, "_fixture_function_marker", None)
    assert marker is not None and marker.autouse is False
    for p in HERE.rglob("*.py"):
        if p.name.startswith("test_") or p.name == "conftest.py" or "tests" in p.parts:
            continue
        assert "small_loss_limit" not in p.read_text(encoding="utf-8", errors="ignore"), p.name


def test_r20_production_loads_ten_with_no_fixture_and_no_env():
    code = ("import live_guardian as lg, live_tile_gate as ltg;"
            "print(ltg.DAILY_LOSS_CAP_TOTAL_USD, lg.LIVE_TOTAL_LOSS_CAP_USD, lg.DAILY_LOSS_CAP_USD)")
    env = {k: v for k, v in os.environ.items() if k != "LIVE_LOSS_LIMIT_USD"}
    out = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.split() == ["10.0"] * 3
