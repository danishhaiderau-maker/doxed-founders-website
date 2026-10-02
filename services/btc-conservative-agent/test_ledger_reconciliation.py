import unittest

import ledger_reconciliation as lr

EPOCH = "epoch-v22-test"
LANES = ["FAMILY_TREND_FADE_60", "FAMILY_XVENUE_LEAD_60S"]
FTF, XVL = LANES


def row(tid, lane=FTF, close="2026-10-02T01:00:00+00:00", exact=0.004, cents=0.0,
        basis="TERMINAL_COST_RECEIPT_EXACT", epoch=EPOCH):
    return {"trade_id": tid, "research_lane": lane, "close_ts": close, "pnl_exact": exact,
            "pnl_cents": cents, "net_pnl_basis": basis, "epoch_id": epoch}


class BuildReportTest(unittest.TestCase):
    def test_canonical_win_pct_uses_exact_pnl_and_counts_breakeven(self):
        raw = [row("a", exact=0.004, cents=0.0), row("b", exact=-0.03, cents=-0.03), row("c", exact=0.0, cents=0.0)]
        rep = lr.build_report(raw_rows=raw, cohort_ids=["a", "b", "c"], quarantine_rows=[], lanes=LANES,
                              epoch_id=EPOCH)
        ftf = rep["analyzer_cohort"][FTF]
        self.assertEqual((ftf["n"], ftf["wins"], ftf["losses"], ftf["flat"]), (3, 1, 1, 1))
        self.assertEqual(ftf["win_pct"], 33.3)
        self.assertEqual(rep["level"], "GREEN")
        self.assertEqual(rep["cents_display_drift"][FTF], {"win_pct_exact": 33.3, "win_pct_cents": 0.0})

    def test_quarantine_explains_drop_but_silent_drop_is_red(self):
        raw = [row("a"), row("b"), row("c")]
        rep = lr.build_report(raw_rows=raw, cohort_ids=["a"], lanes=LANES, epoch_id=EPOCH,
                              quarantine_rows=[{"trade_id": "b", "reason": "AI_PROVIDER_TIMEOUT_OUTAGE"}])
        self.assertEqual(rep["level"], "RED")
        self.assertEqual([t["trade_id"] for t in rep["unexplained_drops"]], ["c"])
        self.assertEqual(rep["quarantined"][FTF], 1)
        self.assertEqual(rep["mirror_ledger"][FTF]["n"], 3)
        self.assertEqual(rep["analyzer_cohort"][FTF]["n"], 1)

    def test_prior_epoch_and_retired_lanes_are_outside_the_reconciliation(self):
        raw = [row("a"), row("old", epoch="epoch-prior"), row("ret", lane="FAMILY_CHANDELIER_3")]
        rep = lr.build_report(raw_rows=raw, cohort_ids=["a"], quarantine_rows=[], lanes=LANES, epoch_id=EPOCH)
        self.assertEqual(rep["level"], "GREEN")
        self.assertEqual([t["trade_id"] for t in rep["trades"]], ["a"])

    def test_inexact_cohort_pnl_is_amber(self):
        rep = lr.build_report(raw_rows=[row("a", basis="CSV_CENTS")], cohort_ids=["a"], quarantine_rows=[],
                              lanes=LANES, epoch_id=EPOCH)
        self.assertEqual(rep["level"], "AMBER")


def mrow(tid, lane, close, pnl):
    return {"trade_id": tid, "research_lane": lane, "epoch_id": EPOCH, "close_ts": close, "net_pnl_usd": pnl}


