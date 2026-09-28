"""Regression checks for the DeepSeek V4 migration and restart-safe AI history."""

import csv
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


def run():
    passed = 0

    def check(name, condition):
        nonlocal passed
        if not condition:
            raise AssertionError(name)
        passed += 1

    original_env = {
        key: os.environ.get(key)
        for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_THINKING_MODE")
    }
    original_post = bot.requests.post
    original_usage = bot._report_showcase_inference_usage
    original_csv = bot.CSV_AI_TRANCHE
    original_bot_start = bot.bot_start_time
    with bot.state_lock:
        original_history = list(bot.state.get("ai_history") or [])
        original_history_updated = bot.state.get("ai_history_updated")

    try:
        os.environ.pop("DEEPSEEK_MODEL", None)
        os.environ.pop("DEEPSEEK_THINKING_MODE", None)
        check("Flash is the default model", bot._deepseek_model() == "deepseek-v4-flash")
        check("thinking is disabled by default", bot._deepseek_thinking_mode() == "disabled")

        os.environ["DEEPSEEK_MODEL"] = "deepseek-v4-pro"
        os.environ["DEEPSEEK_THINKING_MODE"] = "enabled"
        check("Pro remains configurable", bot._deepseek_model() == "deepseek-v4-pro")
        check("thinking remains configurable", bot._deepseek_thinking_mode() == "enabled")

        os.environ["DEEPSEEK_MODEL"] = "deepseek-chat"
        try:
            bot._deepseek_model()
        except RuntimeError as exc:
            check("retired alias fails closed", "INVALID_DEEPSEEK_MODEL:deepseek-chat" in str(exc))
        else:
            raise AssertionError("retired alias fails closed")
        bad_config_error = bot.build_ai_error_result(
            RuntimeError("INVALID_DEEPSEEK_MODEL:deepseek-chat"), "scan-bad-config"
        )
        check(
            "bad model remains journalable",
            bad_config_error["deepseek_model"] == "deepseek-chat",
        )

        os.environ["DEEPSEEK_API_KEY"] = "test-only"
        os.environ["DEEPSEEK_MODEL"] = "deepseek-v4-flash"
        os.environ["DEEPSEEK_THINKING_MODE"] = "disabled"
        captured = {}

        class FakeResponse:
            status_code = 200
            text = '{"choices":[{"message":{"content":"ok"}}]}'

            @staticmethod
            def json():
                return {
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                }

        def fake_post(url, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            return FakeResponse()

        def fake_usage(prompt_tokens, completion_tokens, **kwargs):
            captured["usage"] = (prompt_tokens, completion_tokens, kwargs)

        bot.requests.post = fake_post
        bot._report_showcase_inference_usage = fake_usage
        text, latency_ms = bot.call_deepseek_api(
            [{"role": "user", "content": "test"}],
            purpose="trading_direction",
        )
        check("successful V4 response parses", text == "ok" and latency_ms >= 0)
        check("request uses Flash", captured["json"]["model"] == "deepseek-v4-flash")
        check(
            "request explicitly disables thinking",
            captured["json"]["thinking"] == {"type": "disabled"},
        )
        check("usage records exact model", captured["usage"][2]["model"] == "deepseek-v4-flash")
        blocked_snapshot = dict(captured)
        try:
            bot.call_deepseek_api(
                [{"role": "user", "content": "analyze research"}],
                purpose="research_report",
            )
            research_blocked = False
        except RuntimeError as exc:
            research_blocked = str(exc) == "AI_PURPOSE_BLOCKED:research_report"
        check("non-trading AI purpose fails before network", research_blocked)
        check("blocked research purpose made no request", captured == blocked_snapshot)
        check(
            "retired V2 research AI cannot be environment-enabled",
            bot.TRADING_AI_ONLY
            and bot.TRADING_AI_ALLOWED_PURPOSES
            == frozenset({"trading_direction", "trading_confirmation"}),
        )
        analyzer_source = (
            Path(__file__).with_name("analyzer_research_engine_v62.py")
        ).read_text(encoding="utf-8")
        dashboard_source = (
            Path(__file__).parent / "research" / "research_dashboard.py"
        ).read_text(encoding="utf-8")
        forbidden_egress = (
            "api.deepseek.com",
            "requests.post(",
            "httpx.post(",
            "OpenAI(",
        )
        check(
            "deterministic analyzer has no AI-provider egress",
            not any(marker in analyzer_source for marker in forbidden_egress),
        )
        check(
            "research dashboard has no AI-provider egress",
            not any(marker in dashboard_source for marker in forbidden_egress),
        )
        with bot.state_lock:
            bot.state["ai_history"] = []
        bot._append_ai_history_row(
            {
                "trade_id": "scan-flash-receipt",
                "shared_ai_call_id": "scan-flash-receipt",
                "direction": "LONG",
                "decision": "APPROVE",
                "deepseek_model": "deepseek-v4-flash",
                "deepseek_thinking_mode": "disabled",
            }
        )
        with bot.state_lock:
            receipt_row = bot.state["ai_history"][-1]
        check(
            "live dashboard row carries exact provider receipt",
            receipt_row["deepseek_model"] == "deepseek-v4-flash"
            and receipt_row["deepseek_thinking_mode"] == "disabled",
        )

        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "ai_tranche_log.csv"
            current_start = time.time() - 300
            bot.bot_start_time = current_start
            fieldnames = [
                "ts",
                "trade_id",
                "shared_ai_call_id",
                "decision",
                "ai_direction_raw",
                "candidate_direction",
                "final_direction",
                "ai_error",
                "error_type",
                "error_detail",
                "http_status",
                "latency_ms",
                "deepseek_model",
                "deepseek_thinking_mode",
            ]
            with journal.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(
                    {
                        "ts": bot.utc_iso(
                            bot.datetime.fromtimestamp(current_start - 60, tz=bot.timezone.utc)
                        ),
                        "trade_id": "old-error",
                        "decision": "AI_ERROR",
                        "ai_error": "True",
                    }
                )
                writer.writerow(
                    {
                        "ts": bot.utc_iso(),
                        "trade_id": "scan-error-1",
                        "shared_ai_call_id": "scan-error-1",
                        "decision": "AI_ERROR",
                        "ai_direction_raw": "NO_TRADE",
                        "candidate_direction": "NO_TRADE",
                        "final_direction": "NO_TRADE",
                        "ai_error": "True",
                        "error_type": "RuntimeError",
                        "error_detail": "HTTP_400: retired model",
                        "http_status": "400",
                        "latency_ms": "81",
                        "deepseek_model": "deepseek-chat",
                        "deepseek_thinking_mode": "disabled",
                    }
                )
            bot.CSV_AI_TRANCHE = str(journal)
            restored = bot._restore_session_ai_history_from_csv(50)
            check("only current-session history is restored", len(restored) == 1)
            check("AI error survives restart", restored[0]["decision"] == "AI_ERROR")
            check("AI error detail survives restart", "retired model" in restored[0]["error_detail"])
            check("HTTP status survives restart", restored[0]["http_status"] == "400")
            check("model receipt survives restart", restored[0]["deepseek_model"] == "deepseek-chat")
            check("restored rows are not duplicated", len(bot.state["ai_history"]) == 1)
    finally:
        bot.requests.post = original_post
        bot._report_showcase_inference_usage = original_usage
        bot.CSV_AI_TRANCHE = original_csv
        bot.bot_start_time = original_bot_start
        with bot.state_lock:
            bot.state["ai_history"] = original_history
            bot.state["ai_history_updated"] = original_history_updated
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print(f"PASS: {passed} DeepSeek V4 migration checks")


if __name__ == "__main__":
    run()
