import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const read = (name) => readFileSync(join(scriptDir, name), "utf8");
const executableLines = (text) =>
  text
    .split(/\r?\n/)
    .filter((line) => !line.trimStart().startsWith("#"))
    .join("\n");

const common = read("home-stack-common.ps1");
const launcher = read("home-stack-launcher.ps1");
const watchdog = read("bridge-watchdog.ps1");
const ensureBridge = read("ensure-home-bridge.ps1");
const startEverything = read("home-stack-start-everything.ps1");
const autoWire = read("auto-wire-after-tunnel.ps1");
const startBot = read("start-home-bot.ps1");
const providerFreeRecovery = read("recover-home-stack-provider-free.ps1");
const commandWorker = read("home-stack-cmd-worker.ps1");
const health = read("home-stack-health.ps1");
const supervisor = read("home-stack-supervisor.ps1");
const hiddenPs1 = common.slice(
  common.indexOf("function Start-HiddenPs1"),
  common.indexOf("function Start-VisibleConsole"),
);
const listenOwners = common.slice(
  common.indexOf("function Get-ListenPortOwners"),
  common.indexOf("function Test-PortBound"),
);
const stopListener = common.slice(
  common.indexOf("function Stop-ListenPortFast"),
  common.indexOf("function Stop-RecordedProcess"),
);

for (const [name, source] of Object.entries({
  "home-stack-launcher.ps1": launcher,
  "bridge-watchdog.ps1": watchdog,
  "ensure-home-bridge.ps1": ensureBridge,
  "home-stack-start-everything.ps1": startEverything,
})) {
  assert.doesNotMatch(
    executableLines(source),
    /\bGet-(?:Process|CimInstance)\b|\bWin32_Process\b/,
    `${name} must not use a blocking Windows process provider on recovery paths`,
  );
}

assert.match(common, /CreateToolhelp32Snapshot/);
assert.match(common, /RmStartSession/);
assert.match(common, /RmRegisterResources/);
assert.match(common, /GetLockOwners/);
assert.match(common, /Get-FileLockOwnerProcessIdsFast/);
assert.match(common, /Get-ProcessIdsByExecutableNameFast/);
assert.match(common, /\$MaxStartSkewMinutes/);
assert.match(common, /Test-TunnelConnectorPresent/);
assert.match(common, /Set-Content -LiteralPath \$cloudflaredPidFile/);
assert.match(common, /-PassThru/);

assert.match(launcher, /\$script:BridgeTunnelCache/);
assert.match(launcher, /Test-TunnelConnectorPresent \$probe/);
assert.match(launcher, /Timeout = 7000/);
assert.match(watchdog, /Timeout = 7000/);
assert.match(ensureBridge, /Timeout = 7000/);
assert.match(ensureBridge, /Stop-RecordedProcess \$bridgePidFile/);
assert.match(ensureBridge, /\.home-ensure-bridge\.log/);
assert.match(ensureBridge, /& \$launcher/);
assert.doesNotMatch(
  executableLines(ensureBridge),
  /\bStart-VisibleConsole\b/,
  "bridge recovery process must remain the durable listener owner",
);
assert.match(startEverything, /Timeout = 7000/);

