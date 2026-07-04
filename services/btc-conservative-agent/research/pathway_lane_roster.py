"""Shim — load agent-root pathway_lane_roster (single source of truth).

research/ used to ship a stale copy that shadowed V2 / AI60 when research/ was
first on sys.path. Do not maintain a second roster here.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TARGET = os.path.join(_AGENT_ROOT, "pathway_lane_roster.py")

if _AGENT_ROOT not in sys.path:
    sys.path.insert(0, _AGENT_ROOT)

_spec = importlib.util.spec_from_file_location("_agent_pathway_lane_roster", _TARGET)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load agent pathway_lane_roster: {_TARGET}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

for _name in dir(_mod):
    if _name.startswith("_"):
        continue
    globals()[_name] = getattr(_mod, _name)
