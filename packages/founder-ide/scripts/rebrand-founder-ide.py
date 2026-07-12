# -*- coding: utf-8 -*-
"""
Deep Founder IDE rebrand: kill Skycode/OpenRouter/GitHub-first AI gate UX.
Patches installed FounderIDE + skycode-fork source locales/auth where present.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INSTALL = Path(r"C:\Users\user\AppData\Local\FounderIDE\resources\app")
FORK_SKY = Path(r"C:\Users\user\Desktop\skycode-fork\extensions\skycode")
UPSTREAM = Path(
    r"C:\Users\user\Desktop\Final Bots\doxedcryptofounder\packages\founder-ide\upstream\VSCode-win32-x64\resources\app"
)
ROAMING = Path(os.path.expandvars(r"%APPDATA%\Founder IDE"))
DOT = Path(os.path.expanduser(r"~\.founder-ide"))
VAULT = Path(os.path.expanduser(r"~\FounderVault\node-config.json"))
FOUNDER_LOGIN = "https://doxxedcrypto.digital/login?callbackUrl=/settings/builder"
FOUNDER_API_V1 = "https://api.doxxedcrypto.digital/v1"

BACKUP_DIR = Path(os.path.expanduser("~")) / "FounderVault" / "ide-rebrand-backups" / datetime.now().strftime(
    "%Y%m%d-%H%M%S"
)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

PROOF: dict[str, dict[str, int]] = {"before": {}, "after": {}}

USER_NEEDLES = [
    "Skycode AI",
    "Meet Skycode",
    "completely free project",
    "Sign in for free",
    "OpenRouter",
    "Sign in to use AI",
    "Sign up with Skycode",
    "Hi, I'm Skycode",
    "skycode-ai.ru",
    "Авторизоваться бесплатно",
    "Founder OS AI",
    "Connect Founder OS",
    "doxxedcrypto.digital",
]


def backup(path: Path) -> None:
    if not path.exists():
        return
    rel = re.sub(r"[^A-Za-z0-9._-]+", "_", str(path))
    if len(rel) > 180:
        rel = rel[-180:]
    dest = BACKUP_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        if not dest.exists():
            shutil.copytree(path, dest)
    else:
        shutil.copy2(path, dest)


def file_checksum_vscode(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode("ascii").rstrip("=")


def count_needles(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    data = path.read_text(encoding="utf-8", errors="ignore")
    return {n: data.count(n) for n in USER_NEEDLES if data.count(n)}


def record(phase: str, label: str, path: Path) -> None:
    PROOF.setdefault(phase, {})
    counts = count_needles(path)
    for k, v in counts.items():
        PROOF[phase][f"{label}:{k}"] = v


def replace_many(data: str, pairs: list[tuple[str, str]]) -> tuple[str, int]:
    total = 0
    for old, new in pairs:
        c = data.count(old)
        if c:
            data = data.replace(old, new)
            total += c
            print(f"  {c:4}x  {old[:70]!r} -> {new[:70]!r}")
    return data, total


# Exact user-facing phrase swaps (safe for minified bundles + JSON locales)
I18N_PAIRS: list[tuple[str, str]] = [
    # Welcome / free-trial (EN)
    (
        "Welcome! 🎉 Skycode AI is a completely free project. Try it right now — {{limit}} messages are available without signing up. To use without limits, just create a free account.",
        "Connected via Founder OS. AI runs through your Founder Node gateway — not Skycode cloud. Pair Founder Node (or sign in with Twitter on doxxedcrypto.digital) to use models.",
    ),
    ("Sign in for free", "Connect Founder OS"),
    ("Sign up with Skycode", "Connect Founder OS (Twitter)"),
    ("Hi, I'm Skycode", "Hi, I'm Founder OS"),
    ("About Skycode AI", "About Founder OS AI"),
    ("Skycode Environment", "Founder OS Environment"),
    ("Skycode is free — but we'd love your support!", "Founder OS is powered by your Founder Node and DDollars."),
    (
        "Sign up for an account to get access to the latest models, billing dashboard to view usage and credits, and more upcoming features.",
        "Sign in on doxxedcrypto.digital with Twitter, then pair Founder Node so this IDE can use your Founder OS AI Gateway.",
    ),
    (
        "To get started, sign up at [OpenRouter](https://openrouter.ai) — one key gives access to 200+ models, including free ones. [Learn more](https://skycode-ai.ru/docs/models)",
        "Primary path: Founder OS AI Gateway at [api.doxxedcrypto.digital/v1](https://api.doxxedcrypto.digital/v1) using your Node bearer from ~/FounderVault/node-config.json. [Open Founder OS](https://doxxedcrypto.digital/login)",
    ),
    ("Quick Start", "Founder OS Gateway"),
    (
        "To get started, sign in or configure an API key for access to AI models.",
        "Connect Founder OS (Twitter) or confirm ~/FounderVault/node-config.json is paired — then chat via the Founder OS gateway.",
    ),
    ("Help Improve Skycode", "Help Improve Founder OS"),
    (
        "Skycode collects error and usage data to help us fix bugs and improve the extension. No code, prompts, or personal information is ever sent.",
        "Founder OS may collect anonymous diagnostics when enabled. Prompts stay on your node/gateway path.",
    ),
    (
        "Skycode AI uses complex prompts and works best with Claude models. Less capable models may not perform as expected.",
        "Founder OS AI routes through your gateway. Prefer capable models for agent tasks.",
    ),
    (
        "(Note: Skycode uses complex prompts and works best with Claude models. Less capable models may not work as expected.)",
        "(Note: Founder OS routes via your gateway and works best with capable models.)",
    ),
    # RU free-trial / welcome
    (
        "Добро пожаловать! 🎉 Skycode AI — полностью бесплатный проект. Попробуйте прямо сейчас — {{limit}} сообщений доступны без регистрации. А чтобы пользоваться без ограничений, просто создайте бесплатный аккаунт.",
        "Подключено через Founder OS. ИИ идёт через ваш Founder Node gateway — не через облако Skycode. Войдите через Twitter на doxxedcrypto.digital или подтвердите пару с Founder Node.",
    ),
    ("Авторизоваться бесплатно", "Подключить Founder OS"),
    ("Зарегистрироваться через Skycode", "Подключить Founder OS (Twitter)"),
    ("Привет, я Skycode", "Привет, я Founder OS"),
    ("Окружение Skycode", "Окружение Founder OS"),
    (
        "Для начала работы зарегистрируйтесь на [OpenRouter](https://openrouter.ai) — один ключ даёт доступ к 200+ моделям, включая бесплатные. [Подробнее](https://skycode-ai.ru/docs/models)",
        "Основной путь: Founder OS AI Gateway [api.doxxedcrypto.digital/v1](https://api.doxxedcrypto.digital/v1) с Node bearer из ~/FounderVault/node-config.json. [Открыть Founder OS](https://doxxedcrypto.digital/login)",
    ),
    ("Быстрый старт", "Founder OS Gateway"),
    # Broad branding (after specific phrases)
    ("Skycode AI", "Founder OS AI"),
    ("Meet Skycode, your new coding partner", "Meet Founder OS, your Founder IDE coding partner"),
    ("Meet Skycode", "Meet Founder OS"),
    ("Tell Skycode what you want", "Tell Founder OS what you want"),
    ("Let Skycode Learn Your Codebase", "Let Founder OS Learn Your Codebase"),
    ("Point Skycode to your project", "Point Founder OS to your project"),
    ("Review Skycode's plans", "Review Founder OS plans"),
    ("Skycode codes like a developer", "Founder OS codes like a developer"),
    ("Skycode empowers you", "Founder OS empowers you"),
    ("mode Skycode AI will", "mode Founder OS AI will"),
    ("so Skycode can work", "so Founder OS can work"),
    ("task for Skycode", "task for Founder OS"),
    ("re-opening Skycode", "re-opening Founder IDE"),
    ("Help improve Skycode AI", "Help improve Founder OS AI"),
    # Docs / auth hosts → Founder OS site (user-facing links)
    ("https://skycode-ai.ru/ru/license", "https://doxxedcrypto.digital/terms"),
    ("https://skycode-ai.ru/ru/privacy", "https://doxxedcrypto.digital/privacy"),
    ("https://skycode-ai.ru/docs/models", "https://doxxedcrypto.digital/settings/builder"),
    ("https://skycode-ai.ru/ru/docs/models", "https://doxxedcrypto.digital/settings/builder"),
    ("https://openrouter.ai", "https://doxxedcrypto.digital/login"),
]

# Remaining "Skycode" word in UI strings only — careful, keep ids like skycode.SidebarProvider
SAFE_SKYCODE_WORD_PAIRS: list[tuple[str, str]] = [
    ('"name": "Skycode"', '"name": "Founder OS"'),
    ('"displayName": "Skycode AI"', '"displayName": "Founder OS AI"'),
    ('"displayName": "Founder AI"', '"displayName": "Founder OS AI"'),
    ("I'm Skycode", "I'm Founder OS"),
]

NLS_PAIRS: list[tuple[str, str]] = [
    ("Sign in to use AI Features", "Connect Founder OS"),
    ("Sign in to use AI features...", "Connect Founder OS..."),
    ("Sign in to use AI features", "Connect Founder OS"),
]


def patch_text_file(path: Path, pairs: list[tuple[str, str]], extra: list[tuple[str, str]] | None = None) -> int:
    if not path.exists():
        print(f"skip missing {path}")
        return 0
    backup(path)
    data = path.read_text(encoding="utf-8", errors="ignore")
    original = data
    data, n = replace_many(data, pairs + (extra or []))
    if data != original:
        path.write_text(data, encoding="utf-8", newline="\n")
        print(f"patched {path} ({n} replacements)")
    else:
        print(f"no changes {path}")
    return n


def patch_product_json(path: Path) -> None:
    if not path.exists():
        return
    backup(path)
    product = json.loads(path.read_text(encoding="utf-8"))
    dca = product.get("defaultChatAgent") or {}
    dca.update(
        {
            "documentationUrl": "https://doxxedcrypto.digital",
            "termsStatementUrl": "https://doxxedcrypto.digital/terms",
            "privacyStatementUrl": "https://doxxedcrypto.digital/privacy",
            "manageSettingsUrl": "https://doxxedcrypto.digital/settings/builder",
            "signUpUrl": FOUNDER_LOGIN,
            "provider": {
                "default": {"id": "founder-os", "name": "Founder OS (Twitter)"},
            },
            "providerScopes": [],
            "entitlementUrl": "",
            "entitlementSignupLimitedUrl": "",
            "chatQuotaExceededContext": "",
            "completionsQuotaExceededContext": "",
        }
    )
    product["defaultChatAgent"] = dca
    product["trustedExtensionAuthAccess"] = {"github": [], "github-enterprise": []}
    # Disable stock chat setup prompts when Founder provider is the path
    cfg = product.get("configurationDefaults") or {}
    cfg.update(
        {
            "chat.commandCenter.enabled": False,
            "chat.experimental.detectParticipant.enabled": False,
            "chat.editing.confirmEditRequestRetry": False,
            "workbench.startupEditor": "none",
        }
    )
    product["configurationDefaults"] = cfg
    path.write_text(json.dumps(product, indent="\t", ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"patched product.json providers -> Founder OS at {path}")


def patch_package_json(path: Path) -> None:
    if not path.exists():
        return
    backup(path)
    pkg = json.loads(path.read_text(encoding="utf-8"))
    pkg["displayName"] = "Founder OS AI"
    pkg["description"] = "Founder OS AI assistant for Founder IDE — routed via your Founder Node gateway."
    pkg["author"] = {"name": "doxxedcrypto"}
    pkg["publisher"] = "doxxedcrypto"
    pkg["homepage"] = "https://doxxedcrypto.digital"
    keywords = pkg.get("keywords") or []
    pkg["keywords"] = ["founder-os", "doxxedcrypto", "founder-ide"] + [
        k for k in keywords if k not in ("skycode", "openrouter")
    ]
    walks = (((pkg.get("contributes") or {}).get("walkthroughs")) or [])
    for w in walks:
        w["title"] = "Meet Founder OS, your Founder IDE coding partner"
        w["description"] = (
            "Founder OS codes with you through your own Founder Node gateway. Here are ways to put it to work:"
        )
        for step in w.get("steps") or []:
            desc = step.get("description") or ""
            title = step.get("title") or ""
            step["description"] = (
                desc.replace("Skycode", "Founder OS")
                .replace("Founder AI", "Founder OS")
            )
            step["title"] = title.replace("Skycode", "Founder OS")
    views = (((pkg.get("contributes") or {}).get("views") or {}).get("workbench.panel.chat")) or []
    for v in views:
        if v.get("id") == "skycode.SidebarProvider":
            v["name"] = "Founder OS"
    # English command titles (installed copy has mojibake Russian)
    title_map = {
        "skycode.plusButtonClicked": "New Task",
        "skycode.mcpButtonClicked": "MCP Servers",
        "skycode.historyButtonClicked": "History",
        "skycode.accountButtonClicked": "Account",
        "skycode.settingsButtonClicked": "Settings",
    }
    for cmd in ((pkg.get("contributes") or {}).get("commands")) or []:
        if cmd.get("command") in title_map:
            cmd["title"] = title_map[cmd["command"]]
    path.write_text(json.dumps(pkg, indent="  ", ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"patched package.json at {path}")


def patch_workbench_chat_gate(wb_path: Path) -> None:
    """Soften ChatSetup force-sign-in dialog toward Founder OS / skip when possible."""
    if not wb_path.exists():
        return
    backup(wb_path)
    data = wb_path.read_text(encoding="utf-8", errors="ignore")
    original = data
    data, n = replace_many(data, NLS_PAIRS)
    # Prefer opening Founder login over GitHub when setup asks for sign-in URL patterns
    extras = [
        (
            'id: "github",\n\t\t\t\tname: "GitHub"',
            'id: "founder-os",\n\t\t\t\tname: "Founder OS (Twitter)"',
        ),
        (
            'id:"github",name:"GitHub"',
            'id:"founder-os",name:"Founder OS (Twitter)"',
        ),
    ]
    data, n2 = replace_many(data, extras)
    # Soft-disable anonymous force dialog title path — keep Skip working
    if data != original:
        wb_path.write_text(data, encoding="utf-8", newline="\n")
        print(f"patched workbench ({n + n2} replacements)")
    else:
        print("no workbench string changes")


def patch_auth_login_in_extension(ext_path: Path) -> None:
    """Redirect Skycode cloud auth URL construction toward Founder OS login."""
    if not ext_path.exists():
        return
    backup(ext_path)
    data = ext_path.read_text(encoding="utf-8", errors="ignore")
    original = data
    pairs = [
        ("https://skycode-ai.ru", "https://doxxedcrypto.digital"),
        ("https://skycode-ai.local", "https://doxxedcrypto.digital"),
        ("http://skycode-ai.ru", "https://doxxedcrypto.digital"),
        ("skycode-ai.ru", "doxxedcrypto.digital"),
        # OpenRouter auth callback → Founder login (primary path messaging)
        (
            "https://openrouter.ai/auth?callback_url=",
            FOUNDER_LOGIN + "&from=openrouter_legacy&callback_url=",
        ),
        ("https://openrouter.ai", "https://doxxedcrypto.digital/login"),
        ("OpenRouter", "Founder OS Gateway"),
        ("Skycode AI", "Founder OS AI"),
        ("Sign in with GitHub", "Connect Founder OS"),
        ("Sign in with Google", "Connect Founder OS"),
        ("Sign in with Apple", "Connect Founder OS"),
        ("GitHub / Google / Apple", "Founder OS (Twitter)"),
    ]
    data, n = replace_many(data, pairs)
    # Also apply i18n-ish strings embedded in extension
    data, n2 = replace_many(data, I18N_PAIRS)
    data, n3 = replace_many(data, SAFE_SKYCODE_WORD_PAIRS)
    if data != original:
        ext_path.write_text(data, encoding="utf-8", newline="\n")
        print(f"patched extension.js auth hosts ({n + n2 + n3})")
    else:
        print("no extension.js auth host changes")


def patch_webview_bundle(index_path: Path) -> None:
    if not index_path.exists():
        return
    backup(index_path)
    data = index_path.read_text(encoding="utf-8", errors="ignore")
    original = data
    # Force English default language if still present
    lang_pairs = [
        (
            'localStorage.getItem("skycode.interfaceLanguage")==="en"?"en":"ru"',
            'localStorage.getItem("skycode.interfaceLanguage")==="ru"?"ru":"en"',
        ),
        ('dictationLanguage:"ru"', 'dictationLanguage:"en"'),
    ]
    data, n0 = replace_many(data, lang_pairs)
    data, n1 = replace_many(data, I18N_PAIRS)
    data, n2 = replace_many(data, SAFE_SKYCODE_WORD_PAIRS)
    # Sweep leftover user-facing brand hosts / provider names in the webview bundle
    sweep = [
        ("https://skycode-ai.ru", "https://doxxedcrypto.digital"),
        ("http://skycode-ai.ru", "https://doxxedcrypto.digital"),
        ("skycode-ai.ru", "doxxedcrypto.digital"),
        ("https://openrouter.ai", "https://doxxedcrypto.digital/login"),
        ("OpenRouter", "Founder OS Gateway"),
        ("Sign in with GitHub", "Connect Founder OS"),
        ("Sign in with Google", "Connect Founder OS"),
        ("Sign in with Apple", "Connect Founder OS"),
    ]
    data, n3 = replace_many(data, sweep)
    if data != original:
        index_path.write_text(data, encoding="utf-8", newline="\n")
        print(f"patched webview index.js ({n0 + n1 + n2 + n3})")
    else:
        print("no webview index changes")


def patch_locale_json(path: Path) -> None:
    if not path.exists():
        return
    backup(path)
    data = path.read_text(encoding="utf-8")
    data, n = replace_many(data, I18N_PAIRS + SAFE_SKYCODE_WORD_PAIRS)
    # Extra global Skycode → Founder OS in locale values only (JSON values)
    # Avoid breaking keys that contain skycode.
    def repl_value(m: re.Match[str]) -> str:
        key, val = m.group(1), m.group(2)
        if "skycode" in key.lower():
            # still replace brand inside values
            pass
        new_val = val
        for old, new in [
            ("Skycode AI", "Founder OS AI"),
            ("Skycode", "Founder OS"),
            ("skycode-ai.ru", "doxxedcrypto.digital"),
            ("OpenRouter", "Founder OS Gateway"),
            ("openrouter.ai", "doxxedcrypto.digital"),
        ]:
            new_val = new_val.replace(old, new)
        return f'"{key}": "{new_val}"'

    data2, n2 = re.subn(r'"([^"]+)": "((?:\\.|[^"\\])*)"', repl_value, data)
    path.write_text(data2, encoding="utf-8", newline="\n")
    print(f"patched locale {path} (phrase={n}, value-pass={n2})")


def wire_vault_state() -> None:
    print("\n=== Wire FounderVault into Skycode state ===")
    if not VAULT.exists():
        print("No vault — skip")
        return
    vault = json.loads(VAULT.read_text(encoding="utf-8"))
    api_base = (vault.get("apiBaseUrl") or "https://api.doxxedcrypto.digital").rstrip("/")
    if "api." not in api_base and "doxxedcrypto.digital" in api_base:
        openai_base = FOUNDER_API_V1
        gateway = "https://api.doxxedcrypto.digital"
    else:
        gateway = api_base
        openai_base = f"{api_base}/v1" if not api_base.endswith("/v1") else api_base
    node_id = vault["nodeId"]
    node_token = vault["nodeToken"]
    bearer = f"fos_{node_id}:{node_token}"

    for settings_path in [ROAMING / "User" / "settings.json", DOT / "User" / "settings.json"]:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        backup(settings_path)
        settings = {}
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
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
                "skycode.preferredLanguage": "English",
            }
        )
        settings_path.write_text(json.dumps(settings, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"settings {settings_path}")

    for db_path in [
        ROAMING / "User" / "globalStorage" / "state.vscdb",
        DOT / "User" / "globalStorage" / "state.vscdb",
    ]:
        if not db_path.exists():
            print(f"missing {db_path}")
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
                "welcomeViewCompleted": True,
                "openAiBaseUrl": openai_base,
                "openAiHeaders": {"Authorization": f"Bearer {bearer}"},
                "openAiApiKey": bearer,
                "planModeApiProvider": "openai",
                "actModeApiProvider": "openai",
                "planModeOpenAiModelId": state.get("planModeOpenAiModelId") or "founder-os-auto",
                "actModeOpenAiModelId": state.get("actModeOpenAiModelId") or "founder-os-auto",
                "preferredLanguage": "English",
                "lastDismissedInfoBannerVersion": 1,
            }
        )
        payload = json.dumps(state, ensure_ascii=False)
        cur.execute(
            "INSERT INTO ItemTable(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("skycode.skycode", payload),
        )
        # Skip workbench chat setup / walkthrough friction
        for k, v in [
            ("chat.setup.skipped", "true"),
            ("workbench.action.chat.triggerSetup.hidden", "true"),
            ("interactiveSession.showWelcome", "false"),
        ]:
            cur.execute(
                "INSERT INTO ItemTable(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v),
            )
        con.commit()
        con.close()
        print(f"state DB updated {db_path}")


def fix_checksums(product_path: Path, out_base: Path) -> None:
    print("\n=== Fix product.json checksums ===")
    if not product_path.exists():
        return
    backup(product_path)
    product = json.loads(product_path.read_text(encoding="utf-8"))
    checksums = product.get("checksums") or {}
    updated = []
    for rel, expected in list(checksums.items()):
        path = out_base.joinpath(*rel.split("/"))
        if not path.exists():
            print(f"MISSING {path}")
            continue
        actual = file_checksum_vscode(path)
        if actual != expected:
            print(f"MISMATCH {rel}")
            checksums[rel] = actual
            updated.append(rel)
        else:
            print(f"OK {rel}")
    product["checksums"] = checksums
    product_path.write_text(json.dumps(product, indent="\t", ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"updated {len(updated)} checksum(s)")


def patch_source_auth_and_ui() -> None:
    print("\n=== Patch skycode-fork source ===")
    if not FORK_SKY.exists():
        print("fork skycode missing")
        return

    # locales
    for loc in ["en.json", "ru.json"]:
        patch_locale_json(FORK_SKY / "webview-ui" / "src" / "i18n" / "locales" / loc)

    patch_package_json(FORK_SKY / "package.json")

    # accountLoginClicked → Founder OS Twitter login
    login_ts = FORK_SKY / "src" / "core" / "controller" / "account" / "accountLoginClicked.ts"
    if login_ts.exists():
        backup(login_ts)
        login_ts.write_text(
            '''import { EmptyRequest, String } from "@shared/proto/skycode/common"
import { openExternal } from "@/utils/env"
import { Controller } from "../index"

const FOUNDER_LOGIN = "https://doxxedcrypto.digital/login?callbackUrl=/settings/builder"

/**
 * Founder IDE: open Twitter / Founder OS login instead of Skycode cloud OAuth
 * (GitHub / Google / Apple). Credentials sync via Founder Node → ~/FounderVault.
 */
export async function accountLoginClicked(_controller: Controller, _: EmptyRequest): Promise<String> {
	await openExternal(FOUNDER_LOGIN)
	return String.create({ value: FOUNDER_LOGIN })
}
''',
            encoding="utf-8",
            newline="\n",
        )
        print(f"rewrote {login_ts}")

    # AccountWelcomeView links
    acct = FORK_SKY / "webview-ui" / "src" / "components" / "account" / "AccountWelcomeView.tsx"
    if acct.exists():
        backup(acct)
        text = acct.read_text(encoding="utf-8")
        text = text.replace("https://skycode-ai.ru/ru/license", "https://doxxedcrypto.digital/terms")
        text = text.replace("https://skycode-ai.ru/ru/privacy", "https://doxxedcrypto.digital/privacy")
        acct.write_text(text, encoding="utf-8", newline="\n")
        print(f"patched {acct}")

    # AuthService.createAuthRequest — open Founder login
    auth = FORK_SKY / "src" / "services" / "auth" / "AuthService.ts"
    if auth.exists():
        backup(auth)
        text = auth.read_text(encoding="utf-8")
        if "doxxedcrypto.digital/login" not in text:
            old = """\t\tconst authUrl = await this._provider.getAuthRequest(callbackUrl)
