import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const monitorPath = new URL('../.github/workflows/fly-bot-monitor.yml', import.meta.url);
const deployPath = new URL('../.github/workflows/auto-deploy.yml', import.meta.url);
const flyDeployPath = new URL(
  '../.github/workflows/fly-bot-deploy.yml',
  import.meta.url,
);
const wrapperPath = new URL(
  '../services/btc-conservative-agent/btc_conservative_agent.py',
  import.meta.url,
);
const railwayConfigPath = new URL(
  '../services/btc-conservative-agent/railway.toml',
  import.meta.url,
);
const flyConfigPath = new URL(
  '../services/btc-conservative-agent/fly.toml',
  import.meta.url,
);
const retiredProvisionerPath = new URL('./ensure-btc-bot-public-url.mjs', import.meta.url);
const retiredHomeFinishPath = new URL('./finish-home-production.mjs', import.meta.url);
const patcherPath = new URL('./patch-btc-bot-production.mjs', import.meta.url);
const fullSyncPath = new URL('./sync-all-production.mjs', import.meta.url);
const wirePath = new URL('./wire-home-bot-url.mjs', import.meta.url);
const saveUrlPath = new URL('./save-showcase-bot-url.mjs', import.meta.url);
const credentialPushPath = new URL('./push-showcase-bot-credentials.mjs', import.meta.url);
const railwayControlPath = new URL('./railway-showcase-control.mjs', import.meta.url);
const printHomeEnvPath = new URL('./print-home-bot-env.mjs', import.meta.url);
const localLabPath = new URL('./home-stack-local-lab.ps1', import.meta.url);
const homeModePath = new URL('./home-stack-mode.ps1', import.meta.url);
const directStartPath = new URL(
  '../services/btc-conservative-agent/start.ps1',
  import.meta.url,
);
const homeShowcaseLockPath = new URL('../config/home-showcase.lock.json', import.meta.url);
const localCollectionLockPath = new URL(
  '../config/local-collection.lock.json',
  import.meta.url,
);
const architectureLockPath = new URL(
  '../config/bot-architecture.lock.json',
  import.meta.url,
);
const migrationGuidePath = new URL('../docs/fly-migration-guide.md', import.meta.url);
const dashboardProxyPath = new URL('./fly-dashboard-proxy.py', import.meta.url);
const desktopMirrorPath = new URL('./start-fly-desktop-mirror.ps1', import.meta.url);
const flySyncLoopPath = new URL('./sync-fly-bot-data-loop.ps1', import.meta.url);
const flySyncPath = new URL('./sync-fly-bot-data.ps1', import.meta.url);
const flyDataPathsPath = new URL('./fly-data-paths.ps1', import.meta.url);
const flyMirrorMigrationPath = new URL('./migrate-fly-mirror-to-local.ps1', import.meta.url);
const analyzerAutoRestartPath = new URL('./analyzer-auto-restart.ps1', import.meta.url);
const botSourcePath = new URL(
  '../services/btc-conservative-agent/bot.py',
  import.meta.url,
);
const flyDockerfilePath = new URL(
  '../services/btc-conservative-agent/Dockerfile',
  import.meta.url,
);
const flyDockerignorePath = new URL(
  '../services/btc-conservative-agent/.dockerignore',
  import.meta.url,
);
const flyLockHelperPath = new URL('./fly-canonical-lock.ps1', import.meta.url);
const flyDeployHelperPath = new URL('./deploy-fly-btc-bot.ps1', import.meta.url);
const homeLauncherPath = new URL('./home-stack-launcher.ps1', import.meta.url);
const fastRecoverPath = new URL('./fast-recover-global.ps1', import.meta.url);
const overnightGuardPath = new URL(
  './overnight-architecture-guard.ps1',
  import.meta.url,
);

