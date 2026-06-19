"""Shared BTC signal engine contract — imported by research and showcase runtimes."""
from __future__ import annotations

import json
import os
from pathlib import Path

_MANIFEST_PATH = Path(__file__).with_name("manifest.json")


def load_manifest() -> dict:
    if _MANIFEST_PATH.is_file():
        with _MANIFEST_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def engine_version() -> str:
    return str(load_manifest().get("engine_version") or "unknown")