\t\tconst authUrlString = authUrl.toString()

\t\tawait openExternal(authUrlString)
\t\ttelemetryService.captureAuthStarted(this._provider.name)
\t\treturn String.create({ value: authUrlString })"""
            new = """\t\t// Founder IDE: Skycode cloud OAuth (GitHub/Google/Apple) replaced by Founder OS Twitter login.
\t\tconst authUrlString = "https://doxxedcrypto.digital/login?callbackUrl=/settings/builder"
\t\tawait openExternal(authUrlString)
\t\ttelemetryService.captureAuthStarted(this._provider.name)
\t\treturn String.create({ value: authUrlString })"""
            if old in text:
                text = text.replace(old, new)
                auth.write_text(text, encoding="utf-8", newline="\n")
                print(f"patched AuthService.createAuthRequest")
            else:
                print("AuthService pattern not found — skip")


def clean_welcome_section_properly() -> None:
    """Hide free-trial Skycode banner when Founder gateway credentials are present."""
    welcome = (
        FORK_SKY
        / "webview-ui"
        / "src"
        / "components"
        / "chat"
        / "chat-view"
        / "components"
        / "layout"
        / "WelcomeSection.tsx"
    )
    if not welcome.exists():
        return
    backup(welcome)
    text = welcome.read_text(encoding="utf-8")
    if "apiConfiguration," not in text:
        text = text.replace(
            "\tconst {\n\t\topenRouterModels,",
            "\tconst {\n\t\tapiConfiguration,\n\t\topenRouterModels,",
        )
    if "const hasFounderGateway" not in text:
        text = text.replace(
            "\tconst { handleFieldsChange } = useApiConfigurationHandlers()\n",
            "\tconst { handleFieldsChange } = useApiConfigurationHandlers()\n"
            "\tconst hasFounderGateway = Boolean(\n"
            "\t\tskycodeUser ||\n"
            "\t\tapiConfiguration?.openAiApiKey ||\n"
            '\t\tapiConfiguration?.openAiBaseUrl?.includes("doxxedcrypto"),\n'
            "\t)\n",
        )
    text = text.replace("{!skycodeUser && (", "{!hasFounderGateway && (")
    welcome.write_text(text, encoding="utf-8", newline="\n")
    print("patched WelcomeSection founder-gateway gate")


def main() -> None:
    print("BACKUP_DIR", BACKUP_DIR)

    targets = {
        "index.js": INSTALL / "extensions" / "skycode" / "webview-ui" / "build" / "assets" / "index.js",
        "package.json": INSTALL / "extensions" / "skycode" / "package.json",
        "extension.js": INSTALL / "extensions" / "skycode" / "dist" / "extension.js",
        "nls": INSTALL / "out" / "nls.messages.json",
        "workbench": INSTALL / "out" / "vs" / "workbench" / "workbench.desktop.main.js",
        "product": INSTALL / "product.json",
    }
    for label, path in targets.items():
        record("before", label, path)

    print("\n=== 1. Webview i18n / branding ===")
    patch_webview_bundle(targets["index.js"])

    print("\n=== 2. package.json walkthrough / view name ===")
    patch_package_json(targets["package.json"])

    print("\n=== 3. extension.js auth hosts ===")
    patch_auth_login_in_extension(targets["extension.js"])

    print("\n=== 4. nls + workbench sign-in gate ===")
    patch_text_file(targets["nls"], NLS_PAIRS)
    patch_workbench_chat_gate(targets["workbench"])

    print("\n=== 5. product.json Founder OS providers ===")
    patch_product_json(targets["product"])

    print("\n=== 6. Source fork ===")
    patch_source_auth_and_ui()
    clean_welcome_section_properly()

    # Also mirror install patches into FounderIDE-build / upstream if present
    for base in [
        Path(r"C:\Users\user\Desktop\skycode-fork\FounderIDE-build\resources\app"),
        UPSTREAM,
    ]:
        idx = base / "extensions" / "skycode" / "webview-ui" / "build" / "assets" / "index.js"
        if idx.exists():
            print(f"\n=== Mirror webview -> {base} ===")
            patch_webview_bundle(idx)
        pkg = base / "extensions" / "skycode" / "package.json"
        if pkg.exists():
            patch_package_json(pkg)
        prod = base / "product.json"
        if prod.exists():
            patch_product_json(prod)

    wire_vault_state()
    fix_checksums(targets["product"], INSTALL / "out")
    if (UPSTREAM / "product.json").exists():
        fix_checksums(UPSTREAM / "product.json", UPSTREAM / "out")

    for label, path in targets.items():
        record("after", label, path)

    proof_path = Path(__file__).with_name("rebrand-proof.json")
    proof_path.write_text(json.dumps(PROOF, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n=== PROOF ===")
    print(json.dumps(PROOF, indent=2, ensure_ascii=False))
    print("\nDONE")


if __name__ == "__main__":
    main()
