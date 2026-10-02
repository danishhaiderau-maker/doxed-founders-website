import copy
import json
import pytest
from combo_pathway_config import COMBO_LANE_SPECS
from paper_evidence_capacity import FIELD, initialize_legacy, validate
from test_paper_family_chase_durability import fixture


def test_actual_local_order_commit_reserves_before_target_and_live_swap(tmp_path):
    lane = next(iter(COMBO_LANE_SPECS))
    ns, order, _, outbox, paused, *_ = fixture(tmp_path, lane)
    empty = {"paper_only": True, "live_armed": False, "positions": [], "pending_orders": []}
    outbox._atomic_write(outbox.decorate_lifecycle(empty))
    ns["_build_paper_lifecycle_payload"] = lambda *a, **k: copy.deepcopy(empty)
    seen = []
    def mutate(target):
        assert validate(target)["reserved"] == 2
        target["pending_orders"].append(copy.deepcopy(order))
    def live():
        saved = json.loads(outbox.path.read_text())
        assert saved["pending_orders"] == [order]
        assert validate(saved)["reserved"] == 2
        seen.append(True)
    ns["_commit_local_paper_lifecycle_transition"]("ORDER_PLACED", order["trade_id"], {"research_lane": lane}, target_mutator=mutate, live_mutator=live)
    assert seen == [True] and paused == []


def test_actual_full_capacity_rejects_entry_without_pausing_or_mutating(tmp_path):
    lane = next(iter(COMBO_LANE_SPECS))
    ns, order, _, outbox, paused, *_ = fixture(tmp_path, lane)
    saved = json.loads(outbox.path.read_text())
    saved = initialize_legacy(saved, capacity=2)
    outbox._atomic_write(outbox.decorate_lifecycle(saved))
    before = outbox.path.read_bytes()
    called = []
    with pytest.raises(RuntimeError, match="NEW_ENTRY_EVIDENCE_CAPACITY_EXHAUSTED"):
        ns["_commit_local_paper_lifecycle_transition"]("ORDER_PLACED", COMBO_LANE_SPECS[lane]["id_prefix"] + "-new", {"research_lane": lane}, target_mutator=lambda t: called.append("target"), live_mutator=lambda: called.append("live"))
    assert outbox.path.read_bytes() == before and called == [] and paused == []


def test_ordinary_save_keeps_reservations_with_obligations(tmp_path):
    lane = next(iter(COMBO_LANE_SPECS))
    _, _, _, outbox, *_ = fixture(tmp_path, lane)
    saved = initialize_legacy(json.loads(outbox.path.read_text()))
    saved["paper_research_replay"] = {"cursor_trade_id": "a", "attempt_seq": 3, "outcomes": []}
    outbox._atomic_write(outbox.decorate_lifecycle(saved))
    ordinary = {"paper_only": True, "live_armed": False, "pending_orders": saved["pending_orders"]}
    outbox._atomic_write(outbox.decorate_lifecycle(ordinary))
    current = json.loads(outbox.path.read_text())
    assert current[FIELD] == saved[FIELD]
    assert current["paper_research_replay"] == saved["paper_research_replay"]
    assert validate(current)["reserved"] == 2
