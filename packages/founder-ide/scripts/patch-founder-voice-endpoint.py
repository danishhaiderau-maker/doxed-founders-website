"""Keep packaged Founder voice aligned with the explicitly selected AI route."""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path


DEFAULT_APP = Path(r"C:\Users\user\AppData\Local\Programs\Founder IDE\resources\app")

OLD_CONSTANT = (
    'var GLM_TRANSCRIPTION_ENDPOINT = '
    '"https://open.bigmodel.cn/api/paas/v4/audio/transcriptions";'
)
NEW_CONSTANTS = (
    'var GLM_TRANSCRIPTION_ENDPOINT_GLOBAL = '
    '"https://api.z.ai/api/paas/v4/audio/transcriptions";\n'
    'var GLM_TRANSCRIPTION_ENDPOINT_CHINA = '
    '"https://open.bigmodel.cn/api/paas/v4/audio/transcriptions";'
)
OLD_PROFILE_BLOCK = """var isGlmSpeechProfile = (profile) => {
  const identity2 = `${profile.label} ${profile.model} ${profile.baseUrl}`.toLowerCase();
  return identity2.includes("glm") || identity2.includes("zhipu") || identity2.includes("bigmodel.cn") || identity2.includes("z.ai");
};"""
NEW_PROFILE_BLOCK = f"""{OLD_PROFILE_BLOCK}
var glmTranscriptionEndpoint = (profile) => {{
  try {{
    const hostname = new URL(profile.baseUrl).hostname.toLowerCase();
    if (hostname === "open.bigmodel.cn" || hostname.endsWith(".bigmodel.cn")) {{
      return GLM_TRANSCRIPTION_ENDPOINT_CHINA;
    }}
  }} catch {{
  }}
  return GLM_TRANSCRIPTION_ENDPOINT_GLOBAL;
}};"""
OLD_CAPTURE_GUARD = """    if (recorder.chunks.length === 0 || !voiceProfile) {
      setVoicePhase("idle");
      setVoiceError("No speech was captured. Your typed text is unchanged.");
      return;
    }"""
NEW_CAPTURE_GUARD = """    if (recorder.chunks.length === 0) {
      setVoicePhase("idle");
      setVoiceError("No speech was captured. Your typed text is unchanged.");
      return;
    }"""
OLD_TRANSCRIBE_BLOCK = """      const result = await commandService.executeCommand(
        "founder.personalAi.transcribe",
        { profileId: voiceProfile.id, audioBase64: founderVoiceBase64(founderVoiceWav(recorder.chunks, recorder.context.sampleRate)) }
      );"""
OLD_MANAGED_FALLBACK_BLOCK = """      const audioBase64 = founderVoiceBase64(founderVoiceWav(recorder.chunks, recorder.context.sampleRate));
      let result;
      try {
        result = await commandService.executeCommand(
          "founderOs.transcribeVoice",
          { audioBase64 }
        );
      } catch (managedError) {
        if (!voiceProfile) throw managedError;
        result = await commandService.executeCommand(
          "founder.personalAi.transcribe",
          { profileId: voiceProfile.id, audioBase64 }
        );
      }"""
NEW_TRANSCRIBE_BLOCK = """      const audioBase64 = founderVoiceBase64(founderVoiceWav(recorder.chunks, recorder.context.sampleRate));
      let result;
      if (voiceProfile) {
        result = await commandService.executeCommand(
          "founder.personalAi.transcribe",
          { profileId: voiceProfile.id, audioBase64 }
        );
      } else {
        result = await commandService.executeCommand(
          "founderOs.transcribeVoice",
          { audioBase64 }
        );
      }"""
OLD_PROFILE_REQUIREMENT = """    if (!voiceProfile) {
      setVoiceError("Connect and enable a GLM Personal AI profile before using voice input.");
      await commandService.executeCommand("founderOs.openSettings", "ai");
      return;
    }
"""
OLD_BUNDLED_VOICE_PROFILE = re.compile(
    r"""(?P<prefix>  const isDisabled = [^\n]+isFeatureNameDisabled\("Chat", (?P<settings>settingsState\d+)\);\n"""
    r"""  const voiceSupported = \(0, (?P<react>import_react\d+)\.useMemo\)\(\(\) => founderVoiceSupported\(\), \[\]\);\n)"""
    r"""  const voiceProfile = \(0, (?P=react)\.useMemo\)\(\(\) => reviewerProfiles\.find\(\(profile\) => \{\n"""
    r"""    const identity2 = `\$\{profile\.label\} \$\{profile\.model\} \$\{profile\.baseUrl\}`\.toLowerCase\(\);\n"""
    r"""    return identity2\.includes\("glm"\) \|\| identity2\.includes\("zhipu"\) \|\| identity2\.includes\("bigmodel\.cn"\) \|\| identity2\.includes\("z\.ai"\);\n"""
    r"""  \}\), \[reviewerProfiles\]\);"""
)


def selected_profile_block(match: re.Match[str]) -> str:
    settings = match.group("settings")
    react = match.group("react")
    return (
        match.group("prefix")
        + f"  const selectedChatModel = {settings}.modelSelectionOfFeature.Chat?.modelName;\n"
        + f"  const voiceProfile = (0, {react}.useMemo)(() => reviewerProfiles.find((profile) => {{\n"
        + "    const identity2 = `${profile.label} ${profile.model} ${profile.baseUrl}`.toLowerCase();\n"
        + "    const isSelected = profile.label === selectedChatModel;\n"
        + '    const isGlm = identity2.includes("glm") || identity2.includes("zhipu") || identity2.includes("bigmodel.cn") || identity2.includes("z.ai");\n'
        + "    return isSelected && isGlm;\n"
        + "  }), [reviewerProfiles, selectedChatModel]);"
    )


