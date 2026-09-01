"""Regression checks for runtime source-revision reporting."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


def test_watchdog_and_dashboard_snapshot_need_no_revision_global():
    assert not hasattr(bot, "SOURCE_GIT_REV")
    watchdog = bot._watchdog_crash_context()
    snapshot = bot._build_api_state_snapshot()
    assert watchdog["source_revision"] in {"unknown", bot._runtime_git_rev_exact()}
    assert snapshot["git_rev"] in {"unknown", bot._runtime_git_rev()}


def run():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        nested = root / "services" / "agent"
        nested.mkdir(parents=True)
        git_dir = root / ".git"
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
        sha = "1234567890abcdef1234567890abcdef12345678"
        (git_dir / "refs" / "heads" / "master").write_text(f"{sha}\n", encoding="utf-8")
        assert bot._read_git_revision_without_cli(str(nested)) == sha

    bot._RUNTIME_GIT_REV_CACHE["value"] = None
    rev = bot._runtime_git_rev()
    assert rev != "unknown", "checkout revision must be available without relying on launcher PATH"
    assert len(rev) == 12, f"expected 12-char runtime revision, got {rev!r}"
    bot._RUNTIME_GIT_REV_EXACT_CACHE["value"] = ""
    exact = bot._runtime_git_rev_exact()
    assert len(exact) == 40, f"expected exact runtime revision, got {exact!r}"
    assert exact.startswith(rev)
    print(f"PASS: runtime source revision {rev}")


if __name__ == "__main__":
    run()
