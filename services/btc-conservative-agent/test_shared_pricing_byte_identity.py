"""Tiles 6/7 removal (23 Oct 2026) R4 guard: the shared pricing code is byte-identical to the reviewed pins below.

HM review 20261023-tile67-removal C3: removing disloc_engine must not change how tiles 1-5 are priced.
First pinned at base master 51d926f93; every later move is deliberate and listed in the comments (latest: R22 PR-A).
Pins are the sha256 of each top-level function's exact source segment:
_paper_delay (shared delay helper, kept; was _disloc_delay), its callers, get_mark_price and the paper_fill pricing core.
If one of these functions changes on purpose later, update its sha here in the same PR, with the reason.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "the reviewed pins (first set at 51d926f93, last moved by R22 PR-A)"
# #839 F2/F3 (23 Oct 2026): intentional pin move for bot.py:_paper_lane_entry_result (realism-v2 shared trigger helper / entry quote stamping; R4 pricing intent unchanged for tiles 1-5 except the reviewed realism changes).
# #840 order model (23 Oct 2026): intentional pin move for bot.py:_paper_lane_entry_result, bot.py:_tp1_gate, bot.py:_paper_lane_exit, paper_fill.py:_paper_fill_core (live order-type model: resident stop, BOT_SENT market exits, model-time exits).
# #841 RK fixes (23 Oct 2026): intentional pin move for bot.py:_paper_lane_entry_result, bot.py:_paper_lane_exit, paper_fill.py:_paper_fill_core, paper_fill.py:paper_fill (trades-only stops, first-row-at-or-after resident fill, BFX truncation, liquidation stamp).
# #842 book exits (23 Oct 2026): intentional pin move for bot.py:get_mark_price, bot.py:_paper_lane_entry_result, bot.py:_tp1_gate, bot.py:_paper_lane_exit, bot.py:_disloc_delay, paper_fill.py:_paper_fill_core (every lane exits on book rows via the shared pricer; no ticker).
# #842 RK ruling A (24 Oct 2026): intentional pin move for bot.py:_paper_lane_exit, bot.py:_disloc_delay (POOLED_LIVE_ACK_CROSS_LANE: a paper-only lane times fills with its own signal->send + the shared cross-lane send->ack pool; fail closed below n=10).
# #842 HM 01:43 blocker 2 / #851 (24 Oct 2026): intentional pin move for paper_fill.py:_paper_fill_core (every book walk stamps the row's MEASURED WS receive time via the shared book_receive_proof; missing/inferred -> UNVERIFIED; bot-sent fills require receive_ts <= fill time).
# #842 HM 01:43 blocker 1 / items 4-5 (24 Oct 2026): intentional pin move for bot.py:_paper_lane_exit, bot.py:_disloc_delay (paper exit own signal->send is not measured: None -> default_own_s2s_missing -> UNVERIFIED, no assumed 0.0 / pricing-lag own leg; pool = Fly intent -> ack).
# #842 RK (3) FIRST_AFTER (24 Oct 2026, ff412bcfc): intentional pin move for paper_fill.py:_paper_fill_core (resident exchange-stop fill = the first trade print after the trigger; fill_ts = that row's rx).
# #842 HM 02:55 N1 (24 Oct 2026): intentional pin move for bot.py:_disloc_delay (the per-lane B1 DelaySampler is stamps-only; every priced delay goes through the shared live_ack_pool definition; no pool key -> fail closed).
# #842 HM 02:55 / RK (2) (24 Oct 2026): intentional pin move for bot.py:_tp1_gate, bot.py:_paper_lane_exit, bot.py:_disloc_delay (paper BOT_SENT exits: own trigger -> send code point measured per row via the shared _paper_own_s2s_exit + pooled EXIT_RO_MKT send->ack; resident stays 0; missing -> fail closed).
# R22 PR-A (25 Oct 2026): intentional pin move for bot.py:_tp1_gate, bot.py:_paper_lane_exit, bot.py:_paper_delay (pure rename _disloc_delay->_paper_delay, _DISLOC_DELAYS->_PAPER_DELAYS; no behaviour change: each new source == the old source with only those identifiers renamed).
# R0/R3 exit-truth RED (25 Oct 2026): intentional pin move for bot.py:_paper_lane_exit (paper STOP fills use FIRST_AFTER + STOP_RULE_NO_POOL; exit-book-top stamp prefers fill snapshot; hash recomputed after #879 rename rebase).
# Close-500 (30 Oct 2026): intentional pin move for bot.py:_paper_lane_exit (clamp model-fill wait sleep to >=0 so a clock race cannot ValueError).
PINNED = {
    "bot.py": {
        "get_mark_price": "b03f13d6030996983221371bede76b67fd2f610c03922e9aa3f142ece840b9c6",
        "_paper_lane_entry_result": "a227911677f4b9c9f80b0bec463171fe9e06e4190d2ddac5e1d51005193ef05d",
        "_tp1_gate": "620841b04d0204973ece88513166af9c2439adc4ad81f43e51f4f22c2e52a42d",
        "_paper_lane_exit": "ef700d7f6190937ead890a774477b2258505c0cf768eee9ec9adb997fc4a7f03",
        "_paper_delay": "4cd4b08d3dedb3233d84bd7a167985841e338105937baa1af4a3b908b1de5254"
    },
    "paper_fill.py": {
        "_f": "c70d10da2af1181b4d56905d296e71c2b6da8603ab5d7e9a22c0ad0b0b6e303f",
        "_walk": "1b0a7ced3bbfffb1fd7d4b49b270cada915b0d0dab487c024e615d2d29b9a5ba",
        "_safe": "577c4d1a9927bea25660a9a46802c84fabf3b5d44889bfed82a86e0f042cd002",
        "_snap": "e19ffc0d73e86ae849dff85c6bec6c1e6219883f5c3e1897a2d9666e1bef9f29",
        "taker_side_levels": "2e794b626132b0f1320049fa51683053c6ba948e3b8292aa2bc4a84209efa05b",
        "_paper_fill_core": "dc92f85b328827673158144cfc03fbc8a21db1c00a7115d2d83c1c6e285c0737",
        "paper_fill": "3205191a41fde2cafbbdd1ebd4f500ccefe44668e5fbb792c6fc03943d008057",
        "book10l_at": "9bfbe8e684e475716e132b2105d7ad27eb43a3fc681db481601698f8b20f62d9",
        "book10l_missing_seconds": "3683b425ea5cf93da7aec4fcebbd26e26e9e5baaf75c52f672ae01d1b2f5b38d",
        "_is_buy": "09fe475c10c3d931827335cae923828742d25273287a80f126f48d9d4cf2bda4",
        "best_touch": "07f42fc64361f493043d7258dce6dd53c3493548fe4c5b43ae2f8a83747e44ca",
        "_bp": "050b07ec3b3b797da142bc9d6db9d1dbad0beab7a2501453d486f6f2d5a46ba1",
        "leg_slippage": "3fcbc7a4e94ab6b904699ec3738d73868aa3d8490c9ad206e7baf8fe750e885a",
        "aggregate_legs": "e7ca47f97fe2cf5d5cab9f233216c5e02a1aa15560599a7cd7c6ea2df0e96591"
    }
}


def _shas(path: Path) -> dict:
    src = path.read_text(encoding="utf-8")
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = hashlib.sha256(ast.get_source_segment(src, node).encode()).hexdigest()
    return out


def test_shared_pricing_functions_byte_identical_to_base():
    for fname, pins in PINNED.items():
        now = _shas(HERE / fname)
        for fn, sha in pins.items():
            assert fn in now, f"{fname}:{fn} missing (pinned at {BASE})"
            assert now[fn] == sha, f"{fname}:{fn} changed vs {BASE}; R4 requires it byte-identical"


def test_shared_paper_delay_kept_and_old_name_gone():
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    assert "_paper_delay" in PINNED["bot.py"]
    assert "_disloc_delay" not in src and "_DISLOC_DELAYS" not in src
    assert not (HERE / "disloc_engine.py").exists()
