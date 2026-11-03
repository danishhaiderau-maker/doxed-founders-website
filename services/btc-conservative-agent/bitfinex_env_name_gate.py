"""Fail-closed gate: refuse BITFINEX_* KEY/SECRET/TOKEN names in process env.

RK 08:26 (#981 rev4): starters filter allowlist only (no pre-strip refuse).
Refusal lives inside launched processes (bot, analyzer incl. v62, supervisor
children). Names only — never print values (R10).

Exempt only BITFINEX_LIVE_ENABLED when its value is exactly ``'0'``.
Any other BITFINEX_LIVE_ENABLED value refuses. Other BITFINEX_* names are
refused when they contain KEY, SECRET, or TOKEN.
"""
from __future__ import annotations

import os
import sys
from typing import Mapping, Optional, Sequence

REFUSAL_EXIT = 79
COUNT_LOG_PREFIX = "BITFINEX_ENV_NAME_COUNT count="
_LIVE_ENABLED = "BITFINEX_LIVE_ENABLED"
_CREDENTIAL_TOKENS = ("KEY", "SECRET", "TOKEN")


def _upper_name(key: object) -> str:
    return str(key).upper()


def is_refused_bitfinex_env_name(name: object, value: object) -> bool:
    """True when this BITFINEX_* binding must refuse the process."""
    upper = _upper_name(name)
    if not upper.startswith("BITFINEX_"):
        return False
    if upper == _LIVE_ENABLED:
        # Exact string '0' only — not falsey-coercion of other values.
        return str(value) != "0"
    return any(token in upper for token in _CREDENTIAL_TOKENS)


def refused_bitfinex_env_names(
    environ: Optional[Mapping[str, str]] = None,
) -> list[str]:
    env = os.environ if environ is None else environ
    refused: list[str] = []
    for key, value in env.items():
        if is_refused_bitfinex_env_name(key, value):
            refused.append(str(key))
    return refused


def bitfinex_process_env_name_count(
    environ: Optional[Mapping[str, str]] = None,
) -> int:
    return len(refused_bitfinex_env_names(environ))


def assert_no_bitfinex_process_env_names(
    environ: Optional[Mapping[str, str]] = None,
    *,
    stream=None,
) -> int:
    """Log names-only refused count; SystemExit(79) if any refused name present."""
    count = bitfinex_process_env_name_count(environ)
    out = sys.stdout if stream is None else stream
    print(f"{COUNT_LOG_PREFIX}{count}", file=out, flush=True)
    if count > 0:
        print(
            f"REFUSED_BITFINEX_ENV_NAMES: count={count}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(REFUSAL_EXIT)
    return count


def live_enabled_exact_zero_is_exempt(
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    """Proof helper: BITFINEX_LIVE_ENABLED exactly '0' is not refused."""
    env = os.environ if environ is None else environ
    if _LIVE_ENABLED not in env and "bitfinex_live_enabled" not in {
        str(k) for k in env
    }:
        # Absent is fine for paper (bot treats missing as not requested) but
        # starters restore explicit =0; this helper is about the exemption.
        return True
    for key, value in env.items():
        if _upper_name(key) == _LIVE_ENABLED:
            return str(value) == "0" and not is_refused_bitfinex_env_name(key, value)
    return True