test('Fly monitor compares against the latest bot-source revision', async () => {
  const workflow = await readFile(monitorPath, 'utf8');

  assert.match(workflow, /fetch-depth:\s*0/);
  assert.match(
    workflow,
    /git log -1 --format=%H --[\s\S]*services\/btc-conservative-agent[\s\S]*scripts\/check-relay-flat\.mjs[\s\S]*\.github\/workflows\/fly-bot-deploy\.yml/,
  );
  assert.match(
    workflow,
    /EXPECTED_REVISION:\s*\$\{\{\s*steps\.expected\.outputs\.revision\s*\}\}/,
  );
  assert.doesNotMatch(workflow, /EXPECTED_REVISION:\s*\$\{\{\s*github\.sha\s*\}\}/);
  assert.match(workflow, /re\.fullmatch\(r"\[0-9a-f\]\{7,40\}", reported\)/);
  assert.match(workflow, /"git",\s*"rev-parse",\s*"--verify",\s*f"\{reported\}\^\{\{commit\}\}"/);
  assert.match(workflow, /merge-base",\s*"--is-ancestor",\s*required,\s*actual/);
  assert.match(workflow, /merge-base",\s*"--is-ancestor",\s*actual,\s*"HEAD"/);
  assert.doesNotMatch(workflow, /actual\.startswith\(expected\)/);
});

test('flat-boundary proof targets the canonical Fly owner', async () => {
  const workflow = await readFile(deployPath, 'utf8');

  assert.match(
    workflow,
    /SHOWCASE_OWNER_URL:\s*https:\/\/doxed-btc-bot\.fly\.dev/,
  );
  assert.doesNotMatch(
    workflow,
    /SHOWCASE_OWNER_URL:\s*https:\/\/bot\.doxxedcrypto\.digital/,
  );
});

test('manual Fly deployment is pinned to the BTC service context and flat boundary', async () => {
  const helper = await readFile(flyDeployHelperPath, 'utf8');

  assert.match(helper, /services\\btc-conservative-agent/);
  assert.match(helper, /Push-Location \$serviceRoot/);
  assert.match(helper, /check-relay-flat\.mjs/);
  assert.match(helper, /REQUIRE_CANONICAL_FLY_OWNER = "YES"/);
  assert.match(helper, /SOURCE_GIT_REV=\$revision/);
  assert.match(helper, /source_git_rev/);
  assert.match(helper, /live_armed -eq \$false/);
  assert.match(helper, /force_paper_mode -eq \$true/);
  assert.doesNotMatch(helper, /Push-Location \$repoRoot/);
});

test('Fly deploy proves a disarmed paper-signal owner, never a direct live executor', async () => {
  const workflow = await readFile(flyDeployPath, 'utf8');

  assert.match(workflow, /health\.get\("live_armed"\) is False/);
  assert.match(workflow, /health\.get\("bitfinex_live_enabled"\) is False/);
  assert.match(workflow, /health\.get\("force_paper_mode"\) is True/);
  assert.match(workflow, /paper-signal-only/);
  assert.match(workflow, /python test_paper_mode_private_api_isolation\.py/);
  assert.match(workflow, /python test_showcase_manual_close\.py/);
});

test('Fly image includes and imports every root runtime research module', async () => {
  const dockerfile = await readFile(flyDockerfilePath, 'utf8');
  const dockerignore = await readFile(flyDockerignorePath, 'utf8');
  const requiredModules = [
    'analysis_eligibility',
    'platform_relay_evidence',
    'source_market_evidence',
    'immutable_archive',
    'counterfactual_normalization',
  ];

  for (const moduleName of requiredModules) {
    assert.match(dockerignore, new RegExp(`!research/${moduleName}\\.py`));
    assert.match(dockerfile, new RegExp(`research\\.${moduleName}`));
  }
});

test('Fly routes on process liveness while strategy readiness stays separate', async () => {
  const config = await readFile(flyConfigPath, 'utf8');

  assert.match(config, /path\s*=\s*"\/health"/);
  assert.doesNotMatch(config, /path\s*=\s*"\/ready"/);
});

test('Fly runbook never provisions private Bitfinex execution credentials', async () => {
  const guide = await readFile(migrationGuidePath, 'utf8');
  const secretsSection = guide.match(
    /### 4\. Set the paper-signal owner's secrets on Fly[\s\S]*?(?=### 5\.)/,
  )?.[0] ?? '';

  assert.match(secretsSection, /FORCE_PAPER_MODE=true/);
  assert.match(secretsSection, /BITFINEX_LIVE_ENABLED=false/);
  assert.match(secretsSection, /permanently paper-signal-only/i);
  assert.match(secretsSection, /Railway's isolated subscriber executor/i);
  assert.doesNotMatch(secretsSection, /BITFINEX_API_KEY=\.\.\./);
  assert.doesNotMatch(secretsSection, /BITFINEX_API_SECRET=\.\.\./);
});

test('production wrapper refuses every non-Fly runtime before importing bot.py', async () => {
  const wrapper = await readFile(wrapperPath, 'utf8');
  const guard = wrapper.indexOf('REFUSED_NON_FLY_RUNTIME');
  const botImport = wrapper.indexOf('import bot as signal_engine');

  assert.ok(guard >= 0, 'non-Fly refusal must be present');
  assert.ok(botImport > guard, 'non-Fly refusal must run before bot.py import');
  assert.match(wrapper, /raise SystemExit\(78\)/);
  assert.match(wrapper, /os\.environ\["HOME_BOT_LOCAL"\]\s*=\s*"0"/);
  assert.match(wrapper, /os\.environ\["HOME_RESEARCH_FULL"\]\s*=\s*"1"/);
  assert.match(
    wrapper,
    /if os\.environ\.get\("EXECUTION_MIRROR_ONLY"[\s\S]*os\.environ\["HOME_RESEARCH_FULL"\]\s*=\s*"0"[\s\S]*os\.environ\["BLOCK_RESEARCH_WAREHOUSE"\]\s*=\s*"1"/,
  );
  assert.match(wrapper, /else:[\s\S]*os\.environ\["BLOCK_RESEARCH_WAREHOUSE"\]\s*=\s*"0"/);

  const python = process.platform === 'win32' ? 'python.exe' : 'python';
  const result = spawnSync(python, [fileURLToPath(wrapperPath)], {
    encoding: 'utf8',
    env: {
      ...process.env,
      FLY_APP_NAME: '',
      FLY_MACHINE_ID: '',
      FLY_ALLOC_ID: '',
      FLY_REGION: '',
      HOME_BOT_LOCAL: '',
      HOME_RESEARCH_FULL: '',
    },
  });
  assert.equal(result.status, 78);
  assert.match(result.stderr, /REFUSED_NON_FLY_RUNTIME/);
});

test('retired Railway and home-owner commands are non-mutating and fail closed', async () => {
  const [railway, provisioner, finishHome, fullSync, overnightGuard] = await Promise.all([
    readFile(railwayConfigPath, 'utf8'),
    readFile(retiredProvisionerPath, 'utf8'),
    readFile(retiredHomeFinishPath, 'utf8'),
    readFile(fullSyncPath, 'utf8'),
    readFile(overnightGuardPath, 'utf8'),
  ]);

  assert.match(railway, /restartPolicyType\s*=\s*"NEVER"/);
  assert.doesNotMatch(provisioner, /backboard\.railway\.com|serviceCreate|serviceInstanceRedeploy/);
  assert.match(provisioner, /performs no network or[\s\S]*platform mutation/);
  assert.match(finishHome, /fast-recover-global\.ps1/);
  assert.doesNotMatch(
    finishHome,
    /cloudflared|wire:home-bot|sync:all|setup:named-tunnel|git push/i,
  );
  assert.match(fullSync, /Canonical Fly bot URL lock \(read-only\)/);
  assert.match(fullSync, /Railway API service only/);
  assert.doesNotMatch(fullSync, /Home bot mode/);
  assert.ok(
    overnightGuard.indexOf('REFUSED_LEGACY_OVERNIGHT_GUARD')
      < overnightGuard.indexOf('npm run wire:home-bot'),
    'retired overnight guard must fail before any legacy external mutation',
  );
  assert.match(overnightGuard, /exit 78/);
});

test('legacy source patcher is protected by the reviewed replacement lock', async () => {
  const patcher = await readFile(patcherPath, 'utf8');

  assert.match(patcher, /assertBotSyncAllowed/);
  assert.match(patcher, /BTC_BOT_PATCH_PROBE_ONLY/);
  assert.match(patcher, /temporary probe/);
  assert.match(patcher, /canonical bot\.py/);
});

test('legacy URL, credential, Railway, and local-lab controls cannot create another owner', async () => {
  const [
    wire,
    saveUrl,
    credentialPush,
    railwayControl,
    printHomeEnv,
    localLab,
    homeMode,
    directStart,
  ] = await Promise.all([
    readFile(wirePath, 'utf8'),
    readFile(saveUrlPath, 'utf8'),
    readFile(credentialPushPath, 'utf8'),
    readFile(railwayControlPath, 'utf8'),
    readFile(printHomeEnvPath, 'utf8'),
    readFile(localLabPath, 'utf8'),
    readFile(homeModePath, 'utf8'),
    readFile(directStartPath, 'utf8'),
  ]);

  assert.ok(
    wire.indexOf('assertCanonicalFlyBotUrl') < wire.indexOf('new PrismaClient'),
    'URL lock must run before database/platform mutation',
  );
  assert.match(saveUrl, /assertCanonicalFlyBotUrl/);
  assert.doesNotMatch(saveUrl, /btc-conservative-agent-production\.up\.railway\.app/);
  assert.match(credentialPush, /Railway showcase credential push is disabled/);
  assert.doesNotMatch(credentialPush, /backboard\.railway\.com|serviceInstanceRedeploy/);
  assert.match(railwayControl, /railwayBotControl:\s*'disabled'/);
  assert.doesNotMatch(railwayControl, /deploymentRestart|serviceInstanceRedeploy/);
  assert.match(printHomeEnv, /desktop strategy environment export is disabled/);
  assert.doesNotMatch(
    printHomeEnv,
    /decryptShowcaseSecret|writeFileSync\(|python bot\.py/,
  );
  assert.match(localLab, /Local strategy lab is disabled/);
  assert.doesNotMatch(localLab, /Start-Process|start_stack\.ps1/);
  assert.match(homeMode, /Mode = "fly-mirror"/);
  assert.match(homeMode, /LocalStrategyEnabled = \$false/);
  assert.doesNotMatch(directStart, /python\s+bot\.py/i);
  assert.match(directStart, /REFUSED_NON_FLY_RUNTIME/);
});

test('legacy lock files describe mirrors only and cannot claim production ownership', async () => {
  const [home, local, architecture] = await Promise.all([
    readFile(homeShowcaseLockPath, 'utf8').then(JSON.parse),
    readFile(localCollectionLockPath, 'utf8').then(JSON.parse),
    readFile(architectureLockPath, 'utf8').then(JSON.parse),
  ]);

  assert.equal(home.mode, 'fly-mirror');
  assert.equal(home.authoritative, false);
  assert.equal(home.disableLocalStrategy, true);
  assert.equal(home.disableTunnel, true);
  assert.equal(local.enabled, false);
  assert.equal(local.disableLocalStrategy, true);
  assert.equal(local.doNotUsePorts.includes(local.botPort), false);
  assert.equal(local.doNotUsePorts.includes(local.analyzerPort), false);
  assert.equal(
    architecture.canonicalSource.processEntrypoint,
    'btc_conservative_agent.py',
  );
  assert.equal(architecture.canonicalSource.strategyModule, 'bot.py');
  assert.equal('entrypoint' in architecture.canonicalSource, false);
  assert.equal(architecture.runtimeRoles.fly.liveExecutionOwner, false);
  assert.equal(architecture.runtimeRoles.fly.privateExchangeOwner, false);
  assert.equal(architecture.runtimeRoles.railway.liveExecutionOwner, true);
  assert.equal(architecture.runtimeRoles.railway.privateExchangeOwner, true);
  assert.equal(architecture.runtimeRoles.desktop.liveExecutionOwner, false);
});

test('desktop Fly mirror is loopback-only and cannot proxy money-path mutations', async () => {
  const [proxy, launcher, syncLoop, sync, helper] = await Promise.all([
    readFile(dashboardProxyPath, 'utf8'),
    readFile(desktopMirrorPath, 'utf8'),
    readFile(flySyncLoopPath, 'utf8'),
    readFile(flySyncPath, 'utf8'),
    readFile(flyLockHelperPath, 'utf8'),
  ]);

  assert.match(proxy, /parser\.add_argument\("--bind", default="127\.0\.0\.1"\)/);
  assert.match(proxy, /REFUSED_NON_LOOPBACK_BIND/);
  assert.match(proxy, /REFUSED_NON_CANONICAL_UPSTREAM/);
  assert.match(proxy, /desktop_mirror_read_only/);
  assert.match(proxy, /loopback_response_header/);
  assert.match(proxy, /set-cookie/);
  assert.match(proxy, /Secure/);
  assert.doesNotMatch(proxy, /"\/api\/live_arm"[\s\S]*MIRROR_MUTATION_ALLOWLIST/);
  assert.doesNotMatch(proxy, /"\/api\/bitfinex_live"[\s\S]*MIRROR_MUTATION_ALLOWLIST/);
  assert.doesNotMatch(proxy, /"\/api\/positions\/close"[\s\S]*MIRROR_MUTATION_ALLOWLIST/);
  assert.match(launcher, /--bind 127\.0\.0\.1/);
  for (const script of [launcher, syncLoop, sync]) {
    assert.match(script, /fly-canonical-lock\.ps1/);
    assert.match(script, /Get-CanonicalFlyBotUrl -RequestedUrl \$SourceUrl/);
  }
  assert.match(helper, /doxed-btc-bot\.fly\.dev/);
  assert.match(helper, /REFUSED_NON_CANONICAL_UPSTREAM/);
});

test('desktop Start and Reset routes manage the mirror only', async () => {
  const launcher = await readFile(homeLauncherPath, 'utf8');

  assert.match(
    launcher,
    /"\^\/cmd\/start-mirror\$"\s*\{\s*Invoke-HomeCommand "start-mirror"/,
  );
  assert.match(
    launcher,
    /"\^\/cmd\/reset-mirror\$"\s*\{\s*Invoke-HomeCommand "reset-mirror"/,
  );
  assert.match(launcher, /function Invoke-ResetFlyDesktopMirror/);
  assert.match(launcher, /Stop-RecordedMirrorProcess "\.fly-dashboard-proxy\.pid"/);
  assert.match(launcher, /Stop-RecordedMirrorProcess "\.fly-data-sync-loop\.lock"/);
  assert.doesNotMatch(
    launcher.match(/function Invoke-ResetFlyDesktopMirror[\s\S]*?^}/m)?.[0] ?? '',
    /fly-control|api\.machines\.dev|stop-bot|start-bot/,
  );
});

test('desktop recovery rejects zombie mirror processes and restores watchdog ownership', async () => {
  const launcher = await readFile(desktopMirrorPath, 'utf8');
  const recovery = await readFile(fastRecoverPath, 'utf8');
  const syncLoop = await readFile(flySyncLoopPath, 'utf8');
  const sync = await readFile(flySyncPath, 'utf8');

  assert.match(launcher, /syncHeartbeatMaxAgeSec\s*=\s*600/);
  assert.match(launcher, /LastWriteTimeUtc/);
  assert.match(launcher, /Stop-Process -Id \$syncPid -Force/);
  assert.match(launcher, /Get-NetTCPConnection[\s\S]*LocalPort 7002/);
  assert.match(launcher, /X-Desktop-Mirror/);
  assert.match(launcher, /proxyEndpointAlive/);
  assert.match(launcher, /unowned listener\(s\)/);
  assert.match(syncLoop, /FileShare\]::None/);
  assert.match(syncLoop, /\.fly-data-sync-loop\.guard/);
  assert.match(
    syncLoop,
    /reason = "below_threshold"[\s\S]*sourceRevision = \$\(if \(\$manifest\.PSObject\.Properties\.Name -contains "source_git_rev"\)/,
  );
  assert.match(
    syncLoop,
    /sourceRevision = \$\(if \(\$result\.SourceRevision\)[\s\S]*\$manifest\.source_git_rev/,
  );
  assert.match(sync, /\$statePath\.\$PID\.\$\(\[guid\]::NewGuid/);
  assert.match(sync, /\[System\.IO\.File\]::Replace\(\$stateTmp, \$statePath/);
  assert.match(sync, /\$stateBackup\s*=\s*"\$stateTmp\.bak"/);
  assert.match(sync, /Remove-Item -LiteralPath \$stateBackup/);
  assert.doesNotMatch(sync, /Move-Item -LiteralPath \$stateTmp -Destination \$statePath -Force/);
  assert.match(recovery, /Clear-HomeStackUserStopped/);
  assert.match(recovery, /ensure-home-bridge\.ps1/);
  assert.match(
    recovery,
    /Desktop command bridge did not become reachable on :\$bridgePort/,
  );
});

test('raw Fly evidence defaults to machine-local storage and migration is copy-only', async () => {
  const paths = await readFile(flyDataPathsPath, 'utf8');
  const syncLoop = await readFile(flySyncLoopPath, 'utf8');
  const sync = await readFile(flySyncPath, 'utf8');
  const migration = await readFile(flyMirrorMigrationPath, 'utf8');
  const homeMode = await readFile(homeModePath, 'utf8');

  assert.match(paths, /DOXXED_FLY_MIRROR_DIR/);
  assert.match(paths, /LOCALAPPDATA/);
  assert.match(paths, /DoxxedCrypto\\fly-data-mirror/);
  assert.match(syncLoop, /Get-DoxxedFlyMirrorDir/);
  assert.match(syncLoop, /syncArgs\.TargetDir = \$mirrorDir/);
  assert.match(syncLoop, /Import-HomeBotVaultConfig -VaultEnvPath \$vaultEnv/);
  assert.doesNotMatch(syncLoop, /if \(-not \$env:BOT_ADMIN_TOKEN -and \(Test-Path -LiteralPath \$vaultEnv\)\)/);
  assert.match(sync, /Get-DoxxedFlyMirrorDir/);
  assert.match(sync, /home-bot-vault-env.ps1/);
  assert.match(sync, /Import-CanonicalBotAdminToken/);
  assert.match(homeMode, /DataDir = Get-DoxxedFlyMirrorDir/);
  assert.match(migration, /Get-FileHash[\s\S]*SHA256/);
  assert.match(migration, /SourceRetained = \$true/);
  assert.doesNotMatch(migration, /Remove-Item|Move-Item/);
});

test('retired analyzer restart monitor cannot create a second owner', async () => {
  const monitor = await readFile(analyzerAutoRestartPath, 'utf8');

  assert.match(monitor, /disabled fail-closed/);
  assert.doesNotMatch(monitor, /Start-Process|Set-Content|Remove-Item/);
});

test('Fly process heartbeat cannot be blocked by the five-minute AI pipeline', async () => {
  const bot = await readFile(botSourcePath, 'utf8');

  assert.match(bot, /PROCESS_HEARTBEAT_INTERVAL_SEC[\s\S]*"5"/);
  assert.match(bot, /def heartbeat_loop\(\):[\s\S]*shutdown_event\.wait\(PROCESS_HEARTBEAT_INTERVAL_SEC\)/);
  assert.match(bot, /def periodic_pipeline_loop\(\):[\s\S]*shutdown_event\.wait\(HEARTBEAT_INTERVAL\)/);
  assert.match(bot, /target=safe_thread\(heartbeat_loop\)/);
  assert.match(bot, /target=safe_thread\(periodic_pipeline_loop\)/);
});
