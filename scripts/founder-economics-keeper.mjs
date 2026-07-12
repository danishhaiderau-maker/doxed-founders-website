#!/usr/bin/env node
/**
 * Founder Economics epoch keeper — STUB that fails loudly until production is wired.
 *
 * Production job (after counsel + deploy):
 *   1. Run off-chain DistributionModel for the closed epoch
 *   2. Build Merkle tree of (wallet, amount)
 *   3. Publish root via EpochDistributor.publishRoot (or API → relayer)
 *   4. Optionally call VestingVault.releaseEpoch() when the schedule opens
 *
 * This stub never fakes an on-chain publish. It exits non-zero with the
 * exact next steps so cron/ops cannot silently "succeed".
 *
 * Usage:
 *   node scripts/founder-economics-keeper.mjs
 *   npm run keeper:founder-economics
 *
 * Required for a real run (none of these are set in MVP):
 *   FOUNDER_ECONOMICS_KEEPER_ENABLED=true
 *   FOUNDER_ECONOMICS_RPC_URL=...
 *   FOUNDER_ECONOMICS_DISTRIBUTOR_ADDRESS=0x...
 *   FOUNDER_ECONOMICS_KEEPER_PRIVATE_KEY=...   (ops secret — never commit)
 *   DATABASE_URL=...                          (to load settlement inputs)
 */

const required = [
  'FOUNDER_ECONOMICS_KEEPER_ENABLED',
  'FOUNDER_ECONOMICS_RPC_URL',
  'FOUNDER_ECONOMICS_DISTRIBUTOR_ADDRESS',
  'FOUNDER_ECONOMICS_KEEPER_PRIVATE_KEY',
  'DATABASE_URL',
];

const missing = required.filter((k) => {
  const v = process.env[k];
  if (k === 'FOUNDER_ECONOMICS_KEEPER_ENABLED') {
    return String(v ?? '').toLowerCase() !== 'true';
  }
  return !v || !String(v).trim();
});

console.error('');
console.error('[founder-economics-keeper] REFUSING TO RUN — production keeper is not configured.');
console.error('');
console.error('Founder Economics MVP is off-chain only (DDollar + Merkle proofs in API/UI).');
console.error('On-chain publish is counsel-gated. This script will not fake a deploy or tx.');
console.error('');
console.error('Missing / unset:');
for (const k of missing) {
  console.error(`  - ${k}`);
}
console.error('');
console.error('Next steps:');
console.error('  1. Read docs/FOUNDER-ECONOMICS-MVP-VS-PRODUCTION.md');
console.error('  2. Deploy + audit contracts under services/founder-economics/contracts/');
console.error('  3. Set the env vars above in the ops host (never commit the private key)');
console.error('  4. Re-run this script — a future revision will broadcast publishRoot');
console.error('');
console.error('See also: services/founder-economics/contracts/README.md');
process.exit(2);
