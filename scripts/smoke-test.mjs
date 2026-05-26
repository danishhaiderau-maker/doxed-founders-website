const API_URL = process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:4000';
const base = API_URL.replace(/\/$/, '');

const checks = [
  { name: 'health', path: '/api/health', expect: (j) => j.services?.api === 'ok' },
  {
    name: 'projects',
    path: '/api/projects',
    expect: (j) => Array.isArray(j) && j.length >= 1,
  },
  {
    name: 'featured',
    path: '/api/projects/featured/list',
    expect: (j) => Array.isArray(j),
  },
  {
    name: 'project-detail',
    path: '/api/projects/chainlens',
    expect: (j) => j.slug === 'chainlens',
  },
  {
    name: 'feed',
    path: '/api/feed?filter=recent',
    expect: (j) => Array.isArray(j.posts),
  },
  {
    name: 'reset-info',
    path: '/api/paper-trading/reset-info',
    expect: (j) => j.resetFeeUsd === 50,
  },
];

async function run() {
  console.log(`\n=== Smoke test: ${base} ===\n`);
  let failed = 0;

  for (const check of checks) {
    try {
      const res = await fetch(`${base}${check.path}`);
      const body = await res.json();
      const ok = res.ok && check.expect(body);
      console.log(`${ok ? 'OK  ' : 'FAIL'} ${check.name} (${res.status})`);
      if (!ok) {
        failed += 1;
        console.log('     ', JSON.stringify(body).slice(0, 120));
      }
    } catch (err) {
      failed += 1;
      const message = err instanceof Error ? err.message : String(err);
      console.log(`FAIL ${check.name} — ${message}`);
    }
  }

  console.log(failed === 0 ? '\nAll smoke checks passed.\n' : `\n${failed} check(s) failed.\n`);
  process.exit(failed === 0 ? 0 : 1);
}

run();
