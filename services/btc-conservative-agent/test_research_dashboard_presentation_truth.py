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
    helpers = page[page.index("function escapeHtml("):page.index("function ensureScrollableTables(")]
    helpers += page[page.index("function metricNumber("):page.index("function fmtAdxBucket(")]
    helpers += page[page.index("function fmtResearchBucket("):page.index("async function loadSummary(")]
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


@pytest.mark.parametrize("payload,expected", [
    ({}, "no run yet"),
    ({"generated_at": "2026-09-06T12:00:00Z"}, "2026-09-06T12:00:00"),
    ({"analysis_run": {"phase": "FAILED"}}, "Latest analysis attempt FAILED · no report published"),
    ({"generated_at": "2026-09-06T12:00:00Z", "analysis_run": {"phase": "FAILED"}},
     "Latest analysis attempt FAILED · saved report 2026-09-06T12:00:00 (not refreshed by this attempt)"),
    ({"analysis_run": {"phase": "RUNNING"}}, "Analysis running · no report published yet"),
    ({"generated_at": "2026-09-06T12:00:00Z", "analysis_run": {"phase": "RUNNING"}},
     "Analysis running · saved report 2026-09-06T12:00:00"),
])
def test_header_distinguishes_attempt_from_publication(payload, expected):
    assert run_helpers("analyzerAttemptLabel(" + json.dumps(payload) + ")") == expected
    assert "document.getElementById('updated').textContent = analyzerAttemptLabel(d);" in html()