def patch(app: Path, workbench_override: Path | None = None, backup: bool = True) -> None:
    workbench = (
        workbench_override
        if workbench_override is not None
        else app / "out" / "vs" / "workbench" / "workbench.desktop.main.js"
    )
    if not workbench.is_file():
        raise SystemExit(f"Founder IDE workbench bundle was not found below {app}")

    data = workbench.read_text(encoding="utf-8")
    region_patched = (
        NEW_CONSTANTS in data
        and NEW_PROFILE_BLOCK in data
        and "fetch(glmTranscriptionEndpoint(profile), {" in data
    )
    selected_voice_patched = (
        NEW_CAPTURE_GUARD in data
        and NEW_TRANSCRIBE_BLOCK in data
        and "const selectedChatModel =" in data
        and "return isSelected && isGlm;" in data
        and OLD_MANAGED_FALLBACK_BLOCK not in data
        and OLD_PROFILE_REQUIREMENT not in data
    )
    if region_patched and selected_voice_patched:
        print("Founder voice follows the explicitly selected chat model")
        return

    if not region_patched:
        if OLD_CONSTANT not in data:
            raise SystemExit("Founder voice endpoint constant changed; no patch applied")
        if data.count(OLD_PROFILE_BLOCK) != 1:
            raise SystemExit("Founder GLM profile signature changed; no patch applied")
        if data.count("fetch(GLM_TRANSCRIPTION_ENDPOINT, {") != 1:
            raise SystemExit("Founder transcription fetch signature changed; no patch applied")
        data = data.replace(OLD_CONSTANT, NEW_CONSTANTS, 1)
        data = data.replace(OLD_PROFILE_BLOCK, NEW_PROFILE_BLOCK, 1)
        data = data.replace(
            "fetch(GLM_TRANSCRIPTION_ENDPOINT, {",
            "fetch(glmTranscriptionEndpoint(profile), {",
            1,
        )

    if not selected_voice_patched:
        if OLD_CAPTURE_GUARD in data:
            if data.count(OLD_CAPTURE_GUARD) != 1:
                raise SystemExit("Founder capture guard is ambiguous; no patch applied")
            data = data.replace(OLD_CAPTURE_GUARD, NEW_CAPTURE_GUARD, 1)
        elif NEW_CAPTURE_GUARD not in data:
            raise SystemExit("Founder capture guard changed; no patch applied")

        data, profile_replacements = OLD_BUNDLED_VOICE_PROFILE.subn(
            selected_profile_block,
            data,
            count=1,
        )
        if profile_replacements == 0 and (
            "const selectedChatModel =" not in data
            or "return isSelected && isGlm;" not in data
        ):
            raise SystemExit("Founder selected voice-profile signature changed; no patch applied")

        if OLD_MANAGED_FALLBACK_BLOCK in data:
            if data.count(OLD_MANAGED_FALLBACK_BLOCK) != 1:
                raise SystemExit("Founder managed fallback is ambiguous; no patch applied")
            data = data.replace(OLD_MANAGED_FALLBACK_BLOCK, NEW_TRANSCRIBE_BLOCK, 1)
        elif OLD_TRANSCRIBE_BLOCK in data:
            if data.count(OLD_TRANSCRIBE_BLOCK) != 1:
                raise SystemExit("Founder transcription command is ambiguous; no patch applied")
            data = data.replace(OLD_TRANSCRIBE_BLOCK, NEW_TRANSCRIBE_BLOCK, 1)
        elif NEW_TRANSCRIBE_BLOCK not in data:
            raise SystemExit("Founder transcription command changed; no patch applied")

        if OLD_PROFILE_REQUIREMENT in data:
            if data.count(OLD_PROFILE_REQUIREMENT) != 1:
                raise SystemExit("Founder BYOK-only requirement is ambiguous; no patch applied")
            data = data.replace(OLD_PROFILE_REQUIREMENT, "", 1)

    backup_dir: Path | None = None
    if backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = Path.home() / "FounderVault" / "ide-voice-backups" / stamp
        backup_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(workbench, backup_dir / workbench.name)
    workbench.write_text(data, encoding="utf-8", newline="")

    verify = workbench.read_text(encoding="utf-8")
    if NEW_CONSTANTS not in verify or NEW_PROFILE_BLOCK not in verify:
        raise SystemExit("Founder GLM voice region patch did not verify")
    if "fetch(glmTranscriptionEndpoint(profile), {" not in verify:
        raise SystemExit("Founder GLM voice fetch still ignores the saved region")
    if "fetch(GLM_TRANSCRIPTION_ENDPOINT, {" in verify:
        raise SystemExit("Legacy fixed-region Founder voice fetch still remains")
    if NEW_CAPTURE_GUARD not in verify or NEW_TRANSCRIBE_BLOCK not in verify:
        raise SystemExit("Founder selected voice route did not verify")
    if "const selectedChatModel =" not in verify or "return isSelected && isGlm;" not in verify:
        raise SystemExit("Founder microphone still ignores the selected chat model")
    if OLD_MANAGED_FALLBACK_BLOCK in verify:
        raise SystemExit("Founder microphone still silently falls back after a managed failure")
    if OLD_PROFILE_REQUIREMENT in verify:
        raise SystemExit("Founder microphone still requires a Personal AI profile")
    suffix = f"; backup: {backup_dir}" if backup_dir is not None else ""
    print(f"Founder voice follows the explicitly selected chat model{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--workbench", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    workbench = args.workbench.resolve() if args.workbench is not None else None
    patch(args.app.resolve(), workbench, not args.no_backup)


if __name__ == "__main__":
    main()
