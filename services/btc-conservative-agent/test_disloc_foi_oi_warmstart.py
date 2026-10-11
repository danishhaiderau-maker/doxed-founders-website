"""DISLOC-FOI fired once in a day: oi_d60 needed 60 min of in-memory OI after every restart. Warm-start + tolerant lookup."""
import json

import paper_policy_family_disloc_foi as foi


def _row(minute, oi_b, oi_o):
    return {"minute_ts": minute * 60, "derivatives": {"bybit": {"open_interest": oi_b}, "okx": {"open_interest": oi_o}}}


def test_seed_from_tape_gives_oi_d60_immediately_after_restart(tmp_path):
    now_min = 30_000_000
    path = tmp_path / "cross_venue_tape_1m.jsonl"
    with open(path, "w") as fh:
        for m in range(now_min - 70, now_min):
            if m == now_min - 60:
                continue  # missing exact minute must not blank the feature
            fh.write(json.dumps(_row(m, 50_000.0, 30_000.0)) + "\n")
    ev = foi.make_evaluator()
    ev.oi_seed_fn = ev.make_tape_oi_seed(str(path))
    live = {"derivatives": {"bybit": {"open_interest": 49_800.0, "funding_rate": 3e-5},
                            "okx": {"open_interest": 29_800.0, "funding_rate": 3e-5},
                            "binance": {"funding_rate": 3e-5}}}
    out = ev._derivs(now_min * 60 + 5, live)
    assert out["oi_d60_bp"] is not None
    assert abs(out["oi_d60_bp"] - (79_600 / 80_000 - 1) * 1e4) < 1e-3
    assert out["fund_bp"] == 0.3


def test_no_tape_still_safe(tmp_path):
    ev = foi.make_evaluator()
    ev.oi_seed_fn = ev.make_tape_oi_seed(str(tmp_path / "missing.jsonl"))
    out = ev._derivs(1_800_000_000.0, {"derivatives": {}})
    assert out["oi_d60_bp"] is None and ev.counters["errors"] == 0
