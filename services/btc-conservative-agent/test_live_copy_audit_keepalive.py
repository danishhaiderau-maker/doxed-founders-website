from pathlib import Path

SRC = Path(__file__).with_name("bot.py").read_text()


def test_reconcile_loop_keeps_audit_fresh_for_live_copy_output():
    body = SRC.split("def bitfinex_live_reconcile_loop() -> None:", 1)[1].split("\ndef ", 1)[0]
    assert "_live_copy_output_audit_needed()" in body
    assert body.index("_live_copy_output_audit_needed()") < body.index("if _direct_private_exchange_owner():")
    helper = SRC.split("def _live_copy_output_audit_needed() -> bool:", 1)[1].split("\ndef ", 1)[0]
    assert "_get_live_copy_output().enabled" in helper
    assert "_live_copy_paper_lock_active()" in helper
