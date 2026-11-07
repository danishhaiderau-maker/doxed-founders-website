"""Runtime guard and resource caps.

Network, environment, vault, and real-root access fail before side effects.
Exception text is a static code and does not carry the injected payload.
Missing cap enforcement is a failed acceptance. A cap hit stops the run.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

MAX_WORKERS = 2
MAX_RSS_BYTES = 512 * 1024 * 1024
MAX_COMMAND_SECONDS = 120
MAX_SUITE_SECONDS = 600
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 2048


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
    max_rss_bytes: int = MAX_RSS_BYTES
    max_command_seconds: int = MAX_COMMAND_SECONDS
    max_suite_seconds: int = MAX_SUITE_SECONDS
    max_receipt_bytes: int = MAX_RECEIPT_BYTES
    max_events: int = MAX_EVENTS


DEFAULT_LIMITS = ResourceLimits()


@dataclass
class ResourceObservation:
    workers: int = 1
    rss_bytes: int = 0
    command_seconds: float = 0
    suite_seconds: float = 0
    receipt_bytes: int = 0
    events: int = 0


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
    if obs.events > limits.max_events:
        raise CapStop("events")
    return True


def sample_rss_bytes() -> int:
    """Read this process VmRSS. Absence of the probe blocks acceptance."""

    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError as exc:
        raise CapStop("rss_enforcement_missing") from exc
    raise CapStop("rss_enforcement_missing")


class RuntimeGuard:
    def __init__(self) -> None:
        self.active = False
        self._socket = None
        self._create_connection = None

    def activate(self) -> None:
        if self.active:
            return
        self._socket = socket.socket
        self._create_connection = socket.create_connection

        def blocked_socket(*_args, **_kwargs):
            raise GuardError("PROHIBITED_NETWORK")

        def blocked_connection(*_args, **_kwargs):
            raise GuardError("PROHIBITED_NETWORK")

        socket.socket = blocked_socket
        socket.create_connection = blocked_connection
        self.active = True

    def deactivate(self) -> None:
        if not self.active:
            return
        socket.socket = self._socket
        socket.create_connection = self._create_connection
        self.active = False

    def reject_network(self, _destination: str, payload: str | None = None) -> None:
        del payload
        raise GuardError("PROHIBITED_NETWORK")

    def reject_env(self, _key: str, payload: str | None = None) -> None:
        del payload
        raise GuardError("PROHIBITED_ENV")

    def reject_vault(self, _path: str, payload: str | None = None) -> None:
        del payload
        raise GuardError("PROHIBITED_VAULT")

    def reject_real_root(self, _path: str, payload: str | None = None) -> None:
        del payload
        raise GuardError("PROHIBITED_REAL_ROOT")


def monotonic_seconds(started: float) -> float:
    return time.monotonic() - started
