"""Tiles 6/7 removal (23 Oct 2026) R4 guard: the shared pricing code is byte-identical to base 51d926f93.

HM review 20261023-tile67-removal C3: removing disloc_engine must not change how tiles 1-5 are priced.
Pinned at base master 51d926f93 (sha256 of each top-level function's exact source segment):
_disloc_delay (shared delay helper, kept), its callers, get_mark_price and the paper_fill pricing core.
If one of these functions changes on purpose later, update its sha here in the same PR, with the reason.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "51d926f93"
# #839 F2/F3 (23 Oct 2026): intentional pin move for bot.py:_paper_lane_entry_result (realism-v2 shared trigger helper / entry quote stamping; R4 pricing intent unchanged for tiles 1-5 except the reviewed realism changes).
# #840 order model (23 Oct 2026): intentional pin move for bot.py:_paper_lane_entry_result, bot.py:_tp1_gate, bot.py:_paper_lane_exit, paper_fill.py:_paper_fill_core (live order-type model: resident stop, BOT_SENT market exits, model-time exits).
PINNED = {
    "bot.py": {
        "get_mark_price": "f9c6c2b6cd40d0e9b558ee8b31d73f0a5b0079fc8162ba667a0b8265eba1c707",
        "_paper_lane_entry_result": "ed0e9b17379ac387091f9c9b3b8f3c91bd038dba20fc73f68dce7d049a042dd1",
        "_tp1_gate": "02d113f32f656a59cc96ad0f1be35e42564e4f9274707f478fc6f1fa07cdae51",
        "_paper_lane_exit": "a6112b728783dbdcf1e3deb0bb0c6319fde4356db228c41a46226e799d926f9e",
        "_disloc_delay": "bd27730709158b9bfdcc944c6f303603aa9c2b2f7ccf63d91fc4c485b9bbb9f4"
    },
    "paper_fill.py": {
        "_f": "c70d10da2af1181b4d56905d296e71c2b6da8603ab5d7e9a22c0ad0b0b6e303f",
        "_walk": "1b0a7ced3bbfffb1fd7d4b49b270cada915b0d0dab487c024e615d2d29b9a5ba",
        "_safe": "577c4d1a9927bea25660a9a46802c84fabf3b5d44889bfed82a86e0f042cd002",
        "_snap": "e19ffc0d73e86ae849dff85c6bec6c1e6219883f5c3e1897a2d9666e1bef9f29",
        "taker_side_levels": "2e794b626132b0f1320049fa51683053c6ba948e3b8292aa2bc4a84209efa05b",
        "_paper_fill_core": "68200a5a1d77c50399ae185c924b97172048e63ccd833beb36da74bb34444923",
        "paper_fill": "ab0b29144b0b1990a09d9df4700bed9337407a16caf6376999d4523bb6d8da62",
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


def test_disloc_delay_kept_and_engine_gone():
    assert "_disloc_delay" in PINNED["bot.py"]
    assert not (HERE / "disloc_engine.py").exists()
