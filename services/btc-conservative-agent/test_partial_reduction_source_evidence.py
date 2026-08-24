"""Source-side contract for protected Patient Chase partial-reduction evidence."""

from __future__ import annotations

import ast
import copy
import threading
from pathlib import Path


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _function(name: str) -> ast.FunctionDef:
    return next(
        node for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _compile(name: str, namespace: dict):
    exec(
        compile(ast.Module(body=[_function(name)], type_ignores=[]), f"<{name}>", "exec"),
        namespace,
    )
    return namespace[name]


class _Action:
    reason = "PARTIAL_TP_1_ATR"
    close_fraction = 0.25
    remaining_fraction = 0.75
    first_partial_done = True
    second_partial_done = False
    break_even_armed = False
    peak_price = 101.0


class _ProtectedPolicy:
    POLICY_ID = "OFFSET_029_ATR_PROTECTED_V1"

    @staticmethod
    def exit_action(**_kwargs):
        return _Action()


def _position() -> dict:
    return {
        "trade_id": "o29ps-proof",
        "research_lane": "OFFSET_029_ATR_PROTECTED",
        "entry": 100.0,
        "dir": "LONG",
        "qty": 0.02,
        "policy_original_qty": 0.02,
        "policy_remaining_fraction": 1.0,
        "atr14_3m": 1.0,
        "entry_ts": 1.0,
        "policy_id": _ProtectedPolicy.POLICY_ID,
        "leverage": 100,
    }


def _namespace(save_result: bool = True):
    order: list[str] = []
    pushed: list[tuple] = []

    def save_paper_lifecycle(*, reason: str):
        order.append(f"save:{reason}")
        return save_result

    def push(event, trade_id, payload, *, wait_for_durable_receipt=False):
        order.append(f"push:{event}")
        pushed.append((event, trade_id, copy.deepcopy(payload), wait_for_durable_receipt))
        return False

    class Logger:
        def error(self, _message):
            order.append("error")

    ns = {
        "copy": copy,
        "state": {},
        "utc_iso": lambda: "2026-08-24T00:00:00Z",
        "_buf_float": lambda value, default=0.0: float(value if value is not None else default),
        "offset029_protected_policy": _ProtectedPolicy(),
        "offset029_regime_policy": object(),
        "RESEARCH_LANE_OFFSET_029_ATR_REGIME": "OFFSET_029_ATR_REGIME",
        "save_paper_lifecycle": save_paper_lifecycle,
        "_push_showcase_relay_event": push,
        "close_position": lambda *_args: None,
        "logger": Logger(),
    }
    return ns, order, pushed


def test_partial_receipt_is_persisted_before_signed_delivery() -> None:
    ns, order, pushed = _namespace()
    apply_exit = _compile("_apply_protected_patient_chase_exit", ns)
    pos = _position()

    assert apply_exit(pos, 101.0, 10.0) is False
    assert order == ["save:partial_exit:PARTIAL_TP_1_ATR", "push:POSITION_REDUCED"]
    assert pos["qty"] == 0.015
    receipt = pos["partial_reduction_outbox"][0]
    assert receipt == pos["partial_exit_receipts"][0]
    assert receipt["event_id"] == "o29ps-proof:POSITION_REDUCED:PARTIAL_TP_1_ATR"
    assert receipt["reduction_id"] == "o29ps-proof:PARTIAL_TP_1_ATR"
    assert receipt["event_seq"] == 1
    assert receipt["prior_qty"] == 0.02
    assert receipt["reduced_qty"] == 0.005
    assert receipt["remaining_qty"] == 0.015
    assert receipt["fill_price"] == 101.0
    assert pushed == [("POSITION_REDUCED", "o29ps-proof", receipt, True)]


def test_failed_local_commit_rolls_back_and_never_delivers() -> None:
    ns, order, pushed = _namespace(save_result=False)
    apply_exit = _compile("_apply_protected_patient_chase_exit", ns)
    pos = _position()
    original = copy.deepcopy(pos)

    assert apply_exit(pos, 101.0, 10.0) is False
    assert pos == original
    assert order == ["save:partial_exit:PARTIAL_TP_1_ATR", "error"]
    assert pushed == []


def test_event_sequence_advances_from_persisted_receipt_maximum() -> None:
    ns, _order, _pushed = _namespace()
    apply_exit = _compile("_apply_protected_patient_chase_exit", ns)
    pos = _position()
    pos["partial_exit_receipts"] = [{"event_seq": 3}]

    assert apply_exit(pos, 101.0, 10.0) is False
    assert pos["partial_exit_receipts"][-1]["event_seq"] == 4


def test_reduction_evidence_identity_does_not_expand_live_allowlist() -> None:
    resolve = _compile(
        "_platform_relay_evidence_lane_for_event",
        {
            "PLATFORM_RELAY_EVIDENCE_PREFIX_LANES": {
                "o29ps": "OFFSET_029_ATR_PROTECTED",
                "o29rd": "OFFSET_029_ATR_REGIME",
            },
        },
    )
    assert resolve("o29ps-a", "OFFSET_029_ATR_PROTECTED") == "OFFSET_029_ATR_PROTECTED"
    assert resolve("o29rd-b", "OFFSET_029_ATR_REGIME") == "OFFSET_029_ATR_REGIME"
    assert resolve("o29ps-a", "OFFSET_029_ATR_REGIME") == ""

    push_source = ast.get_source_segment(BOT_SOURCE, _function("_push_showcase_relay_event"))
    assert 'evidence_only = event == "POSITION_REDUCED"' in push_source
    assert "PLATFORM_RELAY_CONFIGURED_LANES" in push_source
    assert "PLATFORM_RELAY_ELIGIBLE_LANES" in push_source
    assert 'payload["schema"] = "dcf-showcase-intent-v1"' in push_source


def test_relay_snapshot_keeps_immutable_reduction_backstop() -> None:
    snapshot = _compile(
        "_relay_position_row_lite",
        {
            "copy": copy,
            "_position_protection_view": lambda _row: {
                "sl": 99, "sl_enforced": True, "stop_policy": "TEST",
                "tp": 102.5, "tp_policy": "TEST",
            },
            "DEFAULT_RESEARCH_LEVERAGE": 100,
            "FIXED_MARGIN_USDT": 0.25,
        },
    )
    receipt = {"event_id": "o29ps-proof:POSITION_REDUCED:PARTIAL_TP_1_ATR"}
    row = _position()
    row["partial_exit_receipts"] = [receipt]
    row["partial_reduction_outbox"] = [receipt]
    out = snapshot(row, 101.0)
    assert out["partial_exit_receipts"] == [receipt]
    assert out["partial_reduction_outbox"] == [receipt]
    assert out["partial_exit_receipts"] is not row["partial_exit_receipts"]


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_durable_ack_removes_outbox_but_retains_immutable_receipt() -> None:
    receipt = {
        "event_id": "o29ps-proof:POSITION_REDUCED:PARTIAL_TP_1_ATR",
        "reduction_id": "o29ps-proof:PARTIAL_TP_1_ATR",
    }
    pos = _position()
    pos["partial_exit_receipts"] = [copy.deepcopy(receipt)]
    pos["partial_reduction_outbox"] = [copy.deepcopy(receipt)]
    ack = _compile(
        "_ack_partial_reduction_outbox",
        {
            "copy": copy,
            "trade_lock": _Lock(),
            "open_positions": [pos],
            "save_paper_lifecycle": lambda **_kwargs: True,
        },
    )

    assert ack(pos["trade_id"], receipt["event_id"]) is True
    assert pos["partial_reduction_outbox"] == []
    assert pos["partial_exit_receipts"] == [receipt]


def test_failed_ack_persistence_restores_outbox() -> None:
    receipt = {"event_id": "o29ps-proof:POSITION_REDUCED:PARTIAL_TP_1_ATR"}
    pos = _position()
    pos["partial_reduction_outbox"] = [copy.deepcopy(receipt)]
    ack = _compile(
        "_ack_partial_reduction_outbox",
        {
            "copy": copy,
            "trade_lock": _Lock(),
            "open_positions": [pos],
            "save_paper_lifecycle": lambda **_kwargs: False,
        },
    )

    assert ack(pos["trade_id"], receipt["event_id"]) is False
    assert pos["partial_reduction_outbox"] == [receipt]


def test_restart_drain_replays_once_and_ack_removes_event() -> None:
    receipt = {"event_id": "o29ps-proof:POSITION_REDUCED:PARTIAL_TP_1_ATR"}
    pos = _position()
    pos["partial_reduction_outbox"] = [copy.deepcopy(receipt)]
    delivered = []

    def push(event, trade_id, payload, *, wait_for_durable_receipt=False):
        delivered.append((event, trade_id, payload["event_id"], wait_for_durable_receipt))
        return True

    def ack(_trade_id, event_id):
        pos["partial_reduction_outbox"] = [
            row for row in pos["partial_reduction_outbox"]
            if row["event_id"] != event_id
        ]
        return True

    drain = _compile(
        "_drain_partial_reduction_outbox_once",
        {
            "copy": copy,
            "trade_lock": _Lock(),
            "open_positions": [pos],
            "_partial_reduction_drain_lock": threading.Lock(),
            "_push_showcase_relay_event": push,
            "_ack_partial_reduction_outbox": ack,
        },
    )

    assert drain() == {"attempted": 1, "acked": 1, "busy": False}
    assert drain() == {"attempted": 0, "acked": 0, "busy": False}
    assert delivered == [
        ("POSITION_REDUCED", pos["trade_id"], receipt["event_id"], True)
    ]
