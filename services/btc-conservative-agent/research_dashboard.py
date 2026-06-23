#!/usr/bin/env python3
"""
CLI shim + import proxy — canonical dashboard lives in research/research_dashboard.py.

Agent root must not host a full duplicate (wrong ROOT → empty manifest on :9500).
"""
from __future__ import annotations

import importlib.util
import os
import runpy
import sys

_AGENT_ROOT = os.path.dirname(os.path.abspath(__file__))
_RESEARCH_DIR = os.path.join(_AGENT_ROOT, "research")
_CANONICAL = os.path.join(_RESEARCH_DIR, "research_dashboard.py")


def _exec_canonical_as_module() -> None:
    if _AGENT_ROOT not in sys.path:
        sys.path.insert(0, _AGENT_ROOT)
    spec = importlib.util.spec_from_file_location(__name__, _CANONICAL)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load canonical dashboard: {_CANONICAL}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[__name__] = mod
    spec.loader.exec_module(mod)


if __name__ == "__main__":
    if not os.path.isfile(_CANONICAL):
        raise SystemExit(f"Missing canonical dashboard: {_CANONICAL}")
    os.chdir(_RESEARCH_DIR)
    if _RESEARCH_DIR not in sys.path:
        sys.path.insert(0, _RESEARCH_DIR)
    runpy.run_path(_CANONICAL, run_name="__main__")
else:
    _exec_canonical_as_module()
