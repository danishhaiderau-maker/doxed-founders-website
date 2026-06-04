/**
 * Probe Phala CVM workload + production capabilities after Railway apply.
 */
const site = (process.env.API_URL || 'https://doxxedcrypto.digital').replace(/\/$/, '');
const base = (process.env.PHALA_CVM_BASE_URL || process.env.PHALA_PROBE_BASE_URL || '').replace(
  /\/$/,
  '',
);

async function getJson(path, headers = {}) {
  const res = await fetch(`${site}${path}`, { headers, signal: AbortSignal.timeout(20_000) });
  return { status: res.status, body: await res.json().catch(() => null) };
}

console.log('\n=== Production API (capabilities) ===\n');
const cap1 = await getJson('/api/vault/cvm-capabilities');
const cap2 = await getJson('/api/vault/cvm-seal-capabilities');
console.log('vault/cvm-capabilities', cap1.status, cap1.body);
console.log('vault/cvm-seal-capabilities', cap2.status, cap2.body);

if (base) {
  const auth = process.env.PHALA_CVM_API_KEY || process.env.PHALA_API_KEY || '';
  console.log('\n=== CVM workload (direct) ===\n');
  const health = await fetch(`${base}/health`, {
    headers: auth ? { Authorization: `Bearer ${auth}` } : {},
    signal: AbortSignal.timeout(20_000),
  });
  console.log('GET', `${base}/health`, health.status, await health.text().catch(() => ''));
} else {
  console.log('\n(Set PHALA_CVM_BASE_URL to probe workload /health directly)\n');
}

const ok =
  cap1.body?.platformCvmConfigured === true && cap2.body?.platformCvmUnwrapConfigured === true;
process.exit(ok ? 0 : 1);
