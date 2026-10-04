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

    def test_large_unlisted_gap_is_mirror_lag_amber_not_red(self):
        listed = [{"trade_id": "d", "research_lane": FTF, "ts": "2026-10-02T05:00:00+00:00", "net_pnl_usd": 0.08}]
        out = lr.compare_with_fly(self.report, self.fly(ftf_closes=9, listed=listed), self.mirror[:3])
        self.assertEqual(out["level"], "AMBER", out["reasons"])
        self.assertIn("trade counts differ", out["reasons"][0])

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


def test_forced_and_pre_cutoff_mirror_closes_are_outside_fly_scope():
    """Fly tiles exclude deploy-flatten closes and pre-cutoff closes; the mirror side must too."""
    import ledger_reconciliation as lr
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    report = {"lanes": [lane], "level": "GREEN", "epoch_id": "ce-x"}
    mirror = [
        {"trade_id": "a", "research_lane": lane, "close_ts": "2026-10-04T08:20:00Z", "net_pnl_usd": 0.1},
        {"trade_id": "b", "research_lane": lane, "close_ts": "2026-10-04T08:57:00Z", "net_pnl_usd": -0.2,
         "exit_reason": "ADMIN_MANUAL_CLOSE"},
        {"trade_id": "c", "research_lane": lane, "close_ts": "2026-10-04T09:57:00Z", "net_pnl_usd": -0.1,
         "exit_reason": "ADMIN_MANUAL_CLOSE"},
    ]
    fly = {"fresh_epoch_cutoff_utc": "2026-10-04T08:39:39.194216490+00:00", "trades": [],
           "lane_pnl_ledger": {lane: {"closes": 0, "net_pnl_usd": 0.0, "wins": 0}}}
    out = lr.compare_with_fly(report, fly, mirror)
    assert out["level"] == "GREEN", out["reasons"]
    assert out["breakdown"]["per_lane"][lane]["mirror_n"] == 0
    assert out["breakdown"]["mirror_out_of_fly_scope"] == 3


def test_analyzer_cohort_uses_fly_tile_scope_forced_and_pre_epoch_excluded():
    # 2026-10-04: COMMITTED_FADE analyzer n=8 / Win 25% vs the Fly tile 2 / 100%: six of the
    # eight were deploy-flatten ADMIN_MANUAL_CLOSE rows the tile (rightly) leaves out.
    cutoff = lr.parse_ts("2026-10-02T00:30:00+00:00")
    raw = [row("s1", exact=0.01), row("s2", exact=0.02)]
    for i in range(6):
        forced = row(f"f{i}", exact=-0.03, close=f"2026-10-02T02:0{i}:00+00:00")
        forced["exit_reason"] = "ADMIN_MANUAL_CLOSE"
        raw.append(forced)
    raw.append(row("pre", exact=0.05, close="2026-10-02T00:10:00+00:00"))
    rep = lr.build_report(raw_rows=raw, cohort_ids=[r["trade_id"] for r in raw], quarantine_rows=[],
                          lanes=LANES, epoch_id=EPOCH, epoch_cutoff_ts=cutoff)
    ftf = rep["analyzer_cohort"][FTF]
    assert (ftf["n"], ftf["wins"], ftf["losses"], ftf["win_pct"]) == (2, 2, 0, 100.0)
    assert abs(ftf["net_pnl_usd"] - 0.03) < 1e-9
    assert rep["mirror_ledger"][FTF]["n"] == 2
    assert rep["analyzer_cohort_incl_forced"][FTF]["n"] == 9
    assert rep["excluded_from_tile_scope"][FTF] == {"forced": 6, "pre_epoch": 1}
    assert rep["level"] == "GREEN" and not rep["unexplained_drops"]
    assert "ADMIN_MANUAL_CLOSE" in rep["cohort_scope"]["excluded_exit_reasons"]


def test_analyzer_feeds_exit_reason_and_session_cutoff_to_the_reconciliation():
    from pathlib import Path
    src = (Path(__file__).resolve().parent / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    start = src.index("def _record_ledger_reconciliation(")
    body = src[start:src.index("\ndef ", start + 10)]
    assert '"exit_reason"' in body and "epoch_cutoff_ts=cutoff" in body and "_session_start_ts(" in body
