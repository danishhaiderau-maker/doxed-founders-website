"""Regression tests for every guarded-deploy boundary failure mode seen so far.

#164  cancel mutation timed out -> must be unconfirmed, not fatal.
#171  unconfirmed cancel whose order then left the book -> 404 is "already absent".
e5a14a31 (run 36533147639)  a CPU-starved runtime truncated the fresh relay
      read (IncompleteRead) and timed out -> reads retry, mutations unconfirmed,
      and every failure exit ends with a guaranteed paper resume.
"""
import ast
import http.client
import io
import json
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WORKFLOW = (HERE.parents[1] / ".github" / "workflows" / "fly-bot-deploy.yml").read_text(encoding="utf-8")
BOT_SOURCE = (HERE / "bot.py").read_text(encoding="utf-8")


def _step_python(step_name):
    start = WORKFLOW.index(f"- name: {step_name}")
    body_start = WORKFLOW.index("python - <<'PY'\n", start) + len("python - <<'PY'\n")
    body_end = WORKFLOW.index("\n          PY\n", body_start)
    return textwrap.dedent(WORKFLOW[body_start:body_end])


def _functions(source, names, namespace):
    tree = ast.parse(source)
    selected = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    assert sorted(n.name for n in selected) == sorted(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), "<workflow>", "exec"), namespace)
    return namespace


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"{}"))


TRANSIENT_FAILURES = [
    TimeoutError("timed out"),
    http.client.IncompleteRead(b"x" * 209304, 207493),
    http.client.RemoteDisconnected("closed"),
    ConnectionResetError("reset"),
    urllib.error.URLError("unreachable"),
    json.JSONDecodeError("truncated", "{", 1),
    _http_error(502),
    _http_error(503),
    _http_error(504),
]


def _maintenance(responses):
    calls = []

    def request_json(path, payload=None, timeout=45):
        calls.append((path, payload, timeout))
        value = responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    namespace = {
        "http": http, "json": json, "time": type("T", (), {"sleep": staticmethod(lambda _s: None)}),
        "urllib": urllib, "request_json": request_json,
        "require_legacy_bootstrap_status": lambda **_: True,
    }
    _functions(_step_python("Enter durable authenticated paper maintenance boundary"),
               {"transient", "mutate_json", "fresh_exposure"}, namespace)
    return namespace, calls


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES, ids=lambda e: type(e).__name__ + str(getattr(e, "code", "")))
def test_fresh_exposure_survives_starved_runtime_reads(failure):
    exposure = {"money_state_generation": 7, "orders": [], "positions": []}
    ns, calls = _maintenance([failure, failure, exposure])
    assert ns["fresh_exposure"](7) == exposure
    assert len(calls) == 3 and all(timeout == 90 for _, _, timeout in calls)


def test_fresh_exposure_incomplete_read_from_run_36533147639_is_retried():
    exposure = {"money_state_generation": 9, "orders": [], "positions": []}
    ns, _ = _maintenance([http.client.IncompleteRead(b"x" * 209304, 207493), exposure])
    assert ns["fresh_exposure"]() == exposure


@pytest.mark.parametrize("code", [400, 401, 403, 409])
def test_fresh_exposure_does_not_mask_non_transient_errors(code):
    ns, _ = _maintenance([_http_error(code)])
    with pytest.raises(urllib.error.HTTPError):
        ns["fresh_exposure"]()


def test_fresh_exposure_budget_is_bounded():
    ns, calls = _maintenance([TimeoutError()] * 20)
    with pytest.raises(SystemExit, match="generation-matching relay authority unavailable"):
        ns["fresh_exposure"]()
    assert len(calls) == 20


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES, ids=lambda e: type(e).__name__ + str(getattr(e, "code", "")))
def test_pr164_cancel_transport_failure_is_unconfirmed_not_fatal(failure):
    ns, calls = _maintenance([failure])
    assert ns["mutate_json"]("/api/orders/cancel", {"trade_id": "fat-3430a1bbf138"}) is None
    assert calls == [("/api/orders/cancel", {"trade_id": "fat-3430a1bbf138"}, 90)]


def test_pr171_already_absent_404_reaches_the_idempotent_caller():
    ns, _ = _maintenance([_http_error(404)])
    with pytest.raises(urllib.error.HTTPError) as caught:
        ns["mutate_json"]("/api/orders/cancel", {"trade_id": "fat-45524bb34e14"})
    assert caught.value.code == 404
    step = WORKFLOW[WORKFLOW.index("- name: Enter durable authenticated paper maintenance boundary"):]
    handler = step[step.index('cancelled = mutate_json("/api/orders/cancel"'):]
    assert handler.index("if exc.code == 404") < handler.index("already absent") < handler.index("continue")


def test_maintenance_round_budget_absorbs_unconfirmed_rounds():
    step = _step_python("Enter durable authenticated paper maintenance boundary")
    assert "for round_no in range(1, 13):" in step
    assert "for attempt in range(1, 9):" in step


def _postdeploy_mutation(responses):
    def request_json(path, payload=None, timeout=30):
        value = responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    namespace = {"http": http, "json": json, "urllib": urllib, "request_json": request_json}
    _functions(_step_python("Re-enter maintenance and flatten the exact deployed revision"),
               {"postdeploy_mutation"}, namespace)
    return namespace["postdeploy_mutation"]


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES + [_http_error(404)],
                         ids=lambda e: type(e).__name__ + str(getattr(e, "code", "")))
def test_postdeploy_mutation_unconfirmed_or_absent_resamples(failure):
    assert _postdeploy_mutation([failure])("/api/orders/cancel", "t-1") is None


def test_postdeploy_mutation_raises_on_rejected_request():
    with pytest.raises(urllib.error.HTTPError):
        _postdeploy_mutation([_http_error(400)])("/api/positions/close", "t-1")


def test_postdeploy_unconfirmed_round_resamples_before_close_or_final_proof():
    step = _step_python("Re-enter maintenance and flatten the exact deployed revision")
    loop = step[step.index("round_unconfirmed = False"):]
    first = loop.index("if round_unconfirmed:")
    assert first < loop.index("for trade_id in positions:")
    second = loop.index("if round_unconfirmed:", first + 1)
    assert second < loop.index("final = fresh_state(required_generation)")


def _step_block(name):
    start = WORKFLOW.index(f"      - name: {name}")
    end = WORKFLOW.find("\n      - name: ", start + 10)
    return WORKFLOW[start:] if end == -1 else WORKFLOW[start:end]


def test_guaranteed_resume_is_the_final_step_on_every_failure_exit():
    block = _step_block("Guaranteed paper resume after failed guarded deploy")
    assert WORKFLOW.rstrip().endswith("run: python scripts/fly_failure_paper_resume.py")
    assert "(failure() || cancelled())" in block
    assert "inputs.keep_maintenance_pause == true" in block
    assert "steps.paper_maintenance.outcome == 'failure'" in block
    assert "steps.deploy_source.outcome == 'success'" in block
    assert "PRIOR_OPERATOR_STATE: ${{ steps.paper_maintenance.outputs.prior_operator_state }}" in block
    assert "timeout-minutes: 15" in block
    assert "continue-on-error" not in block
    assert WORKFLOW.index("Best-effort preserve safe paper maintenance after failed guarded deploy") < WORKFLOW.index(
        "Guaranteed paper resume after failed guarded deploy")


