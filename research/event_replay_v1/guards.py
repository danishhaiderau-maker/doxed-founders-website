"""Resource ceilings, output ledger, and practical containment.

The tree bound is two processes, each with a 256MiB virtual-address ceiling.
Resident size is at most virtual size after the ceiling is installed, so the
sum is a conservative upper bound on the original 512MiB RSS figure. It is
not a rename of that metric. There is no CPU-seconds cap. Wall deadlines are
120 seconds per command and 600 seconds per suite. Receipt files are 8MiB.
Aggregate command output is a separate 16MiB ledger.
"""

from __future__ import annotations

import os
import resource
import socket
import time
from dataclasses import dataclass

VAS_BYTES = 268435456
MAX_WORKERS = 2
MAX_RSS_BOUND_BYTES = 512 * 1024 * 1024
MAX_COMMAND_SECONDS = 120
MAX_SUITE_SECONDS = 600
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_AGGREGATE_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 2048
SECRET_KEY_MARKS = ("secret", "token", "password", "credential", "canary", "payload", "api_key")


class GuardError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CapStop(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(f"CAP_STOP:{code}")
        self.code = code


@dataclass
class ResourceLimits:
    max_workers: int = MAX_WORKERS
    max_rss_bytes: int = MAX_RSS_BOUND_BYTES
    max_command_seconds: int = MAX_COMMAND_SECONDS
    max_suite_seconds: int = MAX_SUITE_SECONDS
    max_receipt_bytes: int = MAX_RECEIPT_BYTES
    max_aggregate_bytes: int = MAX_AGGREGATE_BYTES
    max_events: int = MAX_EVENTS
    vas_bytes: int = VAS_BYTES


DEFAULT_LIMITS = ResourceLimits()


@dataclass
class ResourceObservation:
    workers: int = 1
    rss_bytes: int = 0
    command_seconds: float = 0
    suite_seconds: float = 0
    receipt_bytes: int = 0
    aggregate_bytes: int = 0
    events: int = 0
    vas_bytes: int = 0


def enforce_observation(obs: ResourceObservation, limits: ResourceLimits = DEFAULT_LIMITS) -> bool:
    if obs.workers > limits.max_workers:
        raise CapStop("workers")
    if obs.rss_bytes > limits.max_rss_bytes:
        raise CapStop("rss")
    if obs.command_seconds > limits.max_command_seconds:
        raise CapStop("command")
    if obs.suite_seconds > limits.max_suite_seconds:
        raise CapStop("suite")
    if obs.receipt_bytes > limits.max_receipt_bytes:
        raise CapStop("receipt")
    if obs.aggregate_bytes > limits.max_aggregate_bytes:
        raise CapStop("aggregate")
    if obs.events > limits.max_events:
        raise CapStop("events")
    if obs.vas_bytes > limits.vas_bytes:
        raise CapStop("vas")
    return True


def install_vas_ceiling(limit: int = VAS_BYTES) -> dict:
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    return {
        "metric": "RLIMIT_AS",
        "mechanism": "resource.setrlimit",
        "observed_soft": soft,
        "observed_hard": hard,
        "cap": limit,
    }


def observed_vas_ceiling() -> tuple[int, int]:
    return resource.getrlimit(resource.RLIMIT_AS)


def require_vas_ceiling(limit: int = VAS_BYTES) -> dict:
    soft, hard = observed_vas_ceiling()
    if soft < 0 or hard < 0 or soft > limit or hard > limit:
        raise CapStop("vas_ceiling_missing")
    return {
        "metric": "RLIMIT_AS",
        "mechanism": "inherited_or_installed_setrlimit",
        "observed_soft": soft,
        "observed_hard": hard,
        "cap": limit,
        "stopped": False,
    }


def sample_rss_bytes() -> int:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError as exc:
        raise CapStop("rss_enforcement_missing") from exc
    raise CapStop("rss_enforcement_missing")


class OutputLedger:
    """One authoritative byte ledger. Reservations never raise the caps."""

    def __init__(
        self,
        aggregate_cap: int = MAX_AGGREGATE_BYTES,
        receipt_cap: int = MAX_RECEIPT_BYTES,
    ) -> None:
        self.aggregate_cap = aggregate_cap
        self.receipt_cap = receipt_cap
        self.aggregate_used = 0
        self.receipt_used = 0
        self.transfers: list[dict] = []

    def remaining_aggregate(self) -> int:
        return self.aggregate_cap - self.aggregate_used

    def reserve_pipe(self, nbytes: int, kind: str) -> None:
        if nbytes < 0 or self.aggregate_used + nbytes > self.aggregate_cap:
            raise CapStop("aggregate")
        self.aggregate_used += nbytes
        self.transfers.append({"kind": kind, "bytes": nbytes, "aggregate_used": self.aggregate_used})

    def reserve_receipt(self, nbytes: int) -> None:
        if nbytes < 0 or self.receipt_used + nbytes > self.receipt_cap:
            raise CapStop("receipt")
        if self.aggregate_used + nbytes > self.aggregate_cap:
            raise CapStop("aggregate")
        self.receipt_used += nbytes
        self.aggregate_used += nbytes
        self.transfers.append(
            {
                "kind": "receipt",
                "bytes": nbytes,
                "receipt_used": self.receipt_used,
                "aggregate_used": self.aggregate_used,
            }
        )

    def refund(self, nbytes: int) -> None:
        if nbytes < 0 or nbytes > self.aggregate_used:
            raise CapStop("aggregate")
        self.aggregate_used -= nbytes
        self.transfers.append({"kind": "refund", "bytes": nbytes, "aggregate_used": self.aggregate_used})


class ProcessGate:
    """At most one live child. The parent plus that child is the tree of two."""

    def __init__(self) -> None:
        self.live = 0

    def acquire(self) -> dict:
        if self.live >= 1:
            raise CapStop("workers")
        self.live += 1
        return {
            "metric": "process_count",
            "mechanism": "process_gate",
            "observed": self.live + 1,
            "cap": MAX_WORKERS,
            "stopped": False,
        }

    def release(self) -> None:
        if self.live > 0:
            self.live -= 1


class _EnvironTrap(dict):
    def _refuse(self, *_args, **_kwargs):
        raise GuardError("PROHIBITED_ENV")

    def __getitem__(self, key):
        self._refuse()

    def __iter__(self):
        self._refuse()

    def __contains__(self, key):
        self._refuse()

    def get(self, key, default=None):
        self._refuse()

    keys = _refuse
    items = _refuse
    values = _refuse


def install_env_trap() -> None:
    os.environ = _EnvironTrap()

    def getenv(*_args, **_kwargs):
        raise GuardError("PROHIBITED_ENV")

    os.getenv = getenv


class OuterInterception:
    """Always-on practical containment. Inner mutants do not remove it."""

    def __init__(self) -> None:
        self.installed = False
        self.refusals: list[str] = []
        self._saved: dict = {}

    def install(self) -> None:
        if self.installed:
            return
        self._saved = {
            "socket": socket.socket,
            "create_connection": socket.create_connection,
            "getaddrinfo": socket.getaddrinfo,
        }

        def refuse(code):
            def _inner(*_args, **_kwargs):
                self.refusals.append(code)
                raise GuardError(code)

            return _inner

        socket.socket = refuse("PROHIBITED_NETWORK")
        socket.create_connection = refuse("PROHIBITED_NETWORK")
        socket.getaddrinfo = refuse("PROHIBITED_NETWORK")
        self.installed = True


class InnerGuard:
    """Production checks. A test may deactivate this object only."""

    def __init__(self) -> None:
        self.active = True

    def check(self, code: str) -> None:
        if self.active:
            raise GuardError(code)


INNER_GUARD = InnerGuard()


def structural_redact(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(mark in lowered for mark in SECRET_KEY_MARKS):
                continue
            cleaned[key] = structural_redact(item)
        return cleaned
    if isinstance(value, list):
        return [structural_redact(item) for item in value]
    if isinstance(value, str) and value.startswith("-----BEGIN "):
        return "[REDACTED]"
    return value


def monotonic_seconds(started: float) -> float:
    return time.monotonic() - started