class CompareWithFlyTest(unittest.TestCase):
    """Fly lists only its newest closes; counts come from lane_pnl_ledger."""

    def setUp(self):
        raw = [row("a", close="2026-10-02T01:00:00+00:00", exact=0.004, cents=0.0),
               row("b", close="2026-10-02T02:00:00+00:00", exact=-0.031, cents=-0.03),
               row("x", lane=XVL, close="2026-10-02T03:00:00+00:00", exact=0.02, cents=0.02)]
        self.report = lr.build_report(raw_rows=raw, cohort_ids=["a", "b", "x"], quarantine_rows=[], lanes=LANES,
                                      epoch_id=EPOCH, source_data_through="2026-10-02T03:00:00+00:00")
        self.mirror = [mrow("a", FTF, "2026-10-02T01:00:00+00:00", 0.0),
                       mrow("b", FTF, "2026-10-02T02:00:00+00:00", -0.03),
                       mrow("x", XVL, "2026-10-02T03:00:00+00:00", 0.02),
                       mrow("c", FTF, "2026-10-02T04:00:00+00:00", 0.05)]

    def fly(self, ftf_closes=4, ftf_pnl=0.10, listed=None):
        listed = listed if listed is not None else [
            {"trade_id": "c", "research_lane": FTF, "ts": "2026-10-02T04:00:00+00:00", "net_pnl_usd": 0.05},
            {"trade_id": "d", "research_lane": FTF, "ts": "2026-10-02T04:30:00+00:00", "net_pnl_usd": 0.08},
        ]
        return {
            "trades": listed,
            "lane_pnl_ledger": {FTF: {"closes": ftf_closes, "net_pnl_usd": ftf_pnl, "wins": 2},
                                XVL: {"closes": 1, "net_pnl_usd": 0.02, "wins": 1}},
            "pathway_lane_specs": {"lanes": [{"lane": FTF, "session_stats": {"win_rate_pct": 75.0}}]},
        }

    def test_fly_equals_mirror_plus_newer_listed_closes(self):
        out = lr.compare_with_fly(self.report, self.fly(), self.mirror)
        self.assertEqual(out["level"], "GREEN", out["reasons"])
        lane = out["breakdown"]["per_lane"][FTF]
        self.assertEqual((lane["fly_n"], lane["mirror_n"], lane["fly_newer_than_mirror"], lane["analyzer_n"]),
                         (4, 3, 1, 2))
        self.assertEqual(lane["win_pct"], 50.0)
        self.assertEqual(lane["fly_tile_win_pct"], 75.0)
        self.assertEqual(lane["fly_ledger_win_pct_cents"], 50.0)
        self.assertTrue(out["breakdown"]["bounded"])

    def test_count_mismatch_inside_the_listed_window_is_flagged(self):
        self.assertEqual(lr.compare_with_fly(self.report, self.fly(ftf_closes=5), self.mirror)["level"], "AMBER")
        out = lr.compare_with_fly(self.report, self.fly(ftf_closes=7), self.mirror)
        self.assertEqual(out["level"], "RED")
        self.assertIn("trade counts differ", out["reasons"][0])

    def test_mirror_ahead_of_fly_is_red(self):
        self.assertEqual(lr.compare_with_fly(self.report, self.fly(ftf_closes=2), self.mirror)["level"], "RED")

    def test_unlisted_gap_is_only_a_lower_bound(self):
        listed = [{"trade_id": "d", "research_lane": FTF, "ts": "2026-10-02T05:00:00+00:00", "net_pnl_usd": 0.08}]
        mirror = self.mirror[:3]
        out = lr.compare_with_fly(self.report, self.fly(ftf_closes=5, listed=listed), mirror)
        self.assertEqual(out["level"], "GREEN", out["reasons"])
        self.assertFalse(out["breakdown"]["bounded"])

    def test_pnl_mismatch_beyond_tolerance_is_amber(self):
        out = lr.compare_with_fly(self.report, self.fly(ftf_pnl=0.5), self.mirror)
        self.assertEqual(out["level"], "AMBER")
        self.assertIn("PnL differs", out["reasons"][0])

    def test_listed_fly_close_missing_from_mirror_is_flagged(self):
        listed = [{"trade_id": "gap", "research_lane": FTF, "ts": "2026-10-02T03:30:00+00:00", "net_pnl_usd": 0.0}]
        out = lr.compare_with_fly(self.report, self.fly(ftf_closes=4, ftf_pnl=0.02, listed=listed), self.mirror)
        self.assertEqual(out["breakdown"]["missing_in_mirror"], ["gap"])
        self.assertEqual(out["level"], "AMBER")

    def test_red_analyzer_report_stays_red(self):
        report = dict(self.report, level="RED", reasons=["silent drop"])
        self.assertEqual(lr.compare_with_fly(report, self.fly(), self.mirror)["level"], "RED")

    def test_missing_inputs_are_amber_not_green(self):
        self.assertEqual(lr.compare_with_fly(self.report, None, self.mirror)["level"], "AMBER")
        self.assertEqual(lr.compare_with_fly(self.report, self.fly(), None)["level"], "AMBER")


if __name__ == "__main__":
    unittest.main()
