"""Fail-closed gate: process env must not contain BITFINEX_* names.

HM HOLD b5700e72694d / RK 05:12: names only — never print values.
Missing BITFINEX_LIVE_ENABLED is off (see bot._apply_env_live_gating /
local_fresh_runtime._truthy); do not set BITFINEX_* to force paper.
"""
from __future__ import annotations

import os
import sys
from typing import Mapping, Optional

REFUSAL_EXIT = 79
COUNT_LOG_PREFIX = "BITFINEX_ENV_NAME_COUNT count="


def bitfinex_process_env_name_count(
    environ: Optional[Mapping[str, str]] = None,
) -> int:
    env = os.environ if environ is None else environ
    return sum(1 for key in env if str(key).upper().startswith("BITFINEX_"))


def assert_no_bitfinex_process_env_names(
    environ: Optional[Mapping[str, str]] = None,
    *,
    stream=None,
) -> int:
    """Log names-only count; SystemExit(79) if any BITFINEX_* name is present."""
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


def missing_bitfinex_live_enabled_means_off(
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    """Proof helper: unset/empty/falsey BITFINEX_LIVE_ENABLED is not live."""
    env = os.environ if environ is None else environ
    raw = str(env.get("BITFINEX_LIVE_ENABLED") or "").strip().lower()
    return raw not in ("1", "true", "yes", "on")
