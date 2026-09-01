"""Research ZIP export contract.

Run: cd services/btc-conservative-agent && python test_research_export_csv.py
"""
import io
import json
import os
import sys
import tempfile
import zipfile

os.environ["FORCE_PAPER_MODE"] = "1"
os.environ["SKIP_EXCHANGE_MARKET_LOAD"] = "1"
os.environ["BOT_ADMIN_TOKEN"] = "deterministic-export-route-test-token"

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
    previous_bootstrap = bot._DASHBOARD_BOOTSTRAP_COMPLETE
    previous_admin_token = bot._BOT_ADMIN_TOKEN
    with tempfile.TemporaryDirectory(prefix="research_export_") as tmp:
        try:
            # bot captures BOT_ADMIN_TOKEN at import time. Other broad-suite
            # modules may have imported it first under their own fixture env,
            # so isolate this route contract from collection order.
            bot._BOT_ADMIN_TOKEN = os.environ["BOT_ADMIN_TOKEN"]
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
            fixtures = {
                name: {"fixture": name, "kind": "collection"}
                for name in required
            }
            fixtures[bot.PATHWAY_LANE_SPECS_FILE] = {
                "schema": "pathway_lane_specs_test_v1",
                "lanes": [{"lane_id": "current", "enabled": True}],
            }
            fixtures[bot.LANE_PNL_LEDGER_FILE] = {
                "schema": "lane_pnl_ledger_test_v1",
                "entries": [{"lane_id": "current", "net_pnl": "1.25"}],
            }
            for name, fixture in fixtures.items():
                with open(name, "w", encoding="utf-8") as f:
                    json.dump(fixture, f, sort_keys=True)
                    f.write("\n")

            # A crashed/in-progress mirror transfer must never become a ZIP
            # member merely because its stable filename starts with a known
            # collection filename.
            temporary_member = f"{bot.LANE_PNL_LEDGER_FILE}.13980.deadbeef.download"
            with open(temporary_member, "w", encoding="utf-8") as f:
                f.write('{"must_not_export":true}\n')

            # This file is in research_export_files(), but deliberately absent
            # from the isolated fixture. The route must omit it truthfully,
            # rather than inventing an empty member.
            absent_member = bot.CSV_AI_ERRORS
            if os.path.exists(absent_member):
                os.unlink(absent_member)

            # Unit-test the completed Flask application, not the deliberate
            # early-boot 503 gate used while production state is restoring.
            bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
            client = bot.app.test_client()
            for path in ("/api/export_csv", "/api/export.csv"):
                response = client.get(
                    path,
                    environ_base={"REMOTE_ADDR": "203.0.113.10"},
                    headers={
                        "X-Forwarded-For": "203.0.113.10",
                        "X-Bot-Admin-Token": os.environ["BOT_ADMIN_TOKEN"],
                    },
                )
                if response.status_code != 200:
                    print(f"[FAIL] {path} returned HTTP {response.status_code}")
                    return 1
                if response.mimetype != "application/zip":
                    print(f"[FAIL] {path} MIME was {response.mimetype!r}")
                    return 1
                disposition = response.headers.get("Content-Disposition", "")
                if "attachment" not in disposition or "3factor_logs.zip" not in disposition:
                    print(f"[FAIL] {path} disposition was {disposition!r}")
                    return 1
                with zipfile.ZipFile(io.BytesIO(response.data), "r") as archive:
                    if archive.testzip() is not None:
                        print(f"[FAIL] {path} contains a member with invalid CRC")
                        return 1
                    archive_names = archive.namelist()
                    names = set(archive_names)
                    if len(names) != len(archive_names):
                        print(f"[FAIL] {path} contains duplicate member names")
                        return 1
                    unsafe = sorted(
                        name for name in names
                        if name != os.path.basename(name)
                        or ".download" in name
                        or name.endswith(".tmp")
                    )
                    if unsafe:
                        print(f"[FAIL] {path} contains unsafe/temp members: {unsafe}")
                        return 1
                    specs = json.loads(archive.read(bot.PATHWAY_LANE_SPECS_FILE))
                    ledger = json.loads(archive.read(bot.LANE_PNL_LEDGER_FILE))
                missing = sorted(required - names)
                if missing:
                    print(f"[FAIL] {path} missing: {missing}")
                    return 1
                if absent_member in names:
                    print(f"[FAIL] {path} invented absent member: {absent_member}")
                    return 1
                if temporary_member in names:
                    print(f"[FAIL] {path} exported temporary member: {temporary_member}")
                    return 1
                if specs != fixtures[bot.PATHWAY_LANE_SPECS_FILE]:
                    print(f"[FAIL] {path} pathway manifest content changed")
                    return 1
                if ledger != fixtures[bot.LANE_PNL_LEDGER_FILE]:
                    print(f"[FAIL] {path} lane ledger content changed")
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
            bot._BOT_ADMIN_TOKEN = previous_admin_token
            bot._DASHBOARD_BOOTSTRAP_COMPLETE = previous_bootstrap
            os.chdir(previous)


def test_research_export_route_contract():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
