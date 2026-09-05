#!/usr/bin/env node
/**
 * Mirror canonical showcase bot → btc-signal-engine for CI parity checks.
 * Safe direction: services/btc-conservative-agent/* → services/btc-signal-engine/*
 * Does NOT pull from external bybit_bot.py (use sync-btc-research-bot for that).
 */
import { readFileSync, writeFileSync, existsSync, copyFileSync, readdirSync, rmSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const bot = join(root, 'services/btc-conservative-agent/bot.py');
const engine = join(root, 'services/btc-signal-engine/engine.py');
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
const manifestPath = join(root, 'services/btc-signal-engine/manifest.json');
const agentDir = join(root, 'services/btc-conservative-agent');
const engineDir = join(root, 'services/btc-signal-engine');

function sha256(text) {
  const normalized = text.replace(/\r\n/g, '\n');
  return createHash('sha256').update(normalized, 'utf8').digest('hex').slice(0, 12);
}

function extractEngineVersion(comboSrc) {
  const direct = comboSrc.match(/EXECUTION_FIX_VERSION\s*=\s*"([^"]+)"/);
  if (direct?.[1]) return direct[1];
  const stack = comboSrc.match(/RESEARCH_STACK_VERSION\s*=\s*"([^"]+)"/);
  return stack?.[1] ?? 'unknown';
}

if (!existsSync(bot)) {
  console.error('Missing canonical bot.py');
  process.exit(1);
}

const botSrc = readFileSync(bot, 'utf8');
const botHash = sha256(botSrc);
writeFileSync(engine, botSrc, 'utf8');
console.log(`Mirrored bot.py → engine.py (${botHash})`);

if (existsSync(singletonAgent)) {
  const singletonSrc = readFileSync(singletonAgent, 'utf8');
  writeFileSync(singletonEngine, singletonSrc, 'utf8');
  console.log(`Mirrored process_singleton.py → signal engine (${sha256(singletonSrc)})`);
}

if (existsSync(combosAgent)) {
  const comboSrc = readFileSync(combosAgent, 'utf8');
  writeFileSync(combosEngine, comboSrc, 'utf8');
  console.log(`Mirrored combo_pathway_config.py → combos.py (${sha256(comboSrc)})`);

  const manifest = {
    engine_version: extractEngineVersion(comboSrc),
    combo_version: new Date().toISOString().slice(0, 10),
    exit_version: 'five-family-exits-v1',
    benchmark_lane: 'CONTINUOUS_ANALYTICAL_ONLY',
    signal_hash: botHash,
    source: 'services/btc-conservative-agent/bot.py',
    updated_at: new Date().toISOString(),
  };
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  console.log(`Updated manifest (engine=${manifest.engine_version} hash=${botHash})`);
}

if (!existsSync(inventoryWorkerAgent)) {
  throw new Error('Missing canonical data_sync_inventory_worker.py');
}
copyFileSync(inventoryWorkerAgent, inventoryWorkerEngine);
console.log(`Mirrored data-sync inventory worker (${sha256(readFileSync(inventoryWorkerAgent, 'utf8'))})`);

if (!existsSync(relayEvidenceWorkerAgent)) {
  throw new Error('Missing canonical platform_relay_evidence_worker.py');
}
copyFileSync(relayEvidenceWorkerAgent, relayEvidenceWorkerEngine);
console.log(`Mirrored relay-evidence validation worker (${sha256(readFileSync(relayEvidenceWorkerAgent, 'utf8'))})`);

if (!existsSync(lifecycleCleanupAgent)) {
  throw new Error('Missing canonical lifecycle cleanup transaction dependency');
}
copyFileSync(lifecycleCleanupAgent, lifecycleCleanupEngine);
console.log(`Mirrored lifecycle cleanup transaction (${sha256(readFileSync(lifecycleCleanupAgent, 'utf8'))})`);

for (const [source, target, label] of [
  [rawCleanupAgent, rawCleanupEngine, 'raw generation cleanup transaction'],
  [rawCleanupOwnerAgent, rawCleanupOwnerEngine, 'raw generation cleanup owner'],
  [mirrorLeaseAgent, mirrorLeaseEngine, 'mirror generation lease'],
]) {
  if (!existsSync(source)) throw new Error(`Missing canonical ${label}`);
  mkdirSync(dirname(target), { recursive: true });
  copyFileSync(source, target);
  console.log(`Mirrored ${label} (${sha256(readFileSync(source, 'utf8'))})`);
}

if (!existsSync(rotationAgent)) {
  throw new Error('Missing production rotation orchestrator dependency');
}
copyFileSync(rotationAgent, rotationEngine);
console.log(`Mirrored production rotation orchestrator (${sha256(readFileSync(rotationAgent, 'utf8'))})`);

if (!existsSync(relayOutboxAgent)) {
  throw new Error('Missing durable relay event outbox dependency');
}
copyFileSync(relayOutboxAgent, relayOutboxEngine);
console.log(`Mirrored durable relay event outbox (${sha256(readFileSync(relayOutboxAgent, 'utf8'))})`);

// Registry-owned policy dependencies are part of the executable mirror. Copy
// only the active family modules plus their common implementation and remove
// retired policy files so parity cannot pass with an orphan execution path.
copyFileSync(join(agentDir, 'crash_exception_receipt.py'), join(engineDir, 'crash_exception_receipt.py'));
const activePolicyFiles = [
  'family_policy_common.py',
  'paper_policy_family_atr_target.py',
  'paper_policy_family_atr_trail.py',
  'paper_policy_family_chandelier.py',
  'paper_policy_family_hybrid_runner.py',
  'paper_policy_family_mfe_giveback.py',
];
for (const name of activePolicyFiles) {
  const source = join(agentDir, name);
  if (!existsSync(source)) throw new Error(`Missing registry dependency: ${name}`);
  copyFileSync(source, join(engineDir, name));
}
for (const name of readdirSync(engineDir)) {
  if (name.startsWith('paper_policy_') && !activePolicyFiles.includes(name)) {
    rmSync(join(engineDir, name));
  }
}
console.log(`Mirrored ${activePolicyFiles.length} registry policy dependencies`);

console.log('Done — run npm run verify:signal-parity to confirm.');
