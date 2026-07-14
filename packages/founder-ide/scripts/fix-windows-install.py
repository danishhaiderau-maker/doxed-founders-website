# -*- coding: utf-8 -*-
"""Fix Founder IDE install: checksums, force English Skycode UI, wire Founder Node into Skycode."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INSTALL = r"C:\Users\user\AppData\Local\FounderIDE"
APP = os.path.join(INSTALL, "resources", "app")
PRODUCT = os.path.join(APP, "product.json")
WORKBENCH_JS = os.path.join(APP, "out", "vs", "workbench", "workbench.desktop.main.js")
SKY_INDEX = os.path.join(APP, "extensions", "skycode", "webview-ui", "build", "assets", "index.js")
SKY_EXT = os.path.join(APP, "extensions", "skycode", "dist", "extension.js")
RU_PACK = os.path.join(APP, "extensions", "vscode-language-pack-ru")
UPSTREAM_PRODUCT = r"C:\Users\user\Desktop\Final Bots\doxedcryptofounder\packages\founder-ide\upstream\VSCode-win32-x64\resources\app\product.json"
UPSTREAM_SKY_INDEX = r"C:\Users\user\Desktop\Final Bots\doxedcryptofounder\packages\founder-ide\upstream\VSCode-win32-x64\resources\app\extensions\skycode\webview-ui\build\assets\index.js"

ROAMING = os.path.expandvars(r"%APPDATA%\Founder IDE")
DOT = os.path.expanduser(r"~\.founder-ide")
VAULT = os.path.expanduser(r"~\FounderVault\node-config.json")

BACKUP_DIR = os.path.join(os.path.expanduser("~"), "FounderVault", "ide-fix-backups", datetime.now().strftime("%Y%m%d-%H%M%S"))
os.makedirs(BACKUP_DIR, exist_ok=True)


def backup(path: str) -> None:
    if not os.path.exists(path):
        return
    rel = re.sub(r"[^A-Za-z0-9._-]+", "_", path)
    if len(rel) > 180:
        rel = rel[-180:]
    dest = os.path.join(BACKUP_DIR, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isdir(path):
        if not os.path.exists(dest):
            shutil.copytree(path, dest)
    else:
        shutil.copy2(path, dest)
    print(f"backed up {path} -> {dest}")


def sha256_b64_nopad(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.digest().hex() and h.digest()  # noqa - keep typing happy


def file_checksum_vscode(path: str) -> str:
    import base64

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode("ascii").rstrip("=")


def fix_checksums() -> None:
    print("\n=== A: Fix product.json checksums ===")
    backup(PRODUCT)
    with open(PRODUCT, encoding="utf-8") as f:
        product = json.load(f)
    checksums = product.get("checksums") or {}
    out_base = os.path.join(APP, "out")
    updated = []
    for rel, expected in list(checksums.items()):
        path = os.path.join(out_base, *rel.split("/"))
        if not os.path.exists(path):
            print(f"MISSING {path}")
            continue
        actual = file_checksum_vscode(path)
        if actual != expected:
            print(f"MISMATCH {rel}\n  old={expected}\n  new={actual}")
            checksums[rel] = actual
            updated.append(rel)
        else:
            print(f"OK {rel}")
    product["checksums"] = checksums
    with open(PRODUCT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(product, f, indent="\t", ensure_ascii=False)
        f.write("\n")
    print(f"updated {len(updated)} checksum(s)")

    # Also clear checksums as belt-and-suspenders for future rebrand patches?
    # Keep updated checksums — cleaner than empty.

    # Mirror to upstream checkout if present
    if os.path.exists(UPSTREAM_PRODUCT) and os.path.exists(
        os.path.join(
            r"C:\Users\user\Desktop\Final Bots\doxedcryptofounder\packages\founder-ide\upstream\VSCode-win32-x64\resources\app\out",
            "vs",
            "workbench",
            "workbench.desktop.main.js",
        )
    ):
        backup(UPSTREAM_PRODUCT)
        with open(UPSTREAM_PRODUCT, encoding="utf-8") as f:
            up = json.load(f)
        up_base = os.path.join(
            r"C:\Users\user\Desktop\Final Bots\doxedcryptofounder\packages\founder-ide\upstream\VSCode-win32-x64\resources\app\out"
        )
        up_cs = up.get("checksums") or {}
        for rel in list(up_cs.keys()):
            path = os.path.join(up_base, *rel.split("/"))
            if os.path.exists(path):
                up_cs[rel] = file_checksum_vscode(path)
        up["checksums"] = up_cs
        with open(UPSTREAM_PRODUCT, "w", encoding="utf-8", newline="\n") as f:
            json.dump(up, f, indent="\t", ensure_ascii=False)
            f.write("\n")
        print("mirrored checksums to upstream VSCode-win32-x64 product.json")


def force_skycode_english(path: str) -> bool:
    """Patch Skycode webview so interface language defaults to English, not Russian."""
    if not os.path.exists(path):
        print(f"skip missing {path}")
        return False
    backup(path)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        data = f.read()
    original = data

    # Critical default: localStorage skycode.interfaceLanguage === "en" ? en : ru
    # Flip to prefer en unless explicitly "ru"
    patterns = [
        (
            'localStorage.getItem("skycode.interfaceLanguage")==="en"?"en":"ru"',
            'localStorage.getItem("skycode.interfaceLanguage")==="ru"?"ru":"en"',
        ),
        (
            "localStorage.getItem('skycode.interfaceLanguage')==='en'?'en':'ru'",
            "localStorage.getItem('skycode.interfaceLanguage')==='ru'?'ru':'en'",
        ),
        # dictation default language ru -> en
        (
            'dictationLanguage:"ru"',
            'dictationLanguage:"en"',
        ),
        (
            "dictationLanguage:'ru'",
            "dictationLanguage:'en'",
        ),
    ]
    changed = 0
    for old, new in patterns:
        count = data.count(old)
        if count:
            data = data.replace(old, new)
            changed += count
            print(f"  replaced {count}x: {old[:60]}...")

    if data == original:
        # Try broader regex for the interface language ternary
        new_data, n = re.subn(
            r'localStorage\.getItem\(["\']skycode\.interfaceLanguage["\']\)===["\']en["\']\?["\']en["\']:["\']ru["\']',
            'localStorage.getItem("skycode.interfaceLanguage")==="ru"?"ru":"en"',
            data,
        )
        if n:
            data = new_data
            changed += n
            print(f"  regex replaced interfaceLanguage default {n}x")

    if data != original:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
        print(f"patched {path} ({changed} replacements)")
        return True
    print(f"no language default patterns found in {path}")
    return False


def remove_russian_pack() -> None:
    print("\n=== B/D: Remove Russian language pack ===")
    if os.path.exists(RU_PACK):
        backup(RU_PACK)
        shutil.rmtree(RU_PACK)
        print(f"removed {RU_PACK}")
    else:
        print("RU pack already absent")

    for lp in [
        os.path.join(ROAMING, "languagepacks.json"),
        os.path.join(DOT, "languagepacks.json"),
    ]:
        backup(lp)
        with open(lp, "w", encoding="utf-8") as f:
            f.write("{}\n")
        print(f"cleared {lp}")

    # Ensure locale files
    for base in [ROAMING, DOT]:
        user = os.path.join(base, "User")
        os.makedirs(user, exist_ok=True)
        locale = os.path.join(user, "locale.json")
        with open(locale, "w", encoding="utf-8") as f:
            f.write('{ "locale": "en" }\n')
        argv = os.path.join(base, "argv.json")
        if os.path.exists(argv):
            backup(argv)
            try:
                text = open(argv, encoding="utf-8").read()
                # strip comments for parse attempt
                cleaned = re.sub(r"//.*?$", "", text, flags=re.M)
                cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.S)
                data = json.loads(cleaned)
            except Exception:
                data = {"enable-crash-reporter": False}
            data["locale"] = "en"
            with open(argv, "w", encoding="utf-8") as f:
                json.dump(data, f, indent="\t")
                f.write("\n")
            print(f"set locale=en in {argv}")


def read_vault():
    if not os.path.exists(VAULT):
        return None
    return json.load(open(VAULT, encoding="utf-8"))


def wire_skycode_auth() -> None:
    print("\n=== C: Wire Founder Node into Skycode + settings ===")
    vault = read_vault()
    if not vault:
        print("No vault config — skip auth wiring")
        return

    api_base = (vault.get("apiBaseUrl") or "https://api.doxxedcrypto.digital").rstrip("/")
    # Prefer API host for OpenAI-compat
    if "api." not in api_base and "doxxedcrypto.digital" in api_base:
        openai_base = "https://api.doxxedcrypto.digital/v1"
        gateway = "https://api.doxxedcrypto.digital"
    else:
        gateway = api_base
        openai_base = f"{api_base}/v1" if not api_base.endswith("/v1") else api_base

    node_id = vault["nodeId"]
    node_token = vault["nodeToken"]
    bearer = f"fos_{node_id}:{node_token}"

    # Update user settings.json in both user-data dirs
    for settings_path in [
        os.path.join(ROAMING, "User", "settings.json"),
        os.path.join(DOT, "User", "settings.json"),
    ]:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        backup(settings_path)
        settings = {}
        if os.path.exists(settings_path):
            raw = open(settings_path, encoding="utf-8-sig").read()
            try:
                settings = json.loads(raw)
            except Exception:
                settings = {}
        settings.update(
            {
                "locale": "en",
                "founderOs.apiBaseUrl": gateway,
                "founderOs.nodeId": node_id,
                "founderOs.nodeToken": node_token,
                "openAICompatible.apiUrl": openai_base,
                "openAICompatible.apiKey": bearer,
                # Skycode OpenAI-compatible provider (settings may or may not be read; state DB is primary)
                "skycode.preferredLanguage": "English",
            }
        )
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
            f.write("\n")
        print(f"updated settings {settings_path}")

    # Update skycode global state in state.vscdb
    for db_path in [
        os.path.join(ROAMING, "User", "globalStorage", "state.vscdb"),
        os.path.join(DOT, "User", "globalStorage", "state.vscdb"),
    ]:
        if not os.path.exists(db_path):
            print(f"missing db {db_path}")
            continue
        backup(db_path)
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("SELECT value FROM ItemTable WHERE key=?", ("skycode.skycode",))
        row = cur.fetchone()
        state = {}
        if row:
            try:
                state = json.loads(row[0])
            except Exception:
                state = {}
        state.update(
            {
                "welcomeViewCompleted": True,  # skip first-run cloud gate friction when possible
                "openAiBaseUrl": openai_base,
                "openAiHeaders": {"Authorization": f"Bearer {bearer}"},
                "planModeApiProvider": "openai",
                "actModeApiProvider": "openai",
                "planModeOpenAiModelId": state.get("planModeOpenAiModelId") or "founder-os-auto",
                "actModeOpenAiModelId": state.get("actModeOpenAiModelId") or "founder-os-auto",
                "preferredLanguage": "English",
                "skycode.interfaceLanguage": "en",
            }
        )
        # Also store API key if skycode uses openAiApiKey field
        state["openAiApiKey"] = bearer
        payload = json.dumps(state, ensure_ascii=False)
        cur.execute(
            "INSERT INTO ItemTable(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("skycode.skycode", payload),
        )
        con.commit()
        con.close()
        print(f"updated skycode state in {db_path}")
        print(f"  openAiBaseUrl={openai_base}")
        print(f"  nodeId={node_id}")


def patch_skycode_default_language_in_extension() -> None:
    """Also patch extension.js if it contains the same default."""
    force_skycode_english(SKY_EXT)


def main():
    print("BACKUP_DIR", BACKUP_DIR)
    fix_checksums()
    print("\n=== B: Force Skycode English default ===")
    force_skycode_english(SKY_INDEX)
    # upstream copy if present
    if os.path.exists(UPSTREAM_SKY_INDEX):
        force_skycode_english(UPSTREAM_SKY_INDEX)
    patch_skycode_default_language_in_extension()
    remove_russian_pack()
    wire_skycode_auth()
    print("\nDONE")


if __name__ == "__main__":
    main()
