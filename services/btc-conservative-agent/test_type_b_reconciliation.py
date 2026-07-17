"""Pt 7b: TYPE_B_HUNTER_V1 trade-count reconciliation test.

Verifies the diagnostic helper correctly cross-checks the three independent
trade-count sources (paper ledger, shadow outcome rows, live open positions)
and surfaces mismatches rather than silently masking them.

Run: cd services/btc-conservative-agent && python test_type_b_reconciliation.py
"""
import json
import os
import shutil
import sys
import tempfile

# Force paper mode so no real Bitfinex orders are ever attempted.
os.environ["FORCE_PAPER_MODE"] = "1"
os.environ["SKIP_EXCHANGE_MARKET_LOAD"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _run_case(name, setup_fn, expected):
    """Run one reconciliation case against an isolated cwd with seeded files."""
    cwd = tempfile.mkdtemp(prefix="tb_recon_")
    prev_cwd = os.getcwd()
    try:
        os.chdir(cwd)
        # Re-import bot fresh so LANE_PNL_LEDGER_FILE etc resolve to cwd
        for mod_name in list(sys.modules.keys()):
            if mod_name == "bot" or mod_name.startswith("bot."):
                del sys.modules[mod_name]
        import bot

        setup_fn(bot)
        result = bot._reconcile_type_b_trade_count()
        for key, want in expected.items():
            got = result.get(key)
            if got != want:
                print(f"  [FAIL] {name}: {key} expected {want!r}, got {got!r}")
                print(f"         full result: {result}")
                return False
        print(f"  [PASS] {name}: paper={result['paper_closes']} "
              f"shadow={result['shadow_closes']} live={result['live_open']} "
              f"consistent={result['consistent']}")
        return True
    finally:
        os.chdir(prev_cwd)
        shutil.rmtree(cwd, ignore_errors=True)


def _seed_ledger(bot, closes):
    """Write a lane_pnl_ledger.json with the given close count for TYPE_B."""
    lane = bot.RESEARCH_LANE_TYPE_B_HUNTER_V1
    payload = {
        "lanes": {
            lane: {"closes": closes, "net_pnl_usd": 0.0, "wins": 0, "losses": 0},
        }
    }
    with open(bot.LANE_PNL_LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _seed_shadow(bot, n_filled, policy_v):
    """Append n_filled filled shadow outcome rows for TYPE_B."""
    lane = bot.RESEARCH_LANE_TYPE_B_HUNTER_V1
    with open(bot.SHADOW_LANE_OUTCOME_FILE, "w", encoding="utf-8") as f:
        for i in range(n_filled):
            row = {
                "research_lane": lane,
                "trade_id": f"tb_test_{i}",
                "filled": True,
                "policy_version": policy_v,
                "net_pnl_usd": 0.0,
            }
            f.write(json.dumps(row) + "\n")


def case_consistent():
    def setup(bot):
        pv = bot._type_b_policy_version()
        _seed_ledger(bot, 5)
        _seed_shadow(bot, 5, pv)
    return _run_case(
        "paper == shadow -> consistent",
        setup,
        {"consistent": True, "paper_closes": 5, "shadow_closes": 5, "mismatch_note": ""},
    )


def case_shadow_exceeds_paper():
    def setup(bot):
        pv = bot._type_b_policy_version()
        _seed_ledger(bot, 3)
        _seed_shadow(bot, 8, pv)
    return _run_case(
        "shadow > paper -> diagnostic note",
        setup,
        {
            "consistent": False,
            "paper_closes": 3,
            "shadow_closes": 8,
        },
    )


def case_paper_exceeds_shadow():
    def setup(bot):
        pv = bot._type_b_policy_version()
        _seed_ledger(bot, 20)
        _seed_shadow(bot, 7, pv)
    return _run_case(
        "paper > shadow -> older cohort note",
        setup,
        {
            "consistent": False,
            "paper_closes": 20,
            "shadow_closes": 7,
        },
    )


def case_policy_filter():
    """Shadow rows for an OLD policy version must not be counted."""
    def setup(bot):
        _seed_ledger(bot, 0)
        # Current policy_version with 2 filled
        _seed_shadow(bot, 2, bot._type_b_policy_version())
        # Append 50 rows for an outdated policy_version -- must be ignored
        lane = bot.RESEARCH_LANE_TYPE_B_HUNTER_V1
        with open(bot.SHADOW_LANE_OUTCOME_FILE, "a", encoding="utf-8") as f:
            for i in range(50):
                f.write(json.dumps({
                    "research_lane": lane,
                    "trade_id": f"tb_old_{i}",
                    "filled": True,
                    "policy_version": "type_b_v9_OLDER",
                    "net_pnl_usd": 0.0,
                }) + "\n")
    return _run_case(
        "policy_version filter excludes older cohorts",
        setup,
        {"shadow_closes": 2, "paper_closes": 0, "consistent": False},
    )


def case_dedup():
    """Repeated study_id rows must collapse -- only one counts per trade_id."""
    def setup(bot):
        pv = bot._type_b_policy_version()
        _seed_ledger(bot, 1)
        lane = bot.RESEARCH_LANE_TYPE_B_HUNTER_V1
        with open(bot.SHADOW_LANE_OUTCOME_FILE, "w", encoding="utf-8") as f:
            for _ in range(3):
                f.write(json.dumps({
                    "research_lane": lane,
                    "trade_id": "tb_dup_1",
                    "filled": True,
                    "policy_version": pv,
                }) + "\n"
    )
    return _run_case(
        "duplicate trade_id collapses",
        setup,
        {"shadow_closes": 1, "paper_closes": 1, "consistent": True},
    )


def case_empty():
    def setup(bot):
        pass  # No files at all
    return _run_case(
        "no files -> all zero, consistent",
        setup,
        {"consistent": True, "paper_closes": 0, "shadow_closes": 0, "live_open": 0},
    )


def main():
    print("=" * 78)
    print("Pt 7b: TYPE_B_HUNTER_V1 trade-count reconciliation")
    print("=" * 78)
    cases = [
        case_empty,
        case_consistent,
        case_shadow_exceeds_paper,
        case_paper_exceeds_shadow,
        case_policy_filter,
        case_dedup,
    ]
    passed = 0
    failed = 0
    for c in cases:
        try:
            if c():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  [ERROR] {c.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print()
    print("=" * 78)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 78)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
