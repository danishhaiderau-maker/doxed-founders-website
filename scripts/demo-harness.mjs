#!/usr/bin/env node
/**
 * End-to-end demo harness orchestrator.
 *
 * One command that proves the entire doxedcryptofounder platform works:
 *   - Platform: Founder OS / Raise Room / DDollar / Trust Center / founder journey
 *   - Bot: BTC conservative agent (:7002) in sim/LAB-shadow mode
 *   - Analyzer: report_manifest + new v1 reports
 *   - Genome: decision/environment/market JSONL + research.db
 *   - Relay: APPROVE_PENDING -> ORDER_PLACED -> POSITION_CLOSED round-trip
 *   - AI: DeepSeek verdicts (cassette-replayed by default; capture via DEMO_CAPTURE=1)
 *   - Stress: peak RPS, p95 latency, DDollar two-ledger invariant
 *
 * Output: logs/demo/demo-report.{json,md} with a readiness score 0-100.
 *
 * Usage:
 *   node scripts/demo-harness.mjs                    # full harness, replay mode
 *   node scripts/demo-harness.mjs --stress           # include stress phase
 *   node scripts/demo-harness.mjs --skip-bot         # attach to running bot
 *   node scripts/demo-harness.mjs --capture          # refresh cassettes from real APIs
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const REPO_ROOT = resolve(__dirname, '..');
const LOG_DIR = join(REPO_ROOT, 'logs', 'demo');
const BOT_DIR = join(REPO_ROOT, 'services', 'btc-conservative-agent');
const CASSETTE_DIR = join(REPO_ROOT, 'cassettes');

const argv = process.argv.slice(2);
const WANT_STRESS = argv.includes('--stress');
const SKIP_BOT = argv.includes('--skip-bot');
const SKIP_RELAY_REPLAY = argv.includes('--skip-relay');
const CAPTURE = argv.includes('--capture') || process.env.DEMO_CAPTURE === '1';
const SKIP_API = argv.includes('--skip-api');
const HELP = argv.includes('--help') || argv.includes('-h');

if (HELP) {
  console.log(`Usage: node scripts/demo-harness.mjs [options]

Options:
  --stress         Include the stress phase (peak RPS, p95 latency, DDollar invariant).
  --skip-bot       Attach to a bot already running on :7002 instead of starting one.
  --skip-relay     Skip replaying relay cassettes (e.g. if the API is read-only).
  --skip-api       Attach to an already-running API + bot; only run the harness probe.
  --capture        Refresh cassettes from real DeepSeek + Bitfinex + relay (DEMO_CAPTURE=1).
  --help, -h       Show this help.

Env:
  DEMO_API_URL           API base URL (default http://127.0.0.1:4000)
  DEMO_BOT_URL           Bot base URL (default http://127.0.0.1:7002)
  DEMO_HARNESS_TOKEN     Shared secret for the internal harness route (REQUIRED for the API call)
  DEMO_SEED_SCALE        small | medium | large | xlarge (default small — fast)
  DEMO_STRESS_RPS        Stress RPS target (default 20)
  DEMO_STRESS_DURATION_S Stress duration seconds (default 10)
`);
  process.exit(0);
}

const env = { ...process.env };
env.DEMO_MODE_ENABLED = 'true';
env.LIVE_TRADING_ENABLED = 'False';
env.FUNDING_SIMULATION_ENABLED = 'True';
env.DEMO_CASSETTE_MODE = CAPTURE ? 'capture' : 'replay';
env.DEMO_RUN_STARTED_MS = String(Date.now());
env.DEMO_BOT_CWD = env.DEMO_BOT_CWD || BOT_DIR;
env.DEMO_REPORT_DIR = env.DEMO_REPORT_DIR || LOG_DIR;
if (!env.DEMO_BOT_URL) env.DEMO_BOT_URL = 'http://127.0.0.1:7002';
if (!env.DEMO_API_URL) env.DEMO_API_URL = 'http://127.0.0.1:4000';
if (!env.DEMO_SEED_SCALE) env.DEMO_SEED_SCALE = 'small';

if (String(env.LIVE_TRADING_ENABLED).toLowerCase() === 'true') {
  console.error('\n[FATAL] LIVE_TRADING_ENABLED=true — refusing to start the demo harness.\n' +
    'Set LIVE_TRADING_ENABLED=False (the orchestrator enforces this, but an external\n' +
    'shell override was detected). Aborting.\n');
  process.exit(2);
}

const lockPath = join(REPO_ROOT, 'config', 'bot-architecture.lock.json');
if (existsSync(lockPath)) {
  const lock = JSON.parse(readFileSync(lockPath, 'utf8'));
  if (lock.allowBluntSync === true) {
    console.error('\n[FATAL] config/bot-architecture.lock.json has allowBluntSync=true.\n' +
      'The demo harness refuses to run in this state — the lock is the safety contract.\n');
    process.exit(2);
  }
}

mkdirSync(LOG_DIR, { recursive: true });

const API_URL = env.DEMO_API_URL.replace(/\/$/, '');
const BOT_URL = env.DEMO_BOT_URL.replace(/\/$/, '');
const HARNESS_TOKEN = env.DEMO_HARNESS_TOKEN || '';
const PHASE = (label) => console.log(`\n[demo-harness] === ${label} ===`);

async function main() {
  const t0 = Date.now();
  console.log(`[demo-harness] repo=${REPO_ROOT}`);
  console.log(`[demo-harness] api=${API_URL} bot=${BOT_URL}`);
  console.log(`[demo-harness] cassette_mode=${env.DEMO_CASSETTE_MODE} scale=${env.DEMO_SEED_SCALE}`);
  console.log(`[demo-harness] stress=${WANT_STRESS} skip_bot=${SKIP_BOT} skip_relay=${SKIP_RELAY_REPLAY}`);

  let botChild = null;
  try {
    if (!SKIP_API) {
      PHASE('1/7  pre-flight: API + bot reachability');
      const apiUp = await waitFor(API_URL, '/api/health', 60_000);
      if (!apiUp) {
        console.error(`[FATAL] API not reachable at ${API_URL}. Start it with: npm run dev:api`);
        console.error(`        Or rerun with --skip-api to attach to an already-running stack.`);
        process.exit(3);
      }
      console.log(`[demo-harness] API reachable at ${API_URL}`);

      PHASE('2/7  reset + seed demo ecosystem');
      await resetAndSeed();

      if (!SKIP_BOT) {
        PHASE('3/7  start BTC bot in sim mode (:7002)');
        botChild = await ensureBotRunning();
      } else {
        console.log('[demo-harness] --skip-bot: attaching to whatever is on :7002');
      }

      PHASE('4/7  wait for bot /api/ping');
      const botUp = await waitFor(BOT_URL, '/api/ping', 120_000);
      if (!botUp) {
        console.error(`[FATAL] Bot /api/ping never went green at ${BOT_URL}.`);
        console.error(`        Tail logs/demo/bot.log for details.`);
        process.exit(4);
      }
      console.log(`[demo-harness] bot reachable at ${BOT_URL}`);

      PHASE('5/7  drive synthetic relay webhooks (cassette replay)');
      if (!SKIP_RELAY_REPLAY) {
        await replayRelayCassettes();
      } else {
        console.log('[demo-harness] --skip-relay: skipping relay cassette replay');
      }

      PHASE('6/7  trigger analyzer run (async — scorecard will still work if it is slow)');
      await triggerAnalyzer().catch((err) => {
        console.log(`[demo-harness] analyzer trigger skipped/failed: ${err?.message ?? err} (non-fatal)`);
      });
    } else {
      console.log('[demo-harness] --skip-api: assuming API + bot already up');
    }

    PHASE('7/7  run unified harness probe (all pillars -> scorecard)');
    if (!HARNESS_TOKEN) {
      console.error('[FATAL] DEMO_HARNESS_TOKEN not set. The orchestrator needs it to call the\n' +
        '        internal harness route. Set it in your .env or pass via the shell:\n' +
        '          $env:DEMO_HARNESS_TOKEN="<some-secret>"\n' +
        '        Then the API service must have the same value in its env.');
      process.exit(5);
    }
    const scorecard = await runHarnessProbe();
    const durationS = ((Date.now() - t0) / 1000).toFixed(1);
    printScorecardSummary(scorecard, durationS);
    const exitCode = scorecard.overall === 'FAIL' ? 6 : 0;
    process.exit(exitCode);
  } catch (err) {
    console.error('\n[demo-harness] CRASH:', err?.stack || err);
    if (botChild) { try { botChild.kill('SIGTERM'); } catch {} }
    process.exit(1);
  }
}

async function waitFor(base, path, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${base}${path}`, { signal: AbortSignal.timeout(5000) });
      if (res.ok) return true;
    } catch {}
    await sleep(3000);
  }
  return false;
}

async function resetAndSeed() {
  const resetRes = await fetch(`${API_URL}/api/admin/demo/reset`, {
    method: 'POST', headers: jsonHeaders(), signal: AbortSignal.timeout(120_000),
  }).catch((err) => { throw new Error(`reset call failed: ${err?.message ?? err}`); });
  if (!resetRes.ok) {
    const body = await safeText(resetRes);
    console.log(`[demo-harness] reset returned HTTP ${resetRes.status} — continuing (may be empty): ${body.slice(0, 200)}`);
  } else {
    const reset = await safeJson(resetRes);
    console.log(`[demo-harness] reset: ${reset?.message ?? 'done'}`);
  }
  const seedRes = await fetch(`${API_URL}/api/admin/demo/seed`, {
    method: 'POST', headers: jsonHeaders(), signal: AbortSignal.timeout(180_000),
  }).catch((err) => { throw new Error(`seed call failed: ${err?.message ?? err}`); });
  if (!seedRes.ok) {
    const body = await safeText(seedRes);
    throw new Error(`seed HTTP ${seedRes.status}: ${body.slice(0, 300)}`);
  }
  const seed = await safeJson(seedRes);
  console.log(`[demo-harness] seed: scale=${seed?.scale} ok=${seed?.ok} ${seed?.message ?? ''}`);
  console.log(`[demo-harness] counts: ${JSON.stringify(seed?.status?.counts ?? {})}`);
}

async function ensureBotRunning() {
  try {
    const res = await fetch(`${BOT_URL}/api/ping`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) { console.log(`[demo-harness] bot already running at ${BOT_URL} — attaching`); return null; }
  } catch {}
  const botLog = join(LOG_DIR, 'bot.log');
  const logStream = (await import('node:fs')).createWriteStream(botLog, { flags: 'w' });
  console.log(`[demo-harness] starting bot — env LIVE_TRADING_ENABLED=${env.LIVE_TRADING_ENABLED} DEMO_MODE_ENABLED=${env.DEMO_MODE_ENABLED} cassette=${env.DEMO_CASSETTE_MODE}`);
  console.log(`[demo-harness] bot cwd=${BOT_DIR} log=${botLog}`);
  const child = spawn('python', ['demo_mode.py'], { cwd: BOT_DIR, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  child.stdout.pipe(logStream);
  child.stderr.pipe(logStream);
  child.on('exit', (code, signal) => { console.log(`[demo-harness] bot child exited code=${code} signal=${signal}`); });
  return child;
}

async function replayRelayCassettes() {
  const relaySecret = env.BOT_CONTROL_SECRET || '';
  if (!relaySecret) {
    console.log('[demo-harness] BOT_CONTROL_SECRET not set — skipping relay replay (set it to enable)');
    return;
  }
  const events = ['approve_pending_sample', 'order_placed_sample', 'limit_updated_sample', 'position_closed_sample'];
  let pushed = 0;
  for (const key of events) {
    const cassettePath = join(CASSETTE_DIR, 'relay', `${key}.json`);
    if (!existsSync(cassettePath)) { console.log(`[demo-harness] relay cassette ${key} missing — skipping`); continue; }
    const cassette = JSON.parse(readFileSync(cassettePath, 'utf8'));
    const payload = cassette.response;
    const tradeId = `demo-${env.DEMO_RUN_STARTED_MS}-${key}`;
    payload.trade_id = tradeId;
    payload.ts = new Date().toISOString();
    try {
      const res = await fetch(`${API_URL}/api/trading-agents/conservative-btc/showcase-relay-event`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-bot-control-secret': relaySecret },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(15_000),
      });
      const body = await safeJson(res).catch(() => null);
      if (res.ok) { pushed += 1; console.log(`[demo-harness] relay ${payload.event} trade=${tradeId.slice(0, 24)} -> ok=${body?.ok ?? true}`); }
      else { console.log(`[demo-harness] relay ${payload.event} -> HTTP ${res.status} ${JSON.stringify(body).slice(0, 200)}`); }
    } catch (err) { console.log(`[demo-harness] relay ${payload.event} failed: ${err?.message ?? err}`); }
    await sleep(800);
  }
  console.log(`[demo-harness] relay cassette replay: ${pushed}/${events.length} events pushed`);
}

async function triggerAnalyzer() {
  const analyzerPath = join(BOT_DIR, 'research', 'analyzer_research_engine_v62.py');
  if (!existsSync(analyzerPath)) { console.log(`[demo-harness] analyzer not found at ${analyzerPath}`); return; }
  console.log(`[demo-harness] running analyzer (timeout 90s) — this generates reports/*`);
  await new Promise((resolveFn) => {
    const child = spawn('python', [analyzerPath], { cwd: BOT_DIR, env, stdio: 'ignore', windowsHide: true });
    const timer = setTimeout(() => { try { child.kill('SIGTERM'); } catch {} resolveFn(); }, 90_000);
    child.on('exit', () => { clearTimeout(timer); resolveFn(); });
  });
  console.log('[demo-harness] analyzer run complete');
}

async function runHarnessProbe() {
  const url = new URL(`${API_URL}/api/admin/demo/harness/internal`);
  const body = { token: HARNESS_TOKEN, skipStress: !WANT_STRESS };
  if (WANT_STRESS) {
    body.stressRps = Number(env.DEMO_STRESS_RPS ?? 20);
    body.stressDurationS = Number(env.DEMO_STRESS_DURATION_S ?? 10);
  }
  const res = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(WANT_STRESS ? 300_000 : 180_000),
  });
  const text = await safeText(res);
  if (!res.ok) throw new Error(`harness HTTP ${res.status}: ${text.slice(0, 500)}`);
  let json;
  try { json = JSON.parse(text); } catch (err) { throw new Error(`harness response not JSON: ${text.slice(0, 200)}`); }
  const jsonPath = join(LOG_DIR, 'demo-report.json');
  const mdPath = join(LOG_DIR, 'demo-report.md');
  writeFileSync(jsonPath, JSON.stringify(json, null, 2) + '\n', 'utf8');
  if (json.markdown) writeFileSync(mdPath, json.markdown, 'utf8');
  return json;
}

function printScorecardSummary(scorecard, durationS) {
  const lines = ['', '========================================', '  DEMO HARNESS — READINESS REPORT', '========================================',
    `Overall:          ${scorecard.overall}`,
    `Readiness score:  ${scorecard.readinessScore}/100`,
    `Duration:         ${durationS}s (this orchestrator run)`,
    `Checks:           ${scorecard.totals.checksPassed}/${scorecard.totals.checksRun} passed (${scorecard.totals.checksFailed} failed)`,
    '', 'Pillar scores:'];
  for (const [name, pillar] of Object.entries(scorecard.pillars)) {
    const passed = pillar.checks.filter((c) => c.passed).length;
    lines.push(`  ${name.padEnd(10)} ${String(pillar.score).padStart(3)}/100  (${passed}/${pillar.checks.length} checks)`);
  }
  lines.push('', 'Switches:');
  for (const [k, v] of Object.entries(scorecard.switches)) {
    lines.push(`  ${k.padEnd(18)} ${typeof v === 'boolean' ? (v ? 'ON' : 'OFF') : v}`);
  }
  lines.push('', 'Synthetic data volume:');
  for (const [k, v] of Object.entries(scorecard.numbers)) { lines.push(`  ${k.padEnd(20)} ${v}`); }
  lines.push('', 'Failed checks (detail):');
  const failed = Object.values(scorecard.pillars).flatMap((p) => p.checks).filter((c) => !c.passed);
  if (failed.length === 0) lines.push('  (none)');
  else { for (const c of failed) lines.push(`  ${c.name}: ${c.detail}`); }
  lines.push('', `Report persisted: logs/demo/demo-report.{json,md}`);
  console.log(lines.join('\n'));
}

function jsonHeaders() {
  return { 'Content-Type': 'application/json', ...(HARNESS_TOKEN ? { 'x-demo-harness-token': HARNESS_TOKEN } : {}) };
}
async function safeJson(res) { try { return await res.json(); } catch { return null; } }
async function safeText(res) { try { return await res.text(); } catch { return ''; } }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

main().catch((err) => { console.error('[demo-harness] uncaught:', err?.stack || err); process.exit(1); });
