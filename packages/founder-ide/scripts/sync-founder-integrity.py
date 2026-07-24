#!/usr/bin/env python3
"""Synchronize the packaged workbench checksum in product.json."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path


WORKBENCH_CHECKSUM_KEY = "vs/workbench/workbench.desktop.main.js"


def sha256_base64(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbench", required=True, type=Path)
    parser.add_argument("--product", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if not args.workbench.is_file():
        raise SystemExit(f"Workbench bundle not found: {args.workbench}")
    if not args.product.is_file():
        raise SystemExit(f"Product manifest not found: {args.product}")

    product = json.loads(args.product.read_text(encoding="utf-8"))
    checksums = product.get("checksums")
    if not isinstance(checksums, dict):
        raise SystemExit("Product manifest does not contain a checksums object")
    if WORKBENCH_CHECKSUM_KEY not in checksums:
        raise SystemExit(
            f"Product manifest does not own {WORKBENCH_CHECKSUM_KEY}"
        )

    checksums[WORKBENCH_CHECKSUM_KEY] = sha256_base64(args.workbench)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(product, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "Founder IDE integrity manifest synchronized: "
        f"{WORKBENCH_CHECKSUM_KEY}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
