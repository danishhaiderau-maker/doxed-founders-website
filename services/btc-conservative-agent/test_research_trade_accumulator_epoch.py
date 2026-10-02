import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from combo_pathway_config import ACTIVE_TILE_ORDER
from research import research_trade_accumulator as acc

CURRENT_EPOCH = "epoch-v22-current"
EPOCH_TS = 1790624095.126945  # 2026-09-28T19:34:55Z
SESSION = {
    "bot_start_time": 1789983916.5432568,  # 2026-09-21, older than the collector epoch
    "bot_start_iso_utc": "2026-09-21 09:45:16 UTC",
    "fresh_collection_mode": False,
    "collector_v22_epoch_ts": EPOCH_TS,
    "collector_v22_epoch_id": CURRENT_EPOCH,
}


def _write_data(data: Path, rows):
    pd.DataFrame(rows).to_csv(data / acc.TRADES_CSV, index=False)
    (data / "research_session.json").write_text(json.dumps(SESSION), encoding="utf-8")


def _trade(trade_id, lane, epoch_id=CURRENT_EPOCH, close_ts="2026-10-02T09:14:27+00:00", pnl=0.01):
    return {
        "trade_id": trade_id, "research_lane": lane, "epoch_id": epoch_id,
        "close_ts": close_ts, "net_pnl_usd": pnl,
    }


class TradeAccumulatorEpochTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "code"
        self.data = base / "data"
        self.root.mkdir()
        self.data.mkdir()
        self.lane = ACTIVE_TILE_ORDER[0]

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_csv_from_data_mirror_and_follows_collector_epoch(self):
        _write_data(self.data, [
            _trade("cur-1", self.lane),
            _trade("old-epoch", self.lane, epoch_id="epoch-v21-old"),
            _trade("retired", "FAMILY_CHANDELIER_3"),
            _trade("pre-epoch-no-id", self.lane, epoch_id=None, close_ts="2026-09-25T00:00:00+00:00"),
        ])
        with patch.dict(os.environ, {"BTC_AGENT_DATA_DIR": str(self.data)}):
            result = acc.sync_accumulator(session={"bot_start_time": SESSION["bot_start_time"]}, root=self.root)
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["epoch_id"], CURRENT_EPOCH)
        self.assertTrue(result["epoch"].startswith("2026-09-28T19:34:55"))
        self.assertEqual(result["skipped_non_registry_lane"], 1)
        self.assertEqual(result["skipped_epoch"], 2)
        self.assertEqual(Path(result["trades_csv"]), self.data / acc.TRADES_CSV)

    def test_persisted_stale_epoch_is_reanchored(self):
        acc.init_db(self.root)
        with closing(sqlite3.connect(acc._db_path(self.root))) as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('epoch_start_iso','2026-09-21T09:45:16+00:00')"
            )
            conn.commit()
        _write_data(self.data, [_trade("cur-1", self.lane)])
        result = acc.sync_accumulator(root=self.root, data_dir=self.data)
        self.assertEqual(result["new"], 1)
        status = json.loads(acc._status_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(status["epoch_id"], CURRENT_EPOCH)
        self.assertIn("COLLECTOR_EPOCH", status["epoch_reason"])
        self.assertEqual(status["by_lane"], {self.lane: {
            "n": 1, "wins": 1, "net_pnl_usd": 0.01, "pnl": 0.01, "pnl_unit": "USD",
            "pnl_basis_counts": {"RECORDED_CSV_VALUE": 1},
        }})

    def test_pnl_is_exact_usd_never_the_percent_column(self):
        receipt = json.dumps({"reconciled": True, "observed_net_pnl_usd": -0.004321})
        rows = [
            {**_trade("rounded-zero", self.lane, pnl=0.0), "pnl": 1.79, "execution_cost_accounting": receipt},
            {**_trade("plain", self.lane, pnl=-0.02), "pnl": -3.5},
        ]
        _write_data(self.data, rows)
        acc.sync_accumulator(root=self.root, data_dir=self.data)
        lane = acc.build_status(root=self.root)["by_lane"][self.lane]
        self.assertEqual(lane["net_pnl_usd"], round(-0.004321 - 0.02, 6))
        self.assertEqual(lane["pnl"], lane["net_pnl_usd"])
        self.assertEqual(lane["pnl_unit"], "USD")
        self.assertEqual(lane["wins"], 0)
        with closing(sqlite3.connect(acc._db_path(self.root))) as conn:
            stored = dict(conn.execute("SELECT trade_id, net_pnl_usd FROM trades").fetchall())
        self.assertEqual(stored, {"rounded-zero": -0.004321, "plain": -0.02})

    def test_rows_stored_with_the_percent_value_are_repaired(self):
        _write_data(self.data, [{**_trade("cur-1", self.lane, pnl=0.0), "pnl": 1.79}])
        acc.sync_accumulator(root=self.root, data_dir=self.data)
        with closing(sqlite3.connect(acc._db_path(self.root))) as conn:
            conn.execute("UPDATE trades SET net_pnl_usd=1.79")
            conn.commit()
        acc.sync_accumulator(root=self.root, data_dir=self.data)
        with closing(sqlite3.connect(acc._db_path(self.root))) as conn:
            self.assertEqual(conn.execute("SELECT net_pnl_usd FROM trades").fetchone()[0], 0.0)

    def test_status_reconciles_with_the_mirror_ledger(self):
        _write_data(self.data, [_trade("a", self.lane, pnl=0.01), _trade("b", self.lane, pnl=-0.03)])
        acc.sync_accumulator(root=self.root, data_dir=self.data)
        ledger = {"mirror_ledger": {self.lane: {"n": 2, "net_pnl_usd": -0.02}}, "quarantined": {self.lane: 1}}
        (self.root / acc.LEDGER_RECONCILIATION_FILE).write_text(json.dumps(ledger), encoding="utf-8")
        rec = acc.build_status(root=self.root)["ledger_reconciliation"]
        self.assertEqual(rec["status"], "MATCH")
        self.assertEqual(rec["lanes"][self.lane]["quarantined_in_analyzer_cohort"], 1)
        ledger["mirror_ledger"][self.lane]["net_pnl_usd"] = -0.46
        (self.root / acc.LEDGER_RECONCILIATION_FILE).write_text(json.dumps(ledger), encoding="utf-8")
        rec = acc.build_status(root=self.root)["ledger_reconciliation"]
        self.assertEqual((rec["status"], rec["mismatched_lanes"]), ("MISMATCH", [self.lane]))

    def test_missing_ledger_reconciliation_is_unavailable_not_match(self):
        self.assertEqual(acc.reconcile_with_ledger({}, self.root)["status"], "UNAVAILABLE")

    def test_reads_expose_only_current_epoch_after_epoch_change(self):
        _write_data(self.data, [_trade("cur-1", self.lane)])
        acc.sync_accumulator(root=self.root, data_dir=self.data)
        newer = {**SESSION, "collector_v23_epoch_ts": EPOCH_TS + 86400, "collector_v23_epoch_id": "epoch-v23"}
        (self.data / "research_session.json").write_text(json.dumps(newer), encoding="utf-8")
        pd.DataFrame([
            _trade("cur-1", self.lane),
            _trade("next-1", self.lane, epoch_id="epoch-v23", close_ts="2026-10-03T00:00:00+00:00"),
        ]).to_csv(self.data / acc.TRADES_CSV, index=False)
        result = acc.sync_accumulator(root=self.root, data_dir=self.data)
        self.assertEqual(result["epoch_id"], "epoch-v23")
        self.assertEqual(result["total"], 1)
        frame = acc.load_accumulated_trades_df(self.root)
        self.assertEqual(list(frame["trade_id"]), ["next-1"])
        status = acc.build_status(root=self.root, session=newer)
        self.assertEqual(status["stored_trades_all_epochs"], 2)
        self.assertEqual(status["total_trades"], 1)

    def test_missing_csv_is_reported_not_silent(self):
        with patch.dict(os.environ, {"BTC_AGENT_DATA_DIR": str(self.data)}), \
                patch.object(acc.Path, "cwd", return_value=self.root):
            result = acc.sync_accumulator(session=SESSION, root=self.root)
        self.assertEqual(result["new"], 0)
        self.assertEqual(result["error"], "TRADES_CSV_NOT_FOUND")

    def test_current_collection_epoch_prefers_newest_collector_key(self):
        epoch_id, epoch_ts, source = acc.current_collection_epoch({
            **SESSION, "collector_v21_epoch_id": "old", "collector_v21_epoch_ts": EPOCH_TS - 1000,
        })
        self.assertEqual((epoch_id, source), (CURRENT_EPOCH, "COLLECTOR_EPOCH"))
        self.assertEqual(epoch_ts, pd.Timestamp(EPOCH_TS, unit="s", tz="UTC"))
        self.assertEqual(acc.current_collection_epoch({})[2], "NONE")


if __name__ == "__main__":
    unittest.main()