@pytest.mark.parametrize('order', ['summary-first', 'status-first'])
@pytest.mark.parametrize('phase', ['FAILED', 'RUNNING', 'IDLE_BETWEEN_RUNS'])
@pytest.mark.parametrize('generated', [None, '2026-09-06T12:00:00Z'])
def test_actual_loaders_use_status_header_authority_in_either_completion_order(order, phase, generated):
    page = html()
    helpers = page[page.index('function metricNumber('):page.index('function fmtAdxBucket(')]
    summary = page[page.index('async function loadSummary()'):page.index('async function loadDecisionReadiness()')]
    status = page[page.index('async function loadStatus()'):page.index('async function loadGptAuditNote()')]
    status_payload = {'analysis_run': {'phase': phase}, 'generated_at': generated}
    # Summary deliberately has neither runtime phase nor the same publication
    # timestamp. Its completion cannot replace the status endpoint's authority.
    script = helpers + summary + status + """
const elements = {};
const document = {getElementById: id => elements[id] ||= {textContent:'',innerHTML:'',style:{}}};
const EVIDENCE_SCOPES = {};
const setEvidenceScope = () => {};
const loadDecisionReadiness = async () => {};
""" + f"const statusPayload = {json.dumps(status_payload)};\n" + """
const pending = {};
const fetch = url => new Promise(resolve => {pending[url] = () => resolve({json:async () =>
    url === '/api/status' ? statusPayload : {generated_at:'old-summary-report'}});});
(async () => {
  const summaryJob = loadSummary(), statusJob = loadStatus();
"""
    if order == 'summary-first':
        script += "pending['/api/summary'](); await summaryJob; pending['/api/status'](); await statusJob;"
    else:
        script += "pending['/api/status'](); await statusJob; pending['/api/summary'](); await summaryJob;"
    script += "console.log(JSON.stringify(elements.updated.textContent)); })().catch(e => {console.error(e);process.exit(1);});"
    result = subprocess.run([shutil.which('node'), '-e', script], text=True, encoding='utf-8', capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    expected = run_helpers('analyzerAttemptLabel(' + json.dumps(status_payload) + ')')
    assert actual == expected
    assert 'old-summary-report' not in actual


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


def test_global_presentation_distinguishes_freshness_unverified_from_stale_and_current():
    unverified = {"stale": {"stale": False, "generation_freshness": {"current": None}}}
    stale = {"stale": {"stale": True, "generation_freshness": {"current": False}}}
    current = {"stale": {"stale": False, "generation_freshness": {"current": True}},
               "scope": "FRESH-COLLECTION", "data_scope": "SESSION"}

    assert run_helpers("generationFreshnessUnverified(" + json.dumps(unverified) + ")") is True
    assert run_helpers("analyzerHeaderScope(" + json.dumps(unverified) + ")") == \
        "FRESHNESS UNVERIFIED · READ-ONLY"
    assert run_helpers("analyzerHeaderScope(" + json.dumps(stale) + ")") == \
        "STALE SAVED ANALYZER GENERATION · READ-ONLY"
    assert run_helpers("analyzerHeaderScope(" + json.dumps(current) + ")") == \
        "FRESH-COLLECTION · SESSION"

    source = html()
    assert "⚠ Analyzer freshness unverified — read-only." in source
    assert "Saved reports must not be treated as current or safe." in source
    assert "FORENSIC EXPORT · FRESHNESS UNVERIFIED" in source
    assert "const scopeLabel = analyzerHeaderScope(d);" in source


def test_old_fresh_collection_publication_has_truthful_read_only_header_and_diagnostics():
    stale_fresh_collection = {
        "scope": "FRESH-COLLECTION",
        "data_scope": "SESSION",
        "stale": {
            "stale": True,
            "publication_freshness": {
                "status": "STALE",
                "age_seconds": 172800,
                "max_age_seconds": 86400,
                "reason": "REPORT_PUBLICATION_EXCEEDS_UTC_AGE_POLICY",
            },
            "reasons": ["Saved analyzer report publication exceeds the named UTC age policy"],
        },
    }
    assert run_helpers(
        "analyzerHeaderScope(" + json.dumps(stale_fresh_collection) + ")"
    ) == "STALE SAVED FRESH-COLLECTION REPORT · READ-ONLY"

    source = html()
    assert "⚠ Stale saved fresh-collection report — read-only." in source
    assert "Show exact parity and freshness receipts" in source
    assert "publication_freshness: stale.publication_freshness || {}" in source
    assert "const scopeLabel = analyzerHeaderScope(d);" in source
    assert "document.getElementById('exec-text').textContent = formatExecutiveText" in source


def test_publication_age_cannot_be_hidden_by_current_generation_flag():
    stale_publication = {
        "scope": "FRESH-COLLECTION",
        "data_scope": "SESSION",
        "stale": {
            "stale": False,
            "generation_freshness": {"current": True},
            "publication_freshness": {
                "stale": True,
                "status": "STALE",
                "age_seconds": 172800,
                "max_age_seconds": 86400,
            },
        },
    }
    result = run_helpers(
        "analyzerHeaderScope(" + json.dumps(stale_publication) + ")"
    )
    assert result == "STALE SAVED FRESH-COLLECTION REPORT · READ-ONLY"
    scope = run_helpers(
        "summaryEvidenceScope(" + json.dumps(stale_publication) + ")"
    )
    assert "STALE SAVED" in scope[0]


def test_identity_strip_keeps_analyzer_code_and_report_data_roles_separate():
    identity = {
        "status": "STALE",
        "analyzer_generation_revision": "a" * 40,
        "report_dataset_source_revision": "b" * 40,
        "local_mirror_source_revision": "c" * 40,
        "report_to_mirror_revision_parity": "MISMATCH",
        "mirror_to_fly_revision_parity": "UNAVAILABLE",
    }
    label = run_helpers("analyzerIdentityLabel(" + json.dumps(identity) + ")")
    assert label == (
        "Identity STALE · analyzer " + "a" * 12
        + " · report data " + "b" * 12
        + " · local mirror " + "c" * 12
        + " · report→mirror MISMATCH · mirror→Fly UNAVAILABLE"
    )
    source = html()
    assert 'id="identity-summary"' in source
    assert "function analyzerIdentityLabel(identity)" in source
    assert "value.report_dataset_source_revision" in source
    assert "value.local_mirror_source_revision" in source


def test_runtime_and_research_report_facts_are_separate_and_reuse_readiness_dimensions():
    payload = {
        "runtime_observed_at": "2026-09-14T12:00:00+00:00",
        "generated_at": "2026-09-14T11:30:00+00:00",
        "fresh_epoch_id": "epoch-current",
        "service_ready": True,
        "report_bundle_ready": True,
        "qualification_ready": False,
        "stale": False,
        "generation_freshness": {"current": True},
        "identity": {
            "status": "CURRENT",
            "analyzer_generation_revision": "a" * 40,
            "report_dataset_source_revision": "b" * 40,
            "local_mirror_source_revision": "b" * 40,
            "report_to_mirror_revision_parity": "MATCH",
            "mirror_to_fly_revision_parity": "MATCH",
        },
    }
    facts = run_helpers("analyzerResearchFacts(" + json.dumps(payload) + ")")
    assert "Runtime response observation 2026-09-14T12:00:00+00:00" in facts
    assert "Research report publication 2026-09-14T11:30:00+00:00" in facts
    assert "Identity CURRENT" in facts
    assert "service READY · reports READY · qualification BLOCKED" in facts
    assert facts.endswith("CURRENT REPORT")
    source = html()
    assert 'id="research-provenance-summary"' in source
    assert "provenanceEl.textContent = analyzerResearchFactsFromPresentation(presentation)" in source


def test_analyzer_timestamp_contract_rejects_bad_types_and_invalid_iso():
    bad_values = [
        None, True, False, {}, [], 1789000000,
        "2026-09-14T12:00:00",
        "2026-02-30T12:00:00Z",
        "2026-09-14T25:00:00Z",
        "2026-09-14T12:00:00+15:00",
    ]
    assert run_helpers(
        json.dumps(bad_values) + ".map(value => analyzerIsoTimestamp(value))"
    ) == [None] * len(bad_values)
    assert run_helpers(
        "[analyzerIsoTimestamp('2026-09-14T12:00:00Z'),"
        "analyzerIsoTimestamp('2026-09-14T12:00:00+10:00')]"
    ) == ["2026-09-14T12:00:00Z", "2026-09-14T12:00:00+10:00"]


def test_analyzer_revision_and_epoch_contract_rejects_bad_identifiers():
    assert run_helpers(
        "[analyzerFullRevision('" + "a" * 40 + "'),"
        "analyzerFullRevision('short'),analyzerFullRevision('" + "g" * 40 + "'),"
        "analyzerFullRevision(true),analyzerFullRevision({}),"
        "analyzerEpochId('epoch-current'),analyzerEpochId('current'),"
        "analyzerEpochId(true),analyzerEpochId({})]"
    ) == ["a" * 40, None, None, None, None, "epoch-current", None, None, None]


def test_analyzer_parity_dominates_positive_flags_and_bad_provenance():
    payload = {
        "runtime_observed_at": "2026-09-14T12:00:00Z",
        "generated_at": "2026-09-14T11:30:00Z",
        "fresh_epoch_id": "epoch-current",
        "service_ready": True,
        "report_bundle_ready": True,
        "qualification_ready": False,
        "stale": False,
        "generation_freshness": {"current": True},
        "identity": {
            "status": "CURRENT",
            "analyzer_generation_revision": "a" * 40,
            "report_dataset_source_revision": "b" * 40,
            "local_mirror_source_revision": "b" * 40,
            "report_to_mirror_revision_parity": "MATCH",
            "mirror_to_fly_revision_parity": "MATCH",
        },
    }
    assert run_helpers(
        "normalizeAnalyzerPresentation(" + json.dumps(payload) + ").report_freshness"
    ) == "CURRENT REPORT"
    assert run_helpers(
        "analyzerHeaderFromPresentation(normalizeAnalyzerPresentation("
        + json.dumps(payload) + "))"
    ) == "CURRENT ANALYZER REPORT · NOT QUALIFIED"

    for parity in (None, "UNKNOWN", "MATCH "):
        changed = json.loads(json.dumps(payload))
        changed["identity"]["mirror_to_fly_revision_parity"] = parity
        assert run_helpers(
            "normalizeAnalyzerPresentation(" + json.dumps(changed) + ").report_freshness"
        ) == "FRESHNESS UNVERIFIED"
    for parity in ("MISMATCH", "CONFLICT"):
        changed = json.loads(json.dumps(payload))
        changed["identity"]["mirror_to_fly_revision_parity"] = parity
        assert run_helpers(
            "normalizeAnalyzerPresentation(" + json.dumps(changed) + ").report_freshness"
        ) == "HISTORICAL/STALE"

    for path, value in (
        ("generated_at", "2026-09-14T11:30:00"),
        ("fresh_epoch_id", {}),
        ("report_dataset_source_revision", "short"),
    ):
        changed = json.loads(json.dumps(payload))
        if path in changed:
            changed[path] = value
        else:
            changed["identity"][path] = value
        assert run_helpers(
            "normalizeAnalyzerPresentation(" + json.dumps(changed) + ").report_freshness"
        ) == "FRESHNESS UNVERIFIED"


def test_analyzer_runtime_clock_is_distinct_and_shared_normalization_agrees():
    payload = {
        "runtime_observed_at": "not-a-time",
        "generated_at": "2026-09-14T11:30:00Z",
        "fresh_epoch_id": "epoch-current",
        "report_bundle_ready": True,
        "qualification_ready": False,
        "stale": False,
        "generation_freshness": {"current": True},
        "identity": {
            "status": "CURRENT",
            "analyzer_generation_revision": "a" * 40,
            "report_dataset_source_revision": "b" * 40,
            "local_mirror_source_revision": "b" * 40,
            "report_to_mirror_revision_parity": "MATCH",
            "mirror_to_fly_revision_parity": "MATCH",
        },
    }
    normalized = run_helpers(
        "normalizeAnalyzerPresentation(" + json.dumps(payload) + ")"
    )
    assert normalized["runtime_observed_at"] is None
    assert normalized["runtime_observation_label"] == "UNAVAILABLE"
    assert normalized["report_freshness"] == "CURRENT REPORT"
    facts = run_helpers(
        "analyzerResearchFactsFromPresentation(normalizeAnalyzerPresentation("
        + json.dumps(payload) + "))"
    )
    assert "Runtime response observation UNAVAILABLE" in facts
    assert facts.endswith(normalized["report_freshness"])
    assert run_helpers(
        "analyzerHeaderFromPresentation(normalizeAnalyzerPresentation("
        + json.dumps(payload) + "))"
    ) == "CURRENT ANALYZER REPORT · NOT QUALIFIED"


def test_malformed_failed_or_no_data_status_never_invents_publication():
    malformed_failed = {
        "generated_at": True,
        "analysis_run": {"phase": "FAILED"},
        "generation_freshness": {"current": True},
        "report_bundle_ready": True,
    }
    assert run_helpers(
        "analyzerAttemptLabel(" + json.dumps(malformed_failed) + ")"
    ) == "Latest analysis attempt FAILED · no report published"
    assert run_helpers(
        "analyzerReportFreshnessLabel(" + json.dumps(malformed_failed) + ")"
    ) == "FRESHNESS UNVERIFIED"
    assert run_helpers("analyzerAttemptLabel({})") == "no run yet"


def test_stale_failed_report_and_no_data_remain_distinct_from_runtime_observation():
    stale_failed = {
        "runtime_observed_at": "2026-09-14T12:00:00+00:00",
        "generated_at": "2026-09-10T01:00:00+00:00",
        "analysis_run": {"phase": "FAILED"},
        "service_ready": True,
        "report_bundle_ready": False,
        "qualification_ready": False,
        "stale": True,
        "generation_freshness": {"current": False},
        "identity": {"status": "STALE"},
    }
    facts = run_helpers("analyzerResearchFacts(" + json.dumps(stale_failed) + ")")
    assert "Runtime response observation 2026-09-14T12:00:00+00:00" in facts
    assert "Research report publication 2026-09-10T01:00:00+00:00" in facts
    assert "service READY · reports BLOCKED · qualification BLOCKED" in facts
    assert facts.endswith("HISTORICAL/STALE")
    assert run_helpers(
        "analyzerReportFreshnessLabel(" + json.dumps(stale_failed) + ")"
    ) == "HISTORICAL/STALE"

    no_data = run_helpers("analyzerResearchFacts({analysis_run:{phase:'FAILED'}})")
    assert "Runtime response observation UNAVAILABLE" in no_data
    assert "Research report publication UNAVAILABLE" in no_data
    assert "qualification UNKNOWN" in no_data
    assert no_data.endswith("FRESHNESS UNVERIFIED")


def test_pathway_audit_presents_analyzer_code_and_report_data_as_distinct_rows():
    source = html()
    assert "['Analyzer code revision', currentSync.analyzer_code_revision" in source
    assert "['Report data revision', currentSync.report_dataset_source_revision || 'UNAVAILABLE']" in source
    assert "['Analyzer source revision'" not in source


def test_old_launch_instruction_replaced_without_changing_findings():
    raw = "Finding: policy A has 12 UNKNOWN episodes\ncd C:\\Old Laptop\\Final Bots\nRetained historical cohort"
    display = run_helpers("formatExecutiveText(" + json.dumps(raw) + ")")
    assert "Final Bots" not in display
    assert "C:\\DoxxedCrypto\\btc-v31-current" in display
    assert "do not start a duplicate" in display
    assert display.splitlines()[0] == raw.splitlines()[0]
    assert display.splitlines()[-1] == raw.splitlines()[-1]
    assert "formatExecutiveText(d.executive_text, d)" in html()


def test_no_direct_unavailable_currency_or_percentage_templates_remain():
    source = html()
    assert "$${fmtUsd(" not in source
    assert "(p.win_rate_pct ?? 'n/a') + '%'" not in source
    assert "'$' + (p.expectancy_usd ?? 'n/a')" not in source
    assert "fmtPct(p.win_rate_pct)" in source


def test_missing_oos_counts_are_unavailable_while_true_zero_is_preserved():
    source = html()
    assert "oos_episodes??0" not in source
    assert "oos_episodes ?? 0" not in source
    assert "oos_episodes||0" not in source
    assert "oos_episodes || 0" not in source
    assert "oos_episodes ?? 'UNAVAILABLE'" in source
    assert run_helpers("[({oos_episodes:0}).oos_episodes ?? 'UNAVAILABLE',"
                       "({}).oos_episodes ?? 'UNAVAILABLE']") == [0, "UNAVAILABLE"]


def render_loader(name, payload, target):
    page = html()
    helpers = page[page.index("function escapeHtml("):page.index("function ensureScrollableTables(")]
    helpers += page[page.index("function metricNumber("):page.index("function fmtAdxBucket(")]
    helpers += page[page.index("function fmtResearchBucket("):page.index("async function loadSummary(")]
    helpers += page[page.index("function laneApprovalCount("):page.index("async function loadLanes(")]
    helpers += page[page.index("function executionPanelSource("):page.index("async function loadChaseThreshold(")]
    start = page.index("async function " + name + "(")
    end = page.index("async function ", start + 15)
    loader = page[start:end]
    fixture = """
const elements = new Map();
const element = () => ({innerHTML:'', textContent:'', children:[],
  querySelector: function() { return this.children.find(node => node.className === 'execution-source-identity') || null; },
  querySelectorAll: () => [], prepend: function(node) { this.children.unshift(node); }});
const document = {getElementById: id => {
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id);
}, createElement: () => element()};
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
        "pnl": 0, "ev": 0, "counterfactual_pnl": 0,
        "metric_available": {"executed_closes": True, "pnl": True, "ev": True, "counterfactual_pnl": True}}),
    ("loadChase", "executed_buckets", "chase-body", {"bucket": "1", "trades": 1,
        "sum_pnl_usd": 0, "pnl_usd": 999, "ev_usd": 0, "ev": 999}),
    ("loadChaseThreshold", "executed_thresholds", "chase-threshold-body", {"threshold": "1", "trades": 1,
        "sum_pnl_usd": 0, "pnl_usd": 999, "ev_usd": 0, "ev": 999}),
])
def test_lane_and_chase_preserve_observed_zero_money(name, field, target, row):
    rendered = render_loader(name, {"evidence_status": "CURRENT_GENERATION", field: [row]}, target)
    assert rendered.count("$0.00") >= 2
    assert "$+999" not in rendered


def complete_touch_proof(touch=False, outcome="NO_FILL"):
    return {"classification": "INSUFFICIENT_EVIDENCE", "conservative_touch": touch,
            "conservative_fill_status": outcome, "execution_outcome": outcome,
            "coverage": {"status": "COMPLETE", "stage_ratio": 1, "tape_status": "SUPPORTED",
                         "missing_seconds": 0, "tape_receipt": "tape-join-sha256-fixture",
                         "identity_missing": [], "rejection_codes": []}}


@pytest.mark.parametrize("touch", [False, None, "false"])
def test_missed_proof_missing_779_seconds_never_renders_no_touch(touch):
    row = complete_touch_proof(touch)
    row["coverage"].update(status="INSUFFICIENT", missing_seconds=779)
    rendered = render_loader("loadChasePolicyLab", {"proofs": [row]}, "missed-proof-body")
    assert "UNKNOWN — touch not established in available tape" in rendered
    assert "missing seconds 779" in rendered
    assert "NO TOUCH" not in rendered
    assert "INSUFFICIENT_EVIDENCE" in rendered


@pytest.mark.parametrize("field,value", [
    ("status", "INSUFFICIENT"), ("stage_ratio", None), ("stage_ratio", 0.9),
    ("tape_status", "UNAVAILABLE"), ("missing_seconds", None), ("missing_seconds", 1),
    ("tape_receipt", None), ("identity_missing", ["episode_id"]),
    ("identity_missing", None), ("rejection_codes", ["IDENTITY_INCOMPLETE"]),
    ("rejection_codes", None),
])
def test_incomplete_or_inconsistent_coverage_never_renders_negative(field, value):
    row = complete_touch_proof()
    row["coverage"][field] = value
    label = run_helpers("missedProofTouchLabel(" + json.dumps(row) + ")")
    assert label.startswith("UNKNOWN")
    assert "NO TOUCH" not in label


def test_complete_no_fill_retains_observed_negative_without_inventing_profitability():
    row = complete_touch_proof()
    rendered = render_loader("loadChasePolicyLab", {"proofs": [row]}, "missed-proof-body")
    assert "NO TOUCH — COMPLETE APPLICABLE TAPE" in rendered
    # No-touch can still lack a profitable/avoided-loss counterfactual.
    assert "INSUFFICIENT_EVIDENCE" in rendered
    assert "net UNAVAILABLE" in rendered


@pytest.mark.parametrize("outcome", ["FULL_FILL", "PARTIAL_FILL"])
def test_complete_supported_touch_preserved(outcome):
    row = complete_touch_proof(True, outcome)
    rendered = render_loader("loadChasePolicyLab", {"proofs": [row]}, "missed-proof-body")
    assert "TOUCH OBSERVED — CONSERVATIVE TAPE SUPPORTED" in rendered


def test_checkpoint_only_touch_not_upgraded_to_supported_conservative_touch():
    row = complete_touch_proof(True, "UNKNOWN")
    row["coverage"].update(status="INSUFFICIENT", missing_seconds=779)
    rendered = render_loader("loadChasePolicyLab", {"proofs": [row]}, "missed-proof-body")
    assert "UNKNOWN — checkpoint touch reported" in rendered
    assert "TOUCH OBSERVED" not in rendered


@pytest.mark.parametrize("row", [{}, {"conservative_touch": False},
    {**complete_touch_proof(), "execution_outcome": "UNKNOWN"},
    {**complete_touch_proof(), "conservative_touch": None}])
def test_missing_touch_or_conflicting_outcome_is_unknown(row):
    assert run_helpers("missedProofTouchLabel(" + json.dumps(row) + ")").startswith("UNKNOWN")


def test_compressed_panel_qualification_label_does_not_exclude_all_shadow_research():
    source = html()
    assert "Other shadow simulations are assessed separately by the conservative execution evaluator" in source
    row = {"qualification_status": "NOT_QUALIFICATION_ELIGIBLE_SHADOW_ONLY"}
    rendered = render_loader("loadChasePolicyLab", {"ranked_schedules": [row]}, "chase-policy-body")
    assert "DESCRIPTIVE ONLY — THIS SIGNED-COMPRESSED PANEL" in rendered
    assert "NOT_QUALIFICATION_ELIGIBLE_SHADOW_ONLY" not in rendered
    assert run_helpers("compressedScheduleQualification('UNKNOWN_IDENTITY_INCIDENT')") == "UNKNOWN_IDENTITY_INCIDENT"
