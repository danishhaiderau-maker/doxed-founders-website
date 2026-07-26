#!/usr/bin/env python3
"""Synchronize Founder-owned packaged workbench checksums in product.json."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path


WORKBENCH_CHECKSUM_KEY = "vs/workbench/workbench.desktop.main.js"
WORKBENCH_CSS_CHECKSUM_KEY = "vs/workbench/workbench.desktop.main.css"


def sha256_base64(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbench", required=True, type=Path)
    parser.add_argument("--workbench-css", required=True, type=Path)
    parser.add_argument("--product", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if not args.workbench.is_file():
        raise SystemExit(f"Workbench bundle not found: {args.workbench}")
    if not args.workbench_css.is_file():
        raise SystemExit(f"Workbench stylesheet not found: {args.workbench_css}")
    if not args.product.is_file():
        raise SystemExit(f"Product manifest not found: {args.product}")

    product = json.loads(args.product.read_text(encoding="utf-8"))
    checksums = product.get("checksums")
    if not isinstance(checksums, dict):
        raise SystemExit("Product manifest does not contain a checksums object")
    founder_artifacts = {
        WORKBENCH_CHECKSUM_KEY: args.workbench,
        WORKBENCH_CSS_CHECKSUM_KEY: args.workbench_css,
    }
    missing_keys = [key for key in founder_artifacts if key not in checksums]
    if missing_keys:
        raise SystemExit(
            "Product manifest does not own required checksum keys: "
            + ", ".join(missing_keys)
        )

    for key, artifact in founder_artifacts.items():
        checksums[key] = sha256_base64(artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(product, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "Founder IDE integrity manifest synchronized: "
        + ", ".join(founder_artifacts)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
