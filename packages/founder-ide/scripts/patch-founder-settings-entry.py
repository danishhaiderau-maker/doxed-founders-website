"""Redirect inherited Void settings affordances to Founder Settings.

This is a post-build/install patch for the minified workbench bundle. It is
strict about the action signature so an upstream bundle change fails closed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


DEFAULT_APP = Path(r"C:\Users\user\AppData\Local\Programs\Founder IDE\resources\app")
WORKBENCH_KEY = "vs/workbench/workbench.desktop.main.js"


def checksum(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


def patch(app: Path) -> None:
    workbench = app / "out" / "vs" / "workbench" / "workbench.desktop.main.js"
    product = app / "product.json"
    if not workbench.is_file() or not product.is_file():
        raise SystemExit(f"Founder IDE app files were not found below {app}")

    data = workbench.read_text(encoding="utf-8")
    marker = 'id:"void.settingsAction"'
    if data.count(marker) != 1:
        raise SystemExit(f"Expected one inherited settings action, found {data.count(marker)}")

    start = data.index(marker)
    end = data.find("),X(", start)
    if end < 0 or end - start > 1_200:
        raise SystemExit("Could not isolate the inherited settings action")
    action = data[start:end]
    redirected = 'executeCommand("founderOs.openSettings")'
    if redirected in action:
        rewritten = action
    else:
        rewritten, count = re.subn(
            r"executeCommand\([A-Za-z_$][A-Za-z0-9_$]*\)",
            redirected,
            action,
            count=1,
        )
        if count != 1:
            raise SystemExit("Inherited settings handler signature changed; no patch applied")
    data = data[:start] + rewritten + data[end:]

    replacements = {
        "Void's Settings": "Founder Settings",
        "Void: ": "Founder: ",
        "Void Agent": "Founder Agent",
        "Please add a provider in Founder Settings.": "Connect an AI provider in Founder Settings.",
        "Void automatically detects": "Founder automatically detects",
        "Void can access": "Founder can access",
        "Void suggestions": "Founder suggestions",
        "into Void": "into Founder IDE",
        "Void's settings and chats": "Founder's settings and chats",
        "Void never sees": "Founder never sees",
        "Void will not include": "Founder will not include",
        "comes packaged with Void": "comes packaged with Founder IDE",
        "Void recognizes": "Founder recognizes",
        "Void metrics": "Founder metrics",
    }
    for old, new in replacements.items():
        data = data.replace(old, new)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path.home() / "FounderVault" / "ide-settings-backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(workbench, backup_dir / workbench.name)
    shutil.copy2(product, backup_dir / product.name)

    workbench.write_text(data, encoding="utf-8", newline="")
    manifest = json.loads(product.read_text(encoding="utf-8"))
    checksums = manifest.setdefault("checksums", {})
    checksums[WORKBENCH_KEY] = checksum(workbench)
    product.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    verify = workbench.read_text(encoding="utf-8")
    if 'executeCommand("founderOs.openSettings")' not in verify:
        raise SystemExit("Founder Settings redirect did not verify")
    if "Void's Settings" in verify:
        raise SystemExit("A user-visible Void Settings label remains")
    print(f"Founder Settings redirect installed; backup: {backup_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    args = parser.parse_args()
    patch(args.app.resolve())


if __name__ == "__main__":
    main()
