import fs from 'node:fs';
import path from 'node:path';

const API = process.env.API_URL ?? 'http://localhost:4000';
const WEB = process.env.WEB_URL ?? 'http://localhost:3000';
const logPath = path.join(process.cwd(), 'debug-acf3ea.log');
const sessionId = 'acf3ea';
const runId = 'milestone-verify';

function log(hypothesisId, message, data) {
  const line = JSON.stringify({
    sessionId,
    runId,
    hypothesisId,
    location: 'verify-milestone.mjs',
    message,
    data,
    timestamp: Date.now(),
  });
  fs.appendFileSync(logPath, `${line}\n`);
  console.log(`${hypothesisId}: ${message}`, data);
}

const checks = [
  {
    id: 'H1',
    name: 'api-health',
    url: `${API}/api/health`,
    expect: (j) => j.services?.api === 'ok',
  },
  {
    id: 'H2',
    name: 'smoke-projects',
    url: `${API}/api/projects`,
    expect: (j) => Array.isArray(j) && j.length >= 1,
  },
  {
    id: 'H3',
    name: 'paper-trading-page',
    url: `${WEB}/paper-trading`,
    expect: (_, text) => text.includes('Close position') || text.includes('Paper Trading'),
  },
  {
    id: 'H4',
    name: 'reset-info',
    url: `${API}/api/paper-trading/reset-info`,
    expect: (j) => j.resetFeeUsd === 50,
  },
  {
    id: 'H5',
    name: 'featured-list',
    url: `${API}/api/projects/featured/list`,
    expect: (j) => Array.isArray(j),
  },
];

let failed = 0;
for (const check of checks) {
  try {
    const res = await fetch(check.url);
    const text = await res.text();
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
    const ok = res.ok && check.expect(body, text);
    log(check.id, check.name, { ok, status: res.status, url: check.url });
    if (!ok) failed += 1;
  } catch (err) {
    failed += 1;
    log(check.id, check.name, {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
      url: check.url,
    });
  }
}

log('SUMMARY', 'milestone verification complete', { failed, total: checks.length, passed: checks.length - failed });
process.exit(failed === 0 ? 0 : 1);
