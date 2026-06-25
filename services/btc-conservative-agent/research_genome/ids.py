"""Immutable UUID generators for the genome ID chain."""
from __future__ import annotations

import uuid


def new_id(prefix: str = "") -> str:
    raw = str(uuid.uuid4())
    if prefix:
        return f"{prefix}_{raw}"
    return raw


def new_environment_id() -> str:
    return new_id("env")


def new_market_genome_id() -> str:
    return new_id("mkt")


def new_decision_id() -> str:
    return new_id("dec")


def new_execution_id() -> str:
    return new_id("exe")


def new_trade_id() -> str:
    return new_id("trd")


def new_outcome_id() -> str:
    return new_id("out")
