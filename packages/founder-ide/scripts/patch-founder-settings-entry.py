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
AUXILIARY_BUNDLE_KEYS = (
    "vs/code/node/cliProcessMain.js",
    "vs/code/electron-utility/sharedProcess/sharedProcessMain.js",
)


def checksum(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


def patch(app: Path) -> None:
    workbench = app / "out" / "vs" / "workbench" / "workbench.desktop.main.js"
    messages = app / "out" / "nls.messages.json"
    product = app / "product.json"
    if not workbench.is_file() or not messages.is_file() or not product.is_file():
        raise SystemExit(f"Founder IDE app files were not found below {app}")

    data = workbench.read_text(encoding="utf-8")
    # Release builds may be minified (`id:"..."`) or readable
    # (`id: "..."`). Match both so the post-build hardening step does not
    # accidentally require the unsupported mangled target.
    marker = re.compile(r'id:\s*["\']void\.settingsAction["\']')
    marker_matches = list(marker.finditer(data))
    if len(marker_matches) != 1:
        raise SystemExit(
            f"Expected one inherited settings action, found {len(marker_matches)}"
        )

    start = marker_matches[0].start()
    end = min(len(data), start + 2_000)
    action = data[start:end]
    redirected = 'executeCommand("founderOs.openSettings","ai")'
    legacy_redirected = 'executeCommand("founderOs.openSettings")'
    if redirected in action:
        rewritten = action
    elif legacy_redirected in action:
        rewritten = action.replace(legacy_redirected, redirected, 1)
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
    data, legacy_redirects = re.subn(
        r'executeCommand\(["\']founderOs\.openSettings["\']\)',
        redirected,
        data,
    )

    replacements = {
        "Welcome to Void": "Welcome to Founder IDE",
        "Open Void Settings": "Open Personal AI",
        "Void Settings": "Personal AI",
        "Void Side Bar": "Founder AI",
        "Void Version": "Founder IDE Version",
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
        "before using Vertex with Void": "before using Vertex in Founder IDE",
        "Create a .voidrules file for me": "Create project instructions for me",
    }
    for old, new in replacements.items():
        data = data.replace(old, new)

    message_data = messages.read_text(encoding="utf-8")
    for old, new in replacements.items():
        message_data = message_data.replace(old, new)

    auxiliary_bundles: list[tuple[str, Path, str]] = []
    for relative_key in AUXILIARY_BUNDLE_KEYS:
        bundle = app / "out" / Path(relative_key)
        if not bundle.is_file():
            continue
        bundle_data = bundle.read_text(encoding="utf-8")
        for old, new in replacements.items():
            bundle_data = bundle_data.replace(old, new)
        auxiliary_bundles.append((relative_key, bundle, bundle_data))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path.home() / "FounderVault" / "ide-settings-backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(workbench, backup_dir / workbench.name)
    shutil.copy2(messages, backup_dir / messages.name)
    shutil.copy2(product, backup_dir / product.name)
    for _, bundle, _ in auxiliary_bundles:
        shutil.copy2(bundle, backup_dir / bundle.name)

    workbench.write_text(data, encoding="utf-8", newline="")
    messages.write_text(message_data, encoding="utf-8", newline="")
    for _, bundle, bundle_data in auxiliary_bundles:
        bundle.write_text(bundle_data, encoding="utf-8", newline="")
    # utf-8-sig accepts an accidental BOM left by an older PowerShell
    # checksum rewrite; write_text below always normalizes back to UTF-8.
    manifest = json.loads(product.read_text(encoding="utf-8-sig"))
    checksums = manifest.setdefault("checksums", {})
    checksums[WORKBENCH_KEY] = checksum(workbench)
    for relative_key, bundle, _ in auxiliary_bundles:
        if relative_key in checksums:
            checksums[relative_key] = checksum(bundle)
    product.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    verify = workbench.read_text(encoding="utf-8")
    if redirected not in verify:
        raise SystemExit("Founder Settings redirect did not verify")
    if re.search(r'executeCommand\(["\']founderOs\.openSettings["\']\)', verify):
        raise SystemExit("A personal AI settings action still opens the default tab")
    visible_void_phrases = ("Void's Settings", "Vertex with Void")
    if any(phrase in verify for phrase in visible_void_phrases):
        raise SystemExit("A user-visible Void label remains")
    if "Create a .voidrules file for me" in verify:
        raise SystemExit("The inherited .voidrules chat suggestion remains")
    if any(phrase in messages.read_text(encoding="utf-8") for phrase in visible_void_phrases):
        raise SystemExit("A localized Void Settings label remains")
    for _, bundle, _ in auxiliary_bundles:
        if any(phrase in bundle.read_text(encoding="utf-8") for phrase in visible_void_phrases):
            raise SystemExit(f"A user-visible Void Settings label remains in {bundle}")
    print(
        "Founder Settings redirect installed "
        f"({legacy_redirects} legacy entry points); backup: {backup_dir}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    args = parser.parse_args()
    patch(args.app.resolve())


if __name__ == "__main__":
    main()
