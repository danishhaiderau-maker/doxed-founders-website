"""Canonical bytes and commutative-member ordering.

Ordered pairs keep direction. Only commutative conjunction members are sorted.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from research.causal_grammar_v1.contracts_v1 import PAYLOAD_FIELDS, ContractError


def canonical_bytes(obj: Any) -> bytes:
    try:
        text = json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ContractError("CANONICAL_REJECT") from None
    return text.encode("utf-8")


def canonical_text(obj: Any) -> str:
    return canonical_bytes(obj).decode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def member_core(member: dict[str, Any]) -> dict[str, Any]:
    return {key: member[key] for key in PAYLOAD_FIELDS}


def canonicalize_members(kind: str, members: list[str]) -> list[str]:
    if kind == "ordered_pair":
        return [members[0], members[1]]
    return sorted(members)


def single_id(member: str) -> str:
    return f"S:{member}"


def unordered_id(members: list[str]) -> str:
    return "U:" + "+".join(members)


def ordered_id(antecedent: str, consequent: str) -> str:
    return f"O:{antecedent}>{consequent}"
