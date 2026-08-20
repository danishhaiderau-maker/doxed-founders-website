from collector_v22 import build_research_event
from collector_v22_schema import build_policy_identity
from path_replay_v1 import CONTROL_CELL
from replay_event_report import replay_event_report


def _candles(signal_ts, count=200):
    return [
        [int((signal_ts + index * 60) * 1000), 100, 101, 99, 100, 1]
        for index in range(count)
    ]


def test_invert_variants_have_distinct_policy_signatures_and_policy_epochs():
    off = build_policy_identity(epoch_id="epoch-1", control_cell=CONTROL_CELL, invert_on=False)
    on = build_policy_identity(epoch_id="epoch-1", control_cell=CONTROL_CELL, invert_on=True)
    another_epoch = build_policy_identity(epoch_id="epoch-2", control_cell=CONTROL_CELL, invert_on=False)

    assert off["policy_signature"] != on["policy_signature"]
    assert off["policy_epoch_id"] != on["policy_epoch_id"]
    assert off["policy_signature"] == another_epoch["policy_signature"]
    assert off["policy_epoch_id"] != another_epoch["policy_epoch_id"]
    assert CONTROL_CELL["invert_on"] is False


def test_event_and_replay_preserve_policy_identity_and_raw_direction():
    signal_ts = 1_700_000_000.0
    event = build_research_event(
        trade_id="identity-1",
        epoch_id="epoch-identity",
        signal_ts=signal_ts,
        signal_price=100.0,
        direction="SHORT",
        invert_on=True,
        rejected=True,
        ticket_closed=True,
        candles_1m=_candles(signal_ts),
    )
    report = replay_event_report(event)

    assert event["envelope"]["raw_direction"] == "LONG"
    assert event["envelope"]["executed_direction"] == "SHORT"
    assert event["policy_signature"] == event["envelope"]["policy_signature"]
    assert event["policy_epoch_id"] == event["envelope"]["policy_epoch_id"]
    assert report["policy_signature"] == event["policy_signature"]
    assert report["policy_epoch_id"] == event["policy_epoch_id"]
    assert report["control_cell"]["invert_on"] is True
