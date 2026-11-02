"""Contract: Railway API offline-by-design after Fly app DELETED (HM condition).

Mutation proof: flip FLY_SHOWCASE_RUNTIME_RETIRED to false, or drop the
offline-by-design label / live_armed:false asserts in the API sources → fail.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps" / "api" / "src" / "trading-agents"
RUNTIME = API / "canonical-showcase-runtime.ts"
BRIDGE = API / "bot-bridge.service.ts"
REPORTER = API / "live-copy-fly-reporter.ts"
SPEC = API / "fly-showcase-offline-by-design.spec.ts"
ALLOWLIST = Path(__file__).resolve().parent / "ci" / "unit_test_allowlist.txt"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_fly_showcase_runtime_retired_constant_true():
    src = _read(RUNTIME)
    assert "export const FLY_SHOWCASE_RUNTIME_RETIRED = true;" in src
    assert "offline, by design — moving to laptop" in src
    assert "live_armed: false" in src
    assert "bitfinex_live_enabled: false" in src
    assert "retry: false" in src
    assert "error_loop: false" in src
    assert "shouldDispatchRelayExecutorWake" in src


def test_bot_bridge_refuses_retired_fly_without_cache_armed_live():
    src = _read(BRIDGE)
    assert "refuseRetiredFlyRuntime" in src
    assert "isFlyShowcaseRuntimeRetired" in src
    # Cache path must clear / return null when retired (no stale armed/live).
    assert "never serve a stale armed/live cache after Fly is gone" in src or (
        "Fail closed: never return a stale armed/live cache after Fly is gone" in src
    )


def test_live_copy_reporter_skips_five_second_loop_when_retired():
    src = _read(REPORTER)
    assert "shouldRegisterNeonBackgroundTimers()" in src
    assert "Fly reporter loop not started" in src


def test_neon_idle_gate_exported_and_used():
    runtime = _read(RUNTIME)
    assert "shouldRegisterNeonBackgroundTimers" in runtime
    assert "R51" in runtime or "registration-site gate" in runtime
    for path in (
        API / "trading-agents.service.ts",
        API / "signal-subscriber-execution.service.ts",
        API / "signal-cycles.service.ts",
    ):
        assert "shouldRegisterNeonBackgroundTimers" in _read(path), path.name


def test_offline_by_design_spec_exists_and_is_allowlisted_in_api_package():
    assert SPEC.is_file(), f"missing {SPEC}"
    spec = _read(SPEC)
    assert "offline-by-design status shape is stable and never armed/live" in spec
    assert "refuses retired Fly and clears armed/live cache path" in spec
    pkg = _read(ROOT / "apps" / "api" / "package.json")
    assert "fly-showcase-offline-by-design.spec.ts" in pkg


def test_this_file_is_on_ci_unit_test_allowlist():
    allow = _read(ALLOWLIST)
    assert "test_api_fly_offline_by_design.py" in allow
