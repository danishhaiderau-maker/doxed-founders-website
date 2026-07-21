"""Harden the installed Founder native AI error surface after core packaging."""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path


DEFAULT_APP = Path(r"C:\Users\user\AppData\Local\Programs\Founder IDE\resources\app")


def patch(app: Path) -> None:
    main_js = app / "out" / "main.js"
    if not main_js.is_file():
        raise SystemExit(f"Founder IDE native bundle was not found below {app}")

    data = main_js.read_text(encoding="utf-8")
    raw_error = re.compile(
        r'if\(!(?P<response>[A-Za-z_$][A-Za-z0-9_$]*)\.ok\|\|!(?P=response)\.body\)'
        r'\{const (?P<body>[A-Za-z_$][A-Za-z0-9_$]*)=await (?P=response)\.text\(\)'
        r'\.catch\(\(\)=>""\);return (?P<error>[A-Za-z_$][A-Za-z0-9_$]*)'
        r'\(\{message:`Founder OS gateway returned \$\{(?P=response)\.status\}: '
        r'\$\{(?P=body)\.slice\(0,500\)\}`,fullError:null\}\),null\}'
    )

    def safe_error(match: re.Match[str]) -> str:
        response = match.group("response")
        error = match.group("error")
        return (
            f'if(!{response}.ok||!{response}.body){{await {response}.text().catch(()=>"");'
            f'const founderMessage={response}.status===401||{response}.status===403?'
            '"Your Founder session needs to be renewed. Open the Founder panel and sign in again.":'
            f'{response}.status===429?"Your Founder AI allowance is temporarily unavailable. Check Founder Settings.":'
            f'{response}.status>=500?"Founder AI is temporarily unavailable. Your workspace and local files are unaffected.":'
            '"Founder could not send this request. Check the active model in Founder Settings.";'
            f'return {error}({{message:founderMessage,fullError:null}}),null}}'
        )

    data, count = raw_error.subn(safe_error, data, count=1)
    already_safe = "const founderMessage=" in data
    if count != 1 and not already_safe:
        raise SystemExit("Native gateway error signature changed; no patch applied")

    data = data.replace("Void's Settings", "Founder Settings")
    data = data.replace("Void: Response", "Founder: Response")
    data = data.replace("Void sendLLM", "Founder sendLLM")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path.home() / "FounderVault" / "ide-native-ai-backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(main_js, backup_dir / main_js.name)
    main_js.write_text(data, encoding="utf-8", newline="")

    verify = main_js.read_text(encoding="utf-8")
    if "Founder OS gateway returned ${" in verify:
        raise SystemExit("Raw native gateway error still remains")
    if "const founderMessage=" not in verify:
        raise SystemExit("Safe native gateway message did not verify")
    print(f"Founder native AI errors hardened; backup: {backup_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    args = parser.parse_args()
    patch(args.app.resolve())


if __name__ == "__main__":
    main()