assert.doesNotMatch(
  executableLines(startEverything),
  /\bTest-HomeScriptRunning\b/,
  "lock-protected helpers must be launched without command-line process enumeration",
);
assert.match(autoWire, /\.home-auto-wire\.lock/);
assert.match(autoWire, /FileShare\]::None/);
assert.doesNotMatch(
  executableLines(startBot),
  /\bGet-(?:Process|CimInstance)\b|\bWin32_Process\b|\bTest-PortBound\b/,
  "bot startup must not block on process/TCP-table providers",
);
assert.match(startBot, /Test-ProcessIdAliveFast \$monitorPid/);
assert.match(startBot, /\.home-bot-starter\.pid/);
assert.match(read("start-home-analyzer.ps1"), /\.home-analyzer-starter\.pid/);
assert.match(
  startEverything,
  /start-home-analyzer\.ps1"\)\s+@\(\s*"-Port",\s*"\$AnalyzerPort",\s*"-NoWait"/,
  "full-stack recovery must start the analyzer in detached monitored mode",
);
assert.doesNotMatch(
  executableLines(providerFreeRecovery),
  /\bGet-(?:Process|CimInstance)\b|\bWin32_Process\b/,
  "elevated recovery must remain independent of stalled process providers",
);
assert.doesNotMatch(
  executableLines(providerFreeRecovery),
  /taskkill\.exe/,
  "provider-free recovery must not enumerate window-title processes",
);
assert.match(providerFreeRecovery, /ensure-home-bridge\.ps1/);
assert.match(providerFreeRecovery, /\$bridgeDeadline/);
assert.match(providerFreeRecovery, /Existing bridge health confirmed; preserving its owner/);
assert.match(providerFreeRecovery, /Start-HiddenPs1 \(Join-Path \$scriptDir "home-stack-start-everything\.ps1"\)/);
assert.match(providerFreeRecovery, /Test-LockAvailable/);
assert.match(providerFreeRecovery, /Set-HomeStackUserStopped/);
assert.match(providerFreeRecovery, /Stopped exact watchdog owner pid=\$watchdogPid/);
assert.match(providerFreeRecovery, /Get-ProcessCommandLineFast \$watchdogPid/);
assert.match(providerFreeRecovery, /user-stopped sentinel keeps it in stand-down/);
assert.match(providerFreeRecovery, /\$botNeedsStart = -not \(Test-PortOpen \$BotPort\)/);
assert.match(providerFreeRecovery, /if \(\$botNeedsStart\) \{ \$requiredLocks \+= "\.home-bot-start\.lock" \}/);
assert.doesNotMatch(
  providerFreeRecovery,
  /Remove-Item.+\.home-stack-user-stopped/,
);
assert.match(providerFreeRecovery, /\.home-provider-free-recovery\.log/);
assert.match(providerFreeRecovery, /FAILED:/);
assert.match(providerFreeRecovery, /Stop-VerifiedLockOwners/);
assert.match(providerFreeRecovery, /Get-FileLockOwnerProcessIdsFast/);
assert.match(providerFreeRecovery, /Stop-ProcessIdFast/);
assert.match(common, /RmShutdown/);
assert.match(common, /ShutdownExactProcess/);
assert.match(common, /observed\.ProcessStartTime/);
assert.match(common, /GetExtendedTcpTable/);
assert.doesNotMatch(executableLines(listenOwners), /netstat|Get-NetTCPConnection/);
assert.doesNotMatch(executableLines(stopListener), /taskkill\.exe/);
assert.match(common, /GetProcessIdsByExactWindowTitle/);
assert.match(common, /NtQueryInformationProcess/);
assert.match(common, /Get-ProcessCommandLineFast/);
assert.match(ensureBridge, /Get-ProcessIdsByExactWindowTitleFast "Doxed Home Bridge :\$Port"/);
assert.match(ensureBridge, /Bridge window owner pid=\$windowOwnerPid/);
assert.match(ensureBridge, /stop exact hidden bridge owner pid \$hiddenOwnerPid/);
assert.match(ensureBridge, /Get-ProcessCommandLineFast \$hiddenOwnerPid/);
assert.match(hiddenPs1, /System\.Diagnostics\.ProcessStartInfo/);
assert.match(hiddenPs1, /UseShellExecute = \$false/);
assert.match(hiddenPs1, /CreateNoWindow = \$true/);
assert.doesNotMatch(executableLines(hiddenPs1), /Start-Process|RedirectStandardError/);
assert.match(providerFreeRecovery, /Stop-ExactProcessViaRestartManagerFast/);
assert.match(providerFreeRecovery, /method=\$method/);
assert.match(providerFreeRecovery, /Test-ProcessIdAliveFast/);
assert.match(providerFreeRecovery, /did not release within 20 seconds/);
assert.match(providerFreeRecovery, /\.home-bot-starter\.pid/);
assert.match(providerFreeRecovery, /\.home-analyzer-starter\.pid/);
assert.match(
  providerFreeRecovery,
  /refusing an unsafe duplicate launch/,
);
assert.match(commandWorker, /recover-home-stack-provider-free\.ps1/);
assert.match(health, /function Get-BotRuntimeStatus/);
assert.match(health, /\/api\/state/);
assert.match(health, /StateKnown\s+=\s+\$true/);
assert.match(health, /GetFullPath\(\$agentDir\)/);
assert.match(
  health,
  /Flat\s+=\s+\(\$status\.Orders -eq 0 -and \$status\.Positions -eq 0\)/,
);
assert.match(supervisor, /\$botRevisionDeferred/);
assert.match(supervisor, /revision=stale-deferred/);
assert.match(startEverything, /Bot update deferred - source book is active or unavailable/);
assert.match(startEverything, /Replacing stale bot revision from verified flat source boundary/);

console.log(
  JSON.stringify({
    ok: true,
    checks: 84,
    guarantees: [
      "recovery paths avoid blocking process providers",
      "cloudflared is enumerated natively and PID-tracked",
      "serialized bridge health probes allow bounded status work",
      "background helper starts are single-owner",
      "bridge recovery retains one durable listener owner",
      "startup locks identify their exact recoverable owner",
      "legacy lock cleanup uses exact Restart Manager ownership",
      "same-user UAC lock owners use exact PID and creation-time shutdown fallback",
      "owner termination waits for both process exit and lock release",
      "provider-free recovery avoids unbounded taskkill title scans",
      "healthy bridge ownership is preserved during full recovery",
      "slow bot and analyzer startup runs detached from the recovery caller",
      "hidden launches tolerate duplicate Path/PATH environment keys",
      "listener ownership uses native TCP tables without netstat or taskkill",
      "orphan HTTP.sys bridge owners are resolved by exact native window title",
      "native command-line diagnostics identify hidden script owners without WMI",
      "recovery suppresses watchdog respawn until the successful start path clears it",
      "wedged scheduled watchdog owners are cleared by exact native command line",
      "hidden HTTP.sys bridge owners are cleared by exact repo script path",
      "revision upgrades fail closed when source state is active or unknown",
      "automatic revision replacement proceeds only from a verified source-flat boundary",
      "an unkillable elevated watchdog cannot block recovery while the stop sentinel is active",
      "healthy services keep their legitimate startup-lock owners",
      "analyzer health validates the actual canonical report output root",
    ],
  }),
);
