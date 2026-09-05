"""Execute presentation helpers and parse the complete rendered dashboard JS."""
import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Template

SOURCE = Path(__file__).parent / "research" / "research_dashboard.py"


def html():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "DASHBOARD_HTML" for target in node.targets))


def run_helpers(expression):
    page = html()
    helpers = page[page.index("function metricNumber("):page.index("function fmtAdxBucket(")]
    node = shutil.which("node")
    assert node, "Node is required for executable dashboard presentation QA"
    result = subprocess.run([node, "-e", helpers + "\nconsole.log(JSON.stringify(" + expression + "));"],
                            text=True, encoding="utf-8", capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_complete_dashboard_javascript_parses():
    rendered = Template(html()).render(nav_groups_json="[]", tile_lane_names="")
    script = re.search(r"<script>([\s\S]*?)</script>", rendered).group(1)
    node = shutil.which("node")
    assert node
    result = subprocess.run([node, "--check"], input=script, text=True, encoding="utf-8",
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["null", "undefined", "NaN", "Infinity", "-Infinity", "''", "'n/a'", "true", "false", "[]", "{}"])
def test_missing_invalid_metrics_have_no_currency_or_percent_suffix(value):
    assert run_helpers(f"[fmtExecutionUsd({value}), fmtPct({value})]") == ["UNAVAILABLE", "UNAVAILABLE"]


def test_zero_and_real_metrics_preserve_units_and_sign():
    assert run_helpers("[fmtExecutionUsd(0),fmtExecutionUsd(1.23),fmtExecutionUsd(-2),fmtPct(0),fmtPct(65.5)]") == [
        "$0.00", "$+1.23", "$-2.00", "0%", "65.5%"]


@pytest.mark.parametrize("payload", [{}, {"stale": {}}, {"stale": {"stale": False}},
    {"stale": {"stale": False, "generation_freshness": {"current": True}}},
    {"stale": {"stale": False, "generation_freshness": {"current": True}}, "integrity": {"valid": True, "report_status": "UNKNOWN"}},
    {"stale": {"stale": False, "generation_freshness": {"current": True}}, "integrity": {"valid": False}}])
def test_missing_receipts_never_claim_current(payload):
    scope = run_helpers("summaryEvidenceScope(" + json.dumps(payload) + ")")
    assert "UNVERIFIED" in scope[0]
    assert "CURRENT PINNED" not in scope[0]


def test_stale_scope_overrides_otherwise_current_flag():
    scope = run_helpers("summaryEvidenceScope({stale:{stale:true,generation_freshness:{current:true}}})")
    assert "STALE SAVED" in scope[0]
    assert "not current session data" in scope[1]


def test_current_is_not_claimed_as_qualified():
    scope = run_helpers("summaryEvidenceScope({stale:{stale:false,generation_freshness:{current:true}},integrity:{valid:true,report_status:'VALID'}})")
    assert "CURRENT PINNED" in scope[0]
    assert "Current does not mean qualified" in scope[1]
    source = html()
    assert "EVIDENCE_SCOPES.summary = summaryEvidenceScope(d);" in source
    assert "setEvidenceScope('summary', ...EVIDENCE_SCOPES.summary);" in source
    assert "Best-policy evidence is current/pinned" not in source


def test_old_launch_instruction_replaced_without_changing_findings():
    raw = "Finding: policy A has 12 UNKNOWN episodes\ncd C:\\Old Laptop\\Final Bots\nRetained historical cohort"
    display = run_helpers("formatExecutiveText(" + json.dumps(raw) + ")")
    assert "Final Bots" not in display
    assert "C:\\DoxxedCrypto\\btc-v31-current" in display
    assert "do not start a duplicate" in display
    assert display.splitlines()[0] == raw.splitlines()[0]
    assert display.splitlines()[-1] == raw.splitlines()[-1]
    assert "formatExecutiveText(d.executive_text)" in html()


def test_no_direct_unavailable_currency_or_percentage_templates_remain():
    source = html()
    assert "$${fmtUsd(" not in source
    assert "(p.win_rate_pct ?? 'n/a') + '%'" not in source
    assert "'$' + (p.expectancy_usd ?? 'n/a')" not in source
    assert "fmtPct(p.win_rate_pct)" in source


def render_loader(name, payload, target):
    page = html()
    helpers = page[page.index("function metricNumber("):page.index("function fmtAdxBucket(")]
    start = page.index("async function " + name + "(")
    end = page.index("async function ", start + 15)
    loader = page[start:end]
    fixture = """
const elements = new Map();
const document = {getElementById: id => {
  if (!elements.has(id)) elements.set(id, {innerHTML:'', textContent:''});
  return elements.get(id);
}};
function chaseLaneQuery() { return ''; }
const fetch = async () => ({json: async () => (PAYLOAD)});
""".replace("PAYLOAD", json.dumps(payload))
    script = helpers + fixture + loader + "\n(async()=>{await " + name + "();console.log(JSON.stringify(document.getElementById(" + json.dumps(target) + ").innerHTML));})().catch(e=>{console.error(e);process.exitCode=1;});"
    result = subprocess.run([shutil.which("node"), "-e", script], text=True, encoding="utf-8",
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("name,field,target,row", [
    ("loadLanes", "lanes", "lane-body", {"lane": "FIXED", "executed_closes": 1}),
    ("loadChase", "executed_buckets", "chase-body", {"bucket": "1", "trades": 1}),
    ("loadChaseThreshold", "executed_thresholds", "chase-threshold-body", {"threshold": "1", "trades": 1}),
])
def test_lane_and_chase_render_missing_money_as_unavailable(name, field, target, row):
    rendered = render_loader(name, {"evidence_status": "CURRENT_GENERATION", field: [row]}, target)
    assert rendered.count("UNAVAILABLE") >= 2
    assert "$0.00" not in rendered
    assert "$n/a" not in rendered


@pytest.mark.parametrize("name,field,target,row", [
    ("loadLanes", "lanes", "lane-body", {"lane": "FIXED", "executed_closes": 1,
        "pnl": 0, "ev": 0, "counterfactual_pnl": 0}),
    ("loadChase", "executed_buckets", "chase-body", {"bucket": "1", "trades": 1,
        "sum_pnl_usd": 0, "pnl_usd": 999, "ev_usd": 0, "ev": 999}),
    ("loadChaseThreshold", "executed_thresholds", "chase-threshold-body", {"threshold": "1", "trades": 1,
        "sum_pnl_usd": 0, "pnl_usd": 999, "ev_usd": 0, "ev": 999}),
])
def test_lane_and_chase_preserve_observed_zero_money(name, field, target, row):
    rendered = render_loader(name, {"evidence_status": "CURRENT_GENERATION", field: [row]}, target)
    assert rendered.count("$0.00") >= 2
    assert "$+999" not in rendered
