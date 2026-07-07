# Fix Cursor Z.ai (GLM) BYOK settings on Windows.
# Close Cursor completely before running, or the state DB may stay locked.
#
# What this fixes:
# - Wrong base URL (general API -> Coding Plan endpoint)
# - Agent still on Auto/default instead of custom GLM-5.2
#
# Usage:
#   1. Quit Cursor (all windows)
#   2. powershell -ExecutionPolicy Bypass -File scripts/fix-cursor-zai-settings.ps1
#   3. Reopen Cursor and pick GLM-5.2 in the model dropdown

$ErrorActionPreference = "Stop"

$db = Join-Path $env:APPDATA "Cursor\User\globalStorage\state.vscdb"
if (-not (Test-Path $db)) {
  Write-Error "Cursor state DB not found: $db"
}

$backup = "$db.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $db $backup -Force
Write-Host "Backup: $backup"

$py = @'
import sqlite3, os, json, sys

db = os.path.join(os.environ["APPDATA"], "Cursor", "User", "globalStorage", "state.vscdb")
key = "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser"

conn = sqlite3.connect(db, timeout=10)
conn.execute("PRAGMA busy_timeout=10000")
cur = conn.cursor()
cur.execute("SELECT value FROM ItemTable WHERE key = ?", (key,))
row = cur.fetchone()
if not row:
    print("ERROR: applicationUser storage key missing")
    sys.exit(1)

val = row[0]
if isinstance(val, bytes):
    val = val.decode("utf-8")
data = json.loads(val)

old_url = data.get("openAIBaseUrl")
new_url = "https://api.z.ai/api/coding/paas/v4"
data["openAIBaseUrl"] = new_url
data["useOpenAIKey"] = True

ai = data.setdefault("aiSettings", {})
ai.setdefault("modelConfig", {})

glm_model = {
    "modelName": "GLM-5.2",
    "maxMode": False,
    "selectedModels": [{"modelId": "GLM-5.2", "parameters": []}],
}
for mode in ["composer", "background-composer", "plan-execution", "quick-agent", "cmd-k"]:
    ai["modelConfig"][mode] = json.loads(json.dumps(glm_model))

enabled = set(ai.get("modelOverrideEnabled", []))
enabled.add("GLM-5.2")
ai["modelOverrideEnabled"] = sorted(enabled)

disabled = set(ai.get("modelOverrideDisabled", []))
disabled.add("glm-5.2")
ai["modelOverrideDisabled"] = sorted(disabled)

user_models = ai.setdefault("userAddedModels", [])
if "GLM-5.2" not in user_models:
    user_models.append("GLM-5.2")

cur.execute("UPDATE ItemTable SET value = ? WHERE key = ?", (json.dumps(data, separators=(",", ":")), key))
conn.commit()
conn.close()

print(f"openAIBaseUrl: {old_url!r} -> {new_url!r}")
print("composer/agent model -> GLM-5.2")
print("Done.")
'@

$tmp = Join-Path $env:TEMP "fix_cursor_zai_inline.py"
Set-Content -Path $tmp -Value $py -Encoding UTF8
python $tmp
if ($LASTEXITCODE -ne 0) {
  Write-Error "Fix script failed. Restore from backup if needed."
}
Remove-Item $tmp -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Open Cursor"
Write-Host "  2. Settings -> Models -> confirm Override OpenAI Base URL is https://api.z.ai/api/coding/paas/v4"
Write-Host "  3. In Agent chat, select GLM-5.2 (not Auto / Composer 2.5)"
Write-Host "  4. Send a test message"
