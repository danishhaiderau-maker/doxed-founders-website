"""Research ZIP export contract.

Run: cd services/btc-conservative-agent && python test_research_export_csv.py
"""
import io
import os
import sys
import tempfile
import zipfile

os.environ["FORCE_PAPER_MODE"] = "1"
os.environ["SKIP_EXCHANGE_MARKET_LOAD"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot


def main():
    required = {
        bot.SHADOW_LANE_OUTCOME_FILE,
        bot.LANE_OPPORTUNITY_CAPTURE_FILE,
        bot.LANE_PNL_LEDGER_FILE,
        bot.LANE_LAB_PNL_LEDGER_FILE,
        bot.PATHWAY_LANE_SPECS_FILE,
        bot.TRADE_OUTCOME_FILE,
        bot.PATH_REPLAY_FILE,
        bot.CYCLE_3M_UNIVERSE_FILE,
        bot.CHASE_OFFSET_TOUCH_GRID_FILE,
        bot.ORDER_MULTIVERSE_FILE,
        bot.OPPORTUNITY_CAPTURE_FILE,
        "execution_funnel.jsonl",
    }
    previous = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="research_export_") as tmp:
        try:
            os.chdir(tmp)
            log_path = os.path.join(tmp, "large-bot.log")
            with open(log_path, "wb") as log_file:
                log_file.write(b"discard-me\n" * 200000)
                log_file.write(b"tail-one\ntail-two\ntail-three\n")
            bounded_tail = bot._read_log_tail(log_path, max_lines=2, max_bytes=128)
            if bounded_tail != "tail-two\ntail-three\n":
                print(f"[FAIL] bounded log tail mismatch: {bounded_tail!r}")
                return 1
            if bot.DASHBOARD_HTTP_WATCHDOG_TIMEOUT_SEC < 5.0:
                print("[FAIL] HTTP watchdog deadline is shorter than Fly liveness timeout")
                return 1
            for name in required:
                with open(name, "w", encoding="utf-8") as f:
                    f.write("{}\n")
            # Unit-test the completed Flask application, not the deliberate
            # early-boot 503 gate used while production state is restoring.
            bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
            client = bot.app.test_client()
            for path in ("/api/export_csv", "/api/export.csv"):
                response = client.get(path)
                if response.status_code != 200:
                    print(f"[FAIL] {path} returned HTTP {response.status_code}")
                    return 1
                with zipfile.ZipFile(io.BytesIO(response.data), "r") as archive:
                    names = set(archive.namelist())
                missing = sorted(required - names)
                if missing:
                    print(f"[FAIL] {path} missing: {missing}")
                    return 1
            remote = client.get(
                "/api/export.csv",
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
                headers={"X-Forwarded-For": "203.0.113.10"},
            )
            if remote.status_code != 401:
                print(f"[FAIL] unauthenticated remote export returned HTTP {remote.status_code}")
                return 1
            print(f"[PASS] research export includes {len(required)} collection files on /api/export_csv and /api/export.csv")
            return 0
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    raise SystemExit(main())
