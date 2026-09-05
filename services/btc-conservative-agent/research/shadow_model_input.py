"""Pinned optional analysis assumptions, fenced across atomic publication.

This selects a research scenario only. It never enables trading, invents a
baseline execution context, or converts declared economics to observations.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from research.declared_shadow_model import SCHEMA, validate_contract
from research.policy_evidence_schema import canonical_json

PATH_ENV = "BTC_ANALYZER_SHADOW_MODEL_FILE"
HASH_ENV = "BTC_ANALYZER_SHADOW_MODEL_SHA256"
MAX_BYTES = 2 * 1024 * 1024


def _read(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("SHADOW_MODEL_INPUT_LINK_FORBIDDEN")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
        raise ValueError("SHADOW_MODEL_INPUT_SIZE_INVALID")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("SHADOW_MODEL_INPUT_SIZE_INVALID")
    return raw


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("SHADOW_MODEL_INPUT_DUPLICATE_KEY")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("SHADOW_MODEL_INPUT_NONFINITE")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError("SHADOW_MODEL_INPUT_OBJECT_REQUIRED")
    return value


@dataclass(frozen=True)
class ShadowModelInput:
    raw: bytes = b""
    path: Path | None = None

    @property
    def enabled(self):
        return bool(self.raw)

    def provenance(self):
        if not self.enabled:
            return None
        return {"schema": "shadow_model_input_v1",
                "mode": "PINNED_FILE" if self.path else "EXPLICIT_ARGUMENT",
                "sha256": hashlib.sha256(self.raw).hexdigest(),
                "evidence_basis": "DECLARED_SIMULATION"}

    def resolve(self, generation):
        if not self.enabled:
            return None
        value = _decode(self.raw)
        if value.get("schema") == SCHEMA:
            return validate_contract(value, generation)
        # Existing explicit conservative research models retain their own
        # schema, generation and signature validation in the report builder.
        if self.path:
            raise ValueError("SHADOW_MODEL_FILE_SCHEMA_UNSUPPORTED")
        return value

    def assert_unchanged(self):
        if self.path and _read(self.path) != self.raw:
            raise ValueError("SHADOW_MODEL_INPUT_CHANGED")


def load_shadow_model_input(explicit=None):
    path = os.environ.get(PATH_ENV, "")
    pin = os.environ.get(HASH_ENV, "")
    if explicit is not None:
        if path or pin:
            raise ValueError("SHADOW_MODEL_INPUT_AMBIGUOUS")
        raw = canonical_json(explicit).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError("SHADOW_MODEL_INPUT_SIZE_INVALID")
        _decode(raw)
        return ShadowModelInput(raw)
    if not path and not pin:
        return ShadowModelInput()
    if not path or not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise ValueError("SHADOW_MODEL_INPUT_PIN_REQUIRED")
    source = Path(path)
    if not source.is_absolute():
        raise ValueError("SHADOW_MODEL_INPUT_PATH_NOT_ABSOLUTE")
    raw = _read(source)
    if hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("SHADOW_MODEL_INPUT_HASH_MISMATCH")
    value = _decode(raw)
    if value.get("schema") != SCHEMA:
        raise ValueError("SHADOW_MODEL_FILE_SCHEMA_UNSUPPORTED")
    return ShadowModelInput(raw, source)


def assert_publication_shadow_model_input(manifest):
    source = load_shadow_model_input()
    recorded = (manifest.get("analysis_provenance") or {}).get("shadow_model_input")
    if recorded and recorded.get("mode") == "EXPLICIT_ARGUMENT" and not source.enabled:
        return  # The immutable in-memory input was captured by the publisher.
    if source.provenance() != recorded:
        raise ValueError("SHADOW_MODEL_PUBLICATION_INPUT_MISMATCH")
    source.assert_unchanged()
