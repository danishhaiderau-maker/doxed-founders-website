"""Prevent research-only results from being presented as account orders or PnL."""

from pathlib import Path
import json
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent
BOT_SOURCE = ROOT / "bot.py"


def main() -> None:
    source = BOT_SOURCE.read_text(encoding="utf-8")

    assert "Verdicts are research evaluations, not orders" in source
    assert "executable orders appear only in Pending Orders above" in source
    assert "legacy call — lane metadata unavailable" in source
    assert "verdict not recorded" not in source
    assert "AI call failed — no verdict" in source
    assert "evaluation not reached" in source
    assert "RESTORED_PRE_RESTART_NO_LANE_METADATA" in source
    assert ">not evaluated</span>" not in source
    assert ">pending</span>" not in source

    # A live process or fresh market BBO is not enough to present a two-day-old
    # AI/tile payload as current research. The dashboard must expose this
    # distinction before a user can interpret any visible tile totals.
    assert 'id="researchFreshness"' in source
    assert "Research evidence: STALE" in source
    assert "Current server and market-feed status are separate" in source
    assert "visible tile totals are not a current strategy ranking" in source
    assert "d.execution_paused === true || d.manual_admin_pause === true" in source
    assert "Process clocks, cooldown reservations" in source
    assert "const completedResearchRows" in source
    assert "d.last_ai_call_ts," not in source
    assert "d.ai_history_updated," not in source
    assert 'id="restoredEvidenceBadge"' in source
    assert "HISTORICAL / NON-CURRENT" in source
    assert "RESTORED_AFTER_RESTART" in source

    # Tile headlines deliberately show one comparable fresh-collection
    # accounting row only. Shadow/counterfactual results stay in analyzer
    # reports and must never be mixed into account-like tile PnL.
    assert "statRow('Closed'" in source
    assert "statRow('PnL'" in source
    assert "statRow('EV/appr'" in source
    assert "statRow('Shadow trades'" not in source
    assert "statRow('Shadow PnL'" not in source
    assert "Counterfactual PnL (not account)" not in source
    assert "HISTORICAL / DIAGNOSTIC METRICS · NOT CURRENT RANKING EVIDENCE" in source
    assert "CURRENT PERIOD METRICS · still not a strategy ranking" in source

    assert "'calculating…'" in source
    assert 'unrealUsd < 0 ? \'-$\' : \'$\'' in source

    print("Dashboard research-status clarity tests passed")


def test_research_freshness_uses_completed_durable_rows_not_process_clocks():
    source = BOT_SOURCE.read_text(encoding="utf-8")
    start = source.index("const completedResearchRows =")
    end = source.index("const dashboardRestore =", start)
    projection = source[start:end]
    node = shutil.which("node")
    assert node, "node is required for dashboard behavior tests"

    cases = [
        # Current service/cooldown/input clocks alone must not manufacture
        # current research evidence.
        {
            "last_ai_call_ts": 1999999999,
            "ai_input_time": "2033-05-18T03:33:19Z",
            "ai_history_updated": 1999999999,
            "ai_history": [],
        },
        # A failed call is not a completed research decision.
        {"ai_history": [{"time": 200, "decision": "AI_ERROR", "ai_error": True}]},
        # A durable completed decision is eligible and keeps its recorded time.
        {"ai_history": [{"time": 123, "decision": "APPROVE", "ai_error": False}]},
    ]
    script = f"""
const cases = {json.dumps(cases)};
const outputs = cases.map(d => {{
  const toEpochSec = value => typeof value === 'number' ? value : Date.parse(String(value)) / 1000;
  {projection}
  return newestResearchTs;
}});
console.log(JSON.stringify(outputs));
"""
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [None, None, 123]


def test_family_verdict_fallback_preserves_row_provenance_and_raw_verdict():
    source = BOT_SOURCE.read_text(encoding="utf-8")
    verdict_start = source.index("const formatLaneVerdict =")
    verdict_end = source.index("const formatPatientRoute =", verdict_start)
    verdict_formatter = source[verdict_start:verdict_end]
    raw_start = source.index("const formatRawAiVerdict =", verdict_end)
    raw_end = source.index("safeHTML('aiHistoryTable'", raw_start)
    raw_formatter = source[raw_start:raw_end]
    node = shutil.which("node")
    assert node, "node is required for dashboard behavior tests"

    cases = [
        {"verdict_provenance": "RESTORED_PRE_RESTART_NO_LANE_METADATA"},
        {"ai_error": True},
        {},
        {"lane_verdicts": {"FAMILY_FIXED": {"accepted": True, "score": 7}}},
        {
            "decision": "REJECT",
            "lane_verdicts": {"FAMILY_FIXED": {"accepted": True}},
        },
    ]
    script = f"""
{verdict_formatter}
const formatPatientRoute = () => '<span>route</span>';
{raw_formatter}
const laneBadge = lane => lane;
const cases = {json.dumps(cases)};
const outputs = cases.map(a => {{
  const verdicts = a.lane_verdicts || {{}};
  const familyRows = Object.keys(verdicts)
    .filter(lane => lane.startsWith('FAMILY_'))
    .sort()
    .map(lane => `<div>${{laneBadge(lane, lane)}}: ${{formatLaneVerdict(verdicts[lane], a)}} · ${{formatPatientRoute(a['tile_route_' + lane.toLowerCase()])}}</div>`)
    .join('') || formatLaneVerdict(null, a);
  return {{familyRows, rawVerdict: formatRawAiVerdict(a)}};
}});
const emDash = String.fromCodePoint(0x2014);
const middleDot = String.fromCodePoint(0xb7);
console.log(JSON.stringify([
  outputs[0].familyRows.includes('legacy call ' + emDash + ' lane metadata unavailable'),
  outputs[1].familyRows.includes('AI call failed ' + emDash + ' no verdict'),
  outputs[2].familyRows.includes('evaluation not reached'),
  outputs[3].familyRows.includes('ACCEPT ' + middleDot + ' score 7'),
  outputs[4].rawVerdict.includes('REJECT') && outputs[4].familyRows.includes('ACCEPT'),
]));
"""
    # Windows command-line transport can replace non-ASCII argv characters.
    # JavaScript escapes reconstruct the real labels inside Node instead.
    script = script.encode("ascii", "backslashreplace").decode("ascii")
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [True, True, True, True, True]


def test_inventory_and_runtime_receipts_have_separate_truth_labels():
    source = BOT_SOURCE.read_text(encoding="utf-8")
    assert "server inventory generated" in source
    assert 'id="dataSizeBrowserPoll"' in source
    assert "lastEl.textContent = body.inventory_generated_at" in source
    assert "browserPollEl.textContent = formatMelbourneNow()" in source
    assert "Retained application incident receipts" in source
    assert "Application watchdog requested process restart?" in source
    assert "application watchdog request only" in source
    assert "not a Fly machine/deployment restart" in source


if __name__ == "__main__":
    main()
