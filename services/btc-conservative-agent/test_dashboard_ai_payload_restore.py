import json
import shutil
import subprocess
from pathlib import Path

import bot as bot_mod

# Hostile restored AI context — keys are feature names, not credentials.
_HOSTILE_AI_PAYLOAD = {
    "price": 64000,
    "note": 'line1\nline2 with "quotes"',
    "tick": "select `rsi` and ${oops}",
    "html": "</script><script>alert(1)</script>",
}


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
    assert bot_mod.LAST_AI_PAYLOAD["schema"] == "shared_direction_prompt_v4"
    assert bot_mod.LAST_AI_PAYLOAD["raw"]["price"] == 64000
    assert "historically_profitable_patterns" not in bot_mod.LAST_AI_PAYLOAD
    assert bot_mod.LAST_AI_PAYLOAD["_dashboard_restore"]["status"] == "RESTORED_AFTER_RESTART"
    assert bot_mod.LAST_AI_TIMESTAMP == "2026-08-19T11:50:00Z"


def test_dashboard_names_canonical_non_onedrive_mirror():
    html = bot_mod.HTML
    assert "C:/DoxxedCrypto/btc-v31-current/services/btc-conservative-agent/canonical-research-data" in html
    assert "C:/Users/danis/AppData/Local/DoxxedCrypto/fly-data-mirror" not in html
    assert "services/btc-conservative-agent/fly-data-mirror" not in html


def test_json_for_js_keeps_quotes_newlines_backticks_inside_json():
    blob = bot_mod.json_for_js(_HOSTILE_AI_PAYLOAD)
    parsed = json.loads(blob.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&"))
    assert parsed["tick"] == _HOSTILE_AI_PAYLOAD["tick"]
    assert "\n" in parsed["note"]
    assert "`" in parsed["tick"]
    assert '"' in parsed["note"]
    assert blob.startswith("{") and blob.endswith("}")
    assert "</script>" not in blob


def _node_check(js_path: Path):
    node = shutil.which("node")
    assert node, "node is required to prove dashboard.js parses"
    result = subprocess.run(
        [node, "--check", str(js_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_hostile_restored_payload_does_not_break_dashboard_js(tmp_path):
    js = bot_mod.build_dashboard_js(_HOSTILE_AI_PAYLOAD)
    assert "__LAST_AI_PAYLOAD_JSON__" not in js
    assert "window.__LAST_AI_PAYLOAD__ =" in js
    # The generator must emit a JS escape, not a real newline inside the quote.
    assert "? '\\n@ '" in js or '? "\\n@ "' in js
    assert "ai_input_time ? '\n@" not in js
    out = tmp_path / "dashboard.js"
    out.write_text(js, encoding="utf-8")
    _node_check(out)


def test_restore_hostile_payload_then_dashboard_js_parses(tmp_path, monkeypatch):
    log = tmp_path / "ai_input_log.jsonl"
    log.write_text(
        json.dumps(
            {
                "ts": "2026-08-19T12:00:00Z",
                "context": _HOSTILE_AI_PAYLOAD,
                "ai": {"decision": "REJECT", "reason": 'gap `"quoted"`'},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bot_mod, "LAST_AI_PAYLOAD", {})
    monkeypatch.setattr(bot_mod, "LAST_AI_TIMESTAMP", None)
    assert bot_mod.restore_last_ai_payload_from_log(str(log)) is True
    js = bot_mod.build_dashboard_js()
    out = tmp_path / "dashboard.js"
    out.write_text(js, encoding="utf-8")
    _node_check(out)
