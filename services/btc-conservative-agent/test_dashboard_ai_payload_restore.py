import json
from pathlib import Path

import bot as bot_mod


def test_compose_ai_history_reason_includes_reject():
    text = bot_mod._compose_ai_history_reason(
        {
            "decision": "REJECT",
            "reason": "LOW_CONFIDENCE",
            "comment": "win_prob 65",
        }
    )
    assert "REJECT" in text
    assert "LOW_CONFIDENCE" in text
    assert "win_prob 65" in text


def test_restore_last_ai_payload_from_log(tmp_path, monkeypatch):
    log = tmp_path / "ai_input_log.jsonl"
    log.write_text(
        json.dumps(
            {
                "ts": "2026-08-19T11:50:00Z",
                "context": {"price": 64000, "exhaustion_3m": {"rsi14": 41}},
                "ai": {"decision": "APPROVE"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bot_mod, "LAST_AI_PAYLOAD", {})
    monkeypatch.setattr(bot_mod, "LAST_AI_TIMESTAMP", None)
    assert bot_mod.restore_last_ai_payload_from_log(str(log)) is True
    assert bot_mod.LAST_AI_PAYLOAD["price"] == 64000
    assert bot_mod.LAST_AI_PAYLOAD["_dashboard_restore"]["status"] == "RESTORED_AFTER_RESTART"
    assert bot_mod.LAST_AI_TIMESTAMP == "2026-08-19T11:50:00Z"
