const API_URL = process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:4000';
const base = API_URL.replace(/\/$/, '');

async function fetchJson(path) {
  const res = await fetch(`${base}${path}`);
  const body = await res.json().catch(() => null);
  return { res, body };
}

async function run() {
  console.log(`\n=== Smoke test: ${base} ===\n`);
  let failed = 0;

  const runCheck = async (name, fn) => {
    try {
      const ok = await fn();
      console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}`);
      if (!ok) failed += 1;
    } catch (err) {
      failed += 1;
      const message = err instanceof Error ? err.message : String(err);
      console.log(`FAIL ${name} — ${message}`);
    }
  };

  await runCheck('health', async () => {
    const { res, body } = await fetchJson('/api/health');
    return res.ok && body?.services?.api === 'ok' && body?.services?.database === 'ok';
  });

  await runCheck('projects', async () => {
    const { res, body } = await fetchJson('/api/projects');
    return res.ok && Array.isArray(body) && body.length >= 1;
  });

  await runCheck('featured', async () => {
    const { res, body } = await fetchJson('/api/projects/featured/list');
    return res.ok && Array.isArray(body);
  });

  await runCheck('project-detail', async () => {
    const { body: projects } = await fetchJson('/api/projects');
    const slug = projects?.[0]?.slug;
    if (!slug) return false;
    const { res, body } = await fetchJson(`/api/projects/${slug}`);
    return res.ok && body?.slug === slug;
  });

  await runCheck('feed', async () => {
    const { res, body } = await fetchJson('/api/feed?filter=recent');
    return res.ok && Array.isArray(body?.posts);
  });

  await runCheck('unified-feed', async () => {
    const { res, body } = await fetchJson('/api/feed/unified?limit=3');
    return res.ok && Array.isArray(body?.items);
  });

  await runCheck('feed-hub', async () => {
    const { res, body } = await fetchJson('/api/feed/hub?category=all&limit=10');
    return res.ok && Array.isArray(body?.stream) && typeof body?.counts?.merged === 'number';
  });

  await runCheck('discover-universe', async () => {
    const { res, body } = await fetchJson('/api/founder-den/discover/universe?timeframe=24h');
    return (
      res.ok &&
      Array.isArray(body?.projects) &&
      (body.projects.length === 0 ||
        (body.projects[0].universeStage &&
          ['building', 'validation', 'live'].includes(body.projects[0].universeStage)))
    );
  });

  await runCheck('platform-pulse', async () => {
    const { res, body } = await fetchJson('/api/feed/pulse');
    return res.ok && Array.isArray(body);
  });

  await runCheck('adoption-metrics', async () => {
    const { res, body } = await fetchJson('/api/projects/platform/adoption-metrics?days=14');
    return res.ok && Array.isArray(body?.series) && body.series.length >= 7;
  });

  await runCheck('reputation-leaderboard', async () => {
    const { res, body } = await fetchJson('/api/reputation/leaderboard?limit=5');
    return res.ok && Array.isArray(body?.entries);
  });

  await runCheck('account-api (auth required)', async () => {
    const res = await fetch(`${base}/api/account/overview`);
    return res.status === 401;
  });

  await runCheck('cursor-api (auth required)', async () => {
    const res = await fetch(`${base}/api/builder/providers/cursor-connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    return res.status === 401;
  });

  await runCheck('prediction-markets', async () => {
    const { res, body } = await fetchJson('/api/prediction-markets');
    return res.ok && Array.isArray(body);
  });

  await runCheck('reset-info', async () => {
    const { res, body } = await fetchJson('/api/paper-trading/reset-info');
    return res.ok && typeof body?.resetFeeUsd === 'number' && body.resetFeeUsd > 0;
  });

  console.log(failed === 0 ? '\nAll smoke checks passed.\n' : `\n${failed} check(s) failed.\n`);
  process.exit(failed === 0 ? 0 : 1);
}

run();
