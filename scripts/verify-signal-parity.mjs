#!/usr/bin/env node
/**
 * Signal parity gate — Phase 1 file hash + Phase 2 combo/signal-flag probe.
 */
import { readFileSync, existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { join, dirname, delimiter } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const bot = join(root, 'services/btc-conservative-agent/bot.py');
const engine = join(root, 'services/btc-signal-engine/engine.py');
const manifestPath = join(root, 'services/btc-signal-engine/manifest.json');
const combosAgent = join(root, 'services/btc-conservative-agent/combo_pathway_config.py');
const combosEngine = join(root, 'services/btc-signal-engine/combos.py');
const singletonAgent = join(root, 'services/btc-conservative-agent/process_singleton.py');
const singletonEngine = join(root, 'services/btc-signal-engine/process_singleton.py');
const inventoryWorkerAgent = join(root, 'services/btc-conservative-agent/data_sync_inventory_worker.py');
const inventoryWorkerEngine = join(root, 'services/btc-signal-engine/data_sync_inventory_worker.py');
const relayEvidenceWorkerAgent = join(root, 'services/btc-conservative-agent/platform_relay_evidence_worker.py');
const relayEvidenceWorkerEngine = join(root, 'services/btc-signal-engine/platform_relay_evidence_worker.py');
const lifecycleCleanupAgent = join(root, 'services/btc-conservative-agent/lifecycle_cleanup_transaction.py');
const lifecycleCleanupEngine = join(root, 'services/btc-signal-engine/lifecycle_cleanup_transaction.py');
const rawCleanupAgent = join(root, 'services/btc-conservative-agent/raw_generation_cleanup.py');
const rawCleanupEngine = join(root, 'services/btc-signal-engine/raw_generation_cleanup.py');
const rawCleanupOwnerAgent = join(root, 'services/btc-conservative-agent/raw_generation_cleanup_owner.py');
const rawCleanupOwnerEngine = join(root, 'services/btc-signal-engine/raw_generation_cleanup_owner.py');
const mirrorLeaseAgent = join(root, 'services/btc-conservative-agent/research/mirror_generation_lease.py');
const mirrorLeaseEngine = join(root, 'services/btc-signal-engine/research/mirror_generation_lease.py');
const rotationAgent = join(root, 'services/btc-conservative-agent/production_rotation_orchestrator.py');
const rotationEngine = join(root, 'services/btc-signal-engine/production_rotation_orchestrator.py');
const relayOutboxAgent = join(root, 'services/btc-conservative-agent/relay_event_outbox.py');
const relayOutboxEngine = join(root, 'services/btc-signal-engine/relay_event_outbox.py');
const probe = join(root, 'services/btc-signal-engine/signal_probe.py');
const fixtures = join(root, 'tests/fixtures/signal-parity-cases.json');
const agentDir = join(root, 'services/btc-conservative-agent');
const scenarioConfig = join(agentDir, 'scenario_c_config.py');

function probeEnv(extra = {}) {
  const pythonPath = [
    agentDir,
    join(root, 'services/btc-signal-engine'),
    process.env.PYTHONPATH,
  ]
    .filter(Boolean)
    .join(delimiter);
  return { ...process.env, PYTHONPATH: pythonPath, ...extra };
}

function sha256(path) {
  const text = readFileSync(path, 'utf8').replace(/\r\n/g, '\n');
  return createHash('sha256').update(text, 'utf8').digest('hex').slice(0, 12);
}

function fail(msg) {
  console.error(`PARITY FAIL: ${msg}`);
  process.exit(1);
}

console.log('\n=== Signal parity verify ===\n');

if (!existsSync(bot)) fail('Missing bot.py');
if (!existsSync(engine)) fail('Missing btc-signal-engine/engine.py — run npm run sync:btc-research-bot');

const botHash = sha256(bot);
const engineHash = sha256(engine);
if (botHash !== engineHash) {
  fail(`bot.py (${botHash}) !== engine.py (${engineHash}) — re-run sync`);
}
console.log(`OK  bot.py === engine.py (${botHash})`);

if (existsSync(combosAgent) && existsSync(combosEngine)) {
  const ca = sha256(combosAgent);
  const ce = sha256(combosEngine);
  if (ca !== ce) fail(`combo_pathway_config (${ca}) !== signal-engine/combos.py (${ce})`);
  console.log(`OK  combo configs match (${ca})`);
}

if (!existsSync(singletonAgent) || !existsSync(singletonEngine)) {
  fail('Missing process singleton dependency in canonical bot or signal engine');
}
const singletonAgentHash = sha256(singletonAgent);
const singletonEngineHash = sha256(singletonEngine);
if (singletonAgentHash !== singletonEngineHash) {
  fail(
    `process singleton (${singletonAgentHash}) !== signal-engine copy (${singletonEngineHash})`,
  );
}
console.log(`OK  process singleton dependency matches (${singletonAgentHash})`);

if (!existsSync(inventoryWorkerAgent) || !existsSync(inventoryWorkerEngine)) {
  fail('Missing isolated data-sync inventory worker in canonical bot or signal engine');
}
const inventoryWorkerAgentHash = sha256(inventoryWorkerAgent);
const inventoryWorkerEngineHash = sha256(inventoryWorkerEngine);
if (inventoryWorkerAgentHash !== inventoryWorkerEngineHash) {
  fail(
    `data-sync inventory worker (${inventoryWorkerAgentHash}) !== signal-engine copy (${inventoryWorkerEngineHash})`,
  );
}
console.log(`OK  data-sync inventory worker matches (${inventoryWorkerAgentHash})`);
const quarantineSource = join(root, 'services/btc-conservative-agent/data_sync_quarantine_receipt.py');
const quarantineMirror = join(root, 'services/btc-signal-engine/data_sync_quarantine_receipt.py');
if (!existsSync(quarantineSource) || !existsSync(quarantineMirror) || sha256(quarantineSource) !== sha256(quarantineMirror)) {
  throw new Error('Quarantine receipt helper missing or differs from canonical source');
}

if (!existsSync(relayEvidenceWorkerAgent) || !existsSync(relayEvidenceWorkerEngine)) {
  fail('Missing isolated relay-evidence validation worker in canonical bot or signal engine');
}
const relayEvidenceWorkerAgentHash = sha256(relayEvidenceWorkerAgent);
const relayEvidenceWorkerEngineHash = sha256(relayEvidenceWorkerEngine);
if (relayEvidenceWorkerAgentHash !== relayEvidenceWorkerEngineHash) {
  fail(
    `relay-evidence validation worker (${relayEvidenceWorkerAgentHash}) !== signal-engine copy (${relayEvidenceWorkerEngineHash})`,
  );
}
console.log(`OK  relay-evidence validation worker matches (${relayEvidenceWorkerAgentHash})`);

if (!existsSync(lifecycleCleanupAgent) || !existsSync(lifecycleCleanupEngine)) {
  fail('Missing lifecycle cleanup transaction dependency in canonical bot or signal engine');
}
const lifecycleCleanupAgentHash = sha256(lifecycleCleanupAgent);
const lifecycleCleanupEngineHash = sha256(lifecycleCleanupEngine);
if (lifecycleCleanupAgentHash !== lifecycleCleanupEngineHash) {
  fail(
    `lifecycle cleanup transaction (${lifecycleCleanupAgentHash}) !== signal-engine copy (${lifecycleCleanupEngineHash})`,
  );
}
console.log(`OK  lifecycle cleanup transaction matches (${lifecycleCleanupAgentHash})`);

for (const [canonical, mirror, label] of [
  [rawCleanupAgent, rawCleanupEngine, 'raw generation cleanup transaction'],
  [rawCleanupOwnerAgent, rawCleanupOwnerEngine, 'raw generation cleanup owner'],
  [mirrorLeaseAgent, mirrorLeaseEngine, 'mirror generation lease'],
  [relayOutboxAgent, relayOutboxEngine, 'durable relay event outbox'],
  [join(root, 'services/btc-conservative-agent/crash_exception_receipt.py'),
    join(root, 'services/btc-signal-engine/crash_exception_receipt.py'), 'original crash receipt'],
]) {
  if (!existsSync(canonical) || !existsSync(mirror)) fail(`Missing ${label} dependency`);
  const canonicalHash = sha256(canonical);
  const mirrorHash = sha256(mirror);
  if (canonicalHash !== mirrorHash) fail(`${label} (${canonicalHash}) !== signal-engine copy (${mirrorHash})`);
  console.log(`OK  ${label} matches (${canonicalHash})`);
}

if (!existsSync(rotationAgent) || !existsSync(rotationEngine)) {
  fail('Missing production rotation orchestrator in canonical bot or signal engine');
}
const rotationAgentHash = sha256(rotationAgent);
const rotationEngineHash = sha256(rotationEngine);
if (rotationAgentHash !== rotationEngineHash) {
  fail(
    `production rotation orchestrator (${rotationAgentHash}) !== signal-engine copy (${rotationEngineHash})`,
  );
}
console.log(`OK  production rotation orchestrator matches (${rotationAgentHash})`);

if (existsSync(manifestPath)) {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  if (manifest.signal_hash && manifest.signal_hash !== botHash) {
    fail(`manifest signal_hash ${manifest.signal_hash} !== bot.py ${botHash}`);
  }
  console.log(`OK  manifest engine=${manifest.engine_version} hash=${manifest.signal_hash || botHash}`);
}

const wrapper = join(root, 'services/btc-conservative-agent/btc_conservative_agent.py');
if (!existsSync(wrapper)) fail('Missing btc_conservative_agent.py entry point');
console.log('OK  btc_conservative_agent.py entry point present');

if (!existsSync(fixtures)) fail('Missing tests/fixtures/signal-parity-cases.json');
if (!existsSync(probe)) fail('Missing services/btc-signal-engine/signal_probe.py');
if (!existsSync(scenarioConfig)) {
  fail('Missing canonical scenario_c_config.py dependency for combo fixture probe');
}

console.log('\n--- Phase 2: combo fixture probe ---\n');
try {
  execSync(`python "${probe}"`, {
    cwd: root,
    stdio: 'inherit',
    encoding: 'utf8',
    env: probeEnv(),
  });
} catch {
  fail('signal_probe.py failed');
}

const fullProbe = process.argv.includes('--full');
if (fullProbe) {
  console.log('\n--- Phase 2b: signal-flag probe (imports bot.py) ---\n');
  const probeWorkingDir = mkdtempSync(join(tmpdir(), 'dcf-signal-parity-'));
  let fullProbeFailed = false;
  try {
    execSync(`python "${probe}" --full`, {
      // bot.py still owns legacy runtime defaults at import time.  Keep the
      // characterization probe hermetic so it cannot leave config/log/archive
      // artifacts in the repository merely because CI imported the module.
      cwd: probeWorkingDir,
      stdio: 'inherit',
      encoding: 'utf8',
      timeout: 120_000,
      env: probeEnv({
        FORCE_PAPER_MODE: '1',
        RESEARCH_DATA_COLLECTION: '1',
        SKIP_EXCHANGE_MARKET_LOAD: '1',
      }),
    });
  } catch {
    fullProbeFailed = true;
  } finally {
    rmSync(probeWorkingDir, { recursive: true, force: true });
  }
  if (fullProbeFailed) fail('signal_probe.py --full failed');
} else {
  console.log('Tip: npm run verify:signal-parity -- --full for bot import flag parity\n');
}

console.log('All parity checks passed.\n');
