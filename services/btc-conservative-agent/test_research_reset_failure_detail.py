import ast
from pathlib import Path

import pytest

from research_exact_deletion import ResearchDeletionRejected
from research_reset_failure_detail import REJECTION_CODES, reset_failure_fields


@pytest.mark.parametrize("code", sorted(REJECTION_CODES))
def test_exact_static_rejection_is_retained(code):
    assert reset_failure_fields(ResearchDeletionRejected(code)) == {
        "error": "ResearchDeletionRejected", "rejection_code": code}


@pytest.mark.parametrize("text", ["token=secret", "/app/data/private.json",
    "PROTECTED_PATH:/secret", "PROTECTED_PATH\nprivate", "unknown", ""])
def test_unrecognized_exception_text_is_never_published(text):
    result = reset_failure_fields(ResearchDeletionRejected(text))
    assert result["rejection_code"] == "UNCLASSIFIED_RESEARCH_DELETION_REJECTION"
    assert text not in result.values()


def test_other_exception_preserves_existing_class_only():
    assert reset_failure_fields(OSError("sensitive path")) == {"error": "OSError"}


def test_allowlist_covers_actual_static_rejections_without_runtime_imports():
    observed = set()
    for filename in ("research_exact_deletion.py", "research_reset_execution.py"):
        tree = ast.parse(Path(__file__).with_name(filename).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "ResearchDeletionRejected" and len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                observed.add(node.args[0].value)
    assert observed <= REJECTION_CODES
