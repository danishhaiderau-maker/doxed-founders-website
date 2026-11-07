"""Static isolation checks. This module does not open a socket."""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_IMPORT_ROOTS = {
    "socket",
    "websocket",
    "websockets",
    "requests",
    "urllib",
    "http",
    "aiohttp",
    "ssl",
    "subprocess",
    "dotenv",
    "bot",
    "paper_fill",
    "local_public_ws_collector",
    "local_market_persist",
    "microstructure_tape",
    "tape_archive",
    "tempfile",
    "asyncio",
    "_socket",
    "importlib",
}

FORBIDDEN_CALLS = {
    "getenv",
    "unlink",
    "remove",
    "rmdir",
    "rename",
    "replace",
    "truncate",
    "system",
    "popen",
    "__import__",
}

FORBIDDEN_ATTRS = {"environ"}


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def scan_package(package: Path | None = None) -> list[str]:
    root = package or _package_dir()
    findings = []
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        authenticated = "api" + ".bitfinex.com"
        public = "api-pub" + ".bitfinex.com"
        if authenticated in source and path.name != "bitfinex_docs.py":
            findings.append(f"{path.name}: authenticated host outside docs")
        if public in source and path.name != "bitfinex_docs.py":
            findings.append(f"{path.name}: public url outside docs")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    base = alias.name.split(".", 1)[0]
                    if base in FORBIDDEN_IMPORT_ROOTS:
                        findings.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                base = node.module.split(".", 1)[0]
                if base in FORBIDDEN_IMPORT_ROOTS:
                    findings.append(f"{path.name}: from {node.module}")
            elif isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in FORBIDDEN_CALLS:
                    findings.append(f"{path.name}: call {name}")
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
                findings.append(f"{path.name}: attr {node.attr}")
    return findings
