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
assert.doesNotMatch(
  executableLines(providerFreeRecovery),
  /\bGet-(?:Process|CimInstance)\b|\bWin32_Process\b/,
  "elevated recovery must remain independent of stalled process providers",
);
assert.match(providerFreeRecovery, /Doxed Bot :\$BotPort/);
assert.match(providerFreeRecovery, /ensure-home-bridge\.ps1/);

console.log(
  JSON.stringify({
    ok: true,
    checks: 25,
    guarantees: [
      "recovery paths avoid blocking process providers",
      "cloudflared is enumerated natively and PID-tracked",
      "serialized bridge health probes allow bounded status work",
      "background helper starts are single-owner",
    ],
  }),
);
