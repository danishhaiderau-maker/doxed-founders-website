"""The committed btc-signal-engine mirror must equal a fresh regeneration.

The mirror script is run against a temporary copy of the canonical sources so
the checkout is never mutated; any drift means a bot-side change was merged
without `npm run mirror:signal-engine`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "services" / "btc-conservative-agent"
ENGINE = REPO / "services" / "btc-signal-engine"
MIRROR_SCRIPT = REPO / "scripts" / "mirror-signal-engine.mjs"
VOLATILE_MANIFEST_KEYS = {"updated_at", "combo_version"}


def _normalized(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def _engine_files(root: Path) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory) -> Path:
    if shutil.which("node") is None:
        pytest.skip("node is required to regenerate the signal-engine mirror")
    work = tmp_path_factory.mktemp("mirror")
    (work / "scripts").mkdir()
    shutil.copy2(MIRROR_SCRIPT, work / "scripts" / MIRROR_SCRIPT.name)
    agent_copy = work / "services" / "btc-conservative-agent"
    agent_copy.mkdir(parents=True)
    for source in AGENT.glob("*.py"):
        shutil.copy2(source, agent_copy / source.name)
    (agent_copy / "research").mkdir()
    shutil.copy2(
        AGENT / "research" / "mirror_generation_lease.py",
        agent_copy / "research" / "mirror_generation_lease.py",
    )
    shutil.copytree(
        ENGINE,
        work / "services" / "btc-signal-engine",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    subprocess.run(
        ["node", str(work / "scripts" / MIRROR_SCRIPT.name)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return work / "services" / "btc-signal-engine"


def test_regenerated_mirror_has_same_file_set(regenerated: Path) -> None:
    assert _engine_files(regenerated) == _engine_files(ENGINE)


def test_regenerated_mirror_matches_committed_bytes(regenerated: Path) -> None:
    drift = [
        name
        for name in sorted(_engine_files(regenerated))
        if name != "manifest.json"
        and _normalized(regenerated / name) != _normalized(ENGINE / name)
    ]
    assert drift == [], f"stale signal-engine mirror; run npm run mirror:signal-engine: {drift}"


def test_regenerated_manifest_identity_matches(regenerated: Path) -> None:
    fresh = json.loads((regenerated / "manifest.json").read_text(encoding="utf-8"))
    committed = json.loads((ENGINE / "manifest.json").read_text(encoding="utf-8"))
    for key in VOLATILE_MANIFEST_KEYS:
        fresh.pop(key, None)
        committed.pop(key, None)
    assert fresh == committed
