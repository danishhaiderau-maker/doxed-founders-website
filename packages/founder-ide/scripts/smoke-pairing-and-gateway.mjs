#!/usr/bin/env node
// smoke-pairing-and-gateway.mjs
//
// Smoke test for the Phase 3 Founder IDE pairing + Gateway streaming path.
//
// Validates the three things that must work for a user to onboard:
//   1. connect-ide.connectCursor() writes Cursor settings.json correctly and
//      creates a .founder-os-backup-*.json backup of the prior file.
//   2. The extension's gateway-client.callGateway() streams an OpenAI-compatible
//      SSE response end-to-end (token deltas + [DONE] termination), against a
//      mock gateway running on localhost.
//   3. The SSE parser also handles the non-standard `founderOs` metadata line
//      and CRLF line endings.
//
// This test does NOT require Electron, VS Code, or the Founder Node to be
// running. It imports the real compiled artifacts:
//   - apps/founder-node/dist/connect-ide.js   (the pairing code)
//   - packages/founder-ide-extension/out/gateway-client.js  (the SSE client)
//
// Run from the repo root:
//   node packages/founder-ide/scripts/smoke-pairing-and-gateway.mjs
//
// Exit code 0 = all checks passed, 1 = at least one failed.

import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);

// --- Locate the compiled artifacts -----------------------------------------
// fileURLToPath decodes the %20 in "Final Bots" — a raw URL pathname would not.
const SCRIPT_PATH = fileURLToPath(new URL(import.meta.url));
const REPO_ROOT = path.resolve(path.dirname(SCRIPT_PATH), '..', '..', '..');

const CONNECT_IDE_JS = path.join(REPO_ROOT, 'apps', 'founder-node', 'dist', 'connect-ide.js');
const GATEWAY_CLIENT_JS = path.join(REPO_ROOT, 'packages', 'founder-ide-extension', 'out', 'gateway-client.js');

function fileExists(p) { try { return fs.existsSync(p); } catch { return false; } }

const results = [];
function check(name, cond, detail) {
  results.push({ name, pass: !!cond, detail });
  const mark = cond ? '\u2713' : '\u2717';
  console.log(`  ${mark} ${name}${detail && cond !== true ? '' : ''}${detail ? ` -> ${detail}` : ''}`);
}

console.log('Founder IDE Phase 3 — pairing + Gateway smoke test\n');
console.log(`repo root:  ${REPO_ROOT}`);
console.log(`connect:    ${CONNECT_IDE_JS}`);
console.log(`gateway:    ${GATEWAY_CLIENT_JS}\n`);

// --- 0. Compiled artifacts present -----------------------------------------
check('apps/founder-node/dist/connect-ide.js exists', fileExists(CONNECT_IDE_JS));
check('packages/founder-ide-extension/out/gateway-client.js exists', fileExists(GATEWAY_CLIENT_JS));

let connectIde, gatewayClient;
try {
  connectIde = require(CONNECT_IDE_JS);
  check('connect-ide.js loads under Node (no Electron dependency)', !!connectIde.connectCursor);
} catch (e) {
  check('connect-ide.js loads under Node (no Electron dependency)', false, e.message);
}
try {
  gatewayClient = require(GATEWAY_CLIENT_JS);
  check('gateway-client.js loads under Node (no vscode dependency)', typeof gatewayClient.callGateway === 'function');
} catch (e) {
  check('gateway-client.js loads under Node (no vscode dependency)', false, e.message);
}

if (!connectIde || !gatewayClient) {
  console.log('\nRESULT: FAIL (artifacts did not load — run npm run build in apps/founder-node and packages/founder-ide-extension)');
  process.exit(1);
}

// ===========================================================================
// Part 1 — connectCursor() pairing
// ===========================================================================
console.log('\n--- Part 1: connectCursor() pairing (settings.json + backup) ---');

// Point APPDATA at a temp dir so we don't touch the real Cursor settings.
const TMP_APPDATA = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-smoke-appdata-'));
process.env.APPDATA = TMP_APPDATA;
const cursorUserDir = path.join(TMP_APPDATA, 'Cursor', 'User');
const settingsFile = path.join(cursorUserDir, 'settings.json');

const fakeConfig = {
  version: 1,
  apiBaseUrl: 'https://doxxedcrypto.digital',
  nodeId: 'node_smoke_1234',
  nodeToken: 'tok_smoke_secret_5678',
  label: 'smoke',
  pairedAt: '2026-07-15T00:00:00Z',
};

// 1a. Fresh pair (no existing settings.json -> backupPath should be null)
const resultFresh = connectIde.connectCursor(fakeConfig);
check('fresh connectCursor returns ok=true', resultFresh.ok === true, `ok=${resultFresh.ok}`);
check('fresh connectCursor targets the temp settings.json', resultFresh.target === settingsFile);
check('fresh connectCursor backupPath is null (no prior file)', resultFresh.backupPath === null, `backupPath=${resultFresh.backupPath}`);

let settingsRaw;
try {
  settingsRaw = JSON.parse(fs.readFileSync(settingsFile, 'utf8'));
  check('settings.json was written and parses', !!settingsRaw);
} catch (e) {
  check('settings.json was written and parses', false, e.message);
  settingsRaw = {};
}

check('settings.json has openai.apiBase = {apiBaseUrl}/api/v1',
  settingsRaw['openai.apiBase'] === 'https://doxxedcrypto.digital/api/v1',
  String(settingsRaw['openai.apiBase']));
check('settings.json has openai.apiKey = fos_{nodeId}:{nodeToken}',
  settingsRaw['openai.apiKey'] === 'fos_node_smoke_1234:tok_smoke_secret_5678',
  String(settingsRaw['openai.apiKey']));
check('settings.json sets cursor.generalModel = founder-os-auto',
  settingsRaw['cursor.generalModel'] === 'founder-os-auto');
check('settings.json marks founder-os.connected = true',
  settingsRaw['founder-os.connected'] === true);
check('settings.json records founder-os.nodeId',
  settingsRaw['founder-os.nodeId'] === 'node_smoke_1234');

// proxyBaseUrl helper
check('proxyBaseUrl() appends /api/v1 and strips trailing slash',
  connectIde.proxyBaseUrl('https://example.com/') === 'https://example.com/api/v1');
check('bearerFromConfig() produces fos_{id}:{token}',
  connectIde.bearerFromConfig(fakeConfig) === 'fos_node_smoke_1234:tok_smoke_secret_5678');

// 1b. Re-pair over an existing settings.json -> backup MUST be created
//     and the prior non-Founder keys MUST be preserved.
const preExisting = { 'editor.fontSize': 13, 'workbench.colorTheme': 'Dark+', 'founder-os.connected': false };
fs.writeFileSync(settingsFile, JSON.stringify(preExisting, null, 2), 'utf8');

const resultRePair = connectIde.connectCursor(fakeConfig);
check('re-pair connectCursor returns ok=true', resultRePair.ok === true);
check('re-pair connectCursor created a backup file', !!resultRePair.backupPath, String(resultRePair.backupPath));

let backupExists = false;
if (resultRePair.backupPath) {
  backupExists = fileExists(resultRePair.backupPath);
  // Verify the backup contains the PRE-pair contents.
  try {
    const backupRaw = fs.readFileSync(resultRePair.backupPath, 'utf8');
    const backupParsed = JSON.parse(backupRaw);
    check('backup file contains the pre-pair editor.fontSize=13', backupParsed['editor.fontSize'] === 13);
  } catch (e) {
    check('backup file parses and holds pre-pair state', false, e.message);
  }
}
if (!resultRePair.backupPath) check('backup file contains the pre-pair editor.fontSize=13', false, 'no backup');

// After re-pair, the new settings.json must preserve unrelated keys AND update ours.
let rePairedSettings = {};
try {
  rePairedSettings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'));
} catch { /* checked below */ }
check('re-paired settings.json preserves unrelated editor.fontSize=13',
  rePairedSettings['editor.fontSize'] === 13);
check('re-paired settings.json preserves unrelated workbench.colorTheme',
  rePairedSettings['workbench.colorTheme'] === 'Dark+');
check('re-paired settings.json updates founder-os.connected to true',
  rePairedSettings['founder-os.connected'] === true);

// 1c. disconnectCursor strips Founder keys but leaves the rest.
const resultDisconnect = connectIde.disconnectCursor(fakeConfig);
check('disconnectCursor returns ok=true', resultDisconnect.ok === true);
let disconnected = {};
try { disconnected = JSON.parse(fs.readFileSync(settingsFile, 'utf8')); } catch { /* */ }
check('disconnectCursor removes openai.apiBase', !('openai.apiBase' in disconnected));
check('disconnectCursor removes founder-os.connected', !('founder-os.connected' in disconnected));
check('disconnectCursor preserves editor.fontSize=13', disconnected['editor.fontSize'] === 13);

// ===========================================================================
// Part 2 — Gateway SSE streaming (real callGateway vs mock server)
// ===========================================================================
console.log('\n--- Part 2: Gateway SSE streaming (real callGateway vs mock gateway) ---');

function startMockGateway({ status = 200, chunks = [], delayMs = 0, useCrlf = false }) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      // Validate the request the extension sends.
      let body = '';
      req.on('data', (c) => { body += c; });
      req.on('end', () => {
        res.writeHead(status, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        });
        if (status !== 200) { res.end(body || 'error'); return; }
        const eol = useCrlf ? '\r\n' : '\n';
        const blank = useCrlf ? '\r\n\r\n' : '\n\n';
        const sendChunk = (i) => {
          if (i >= chunks.length) { res.end(); return; }
          res.write(`data: ${chunks[i]}${blank}`);
          setTimeout(() => sendChunk(i + 1), delayMs);
        };
        sendChunk(0);
        server._lastBody = body;
        server._lastHeaders = req.headers;
      });
    });
    // unref so the server never keeps the Node event loop alive on its own.
    // This avoids a Node 24 / libuv assertion that fires when http server
    // handles close during process teardown (win32, async.c line 94).
    server.unref();
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function close(server) {
  // Intentionally a no-op. Servers are created with .unref() so they don't keep
  // the Node event loop alive. Calling server.close() on Node 24/win32 triggers
  // a libuv assertion (async.c line 94) during teardown, so we let unref'd
  // servers vanish with the process instead of closing them explicitly.
  void server;
  return Promise.resolve();
}

// A fake CancellationToken that never cancels (the extension's callGateway expects one).
const neverCancel = { isCancellationRequested: false, onCancellationRequested() { return { dispose() {} }; } };

// 2a. Happy path — three token chunks then [DONE].
const happyChunks = [
  JSON.stringify({ choices: [{ delta: { content: 'Hello' } }] }),
  JSON.stringify({ choices: [{ delta: { content: ', ' } }] }),
  JSON.stringify({ choices: [{ delta: { content: 'Founder!' } }] }),
  '[DONE]',
];
const serverHappy = await startMockGateway({ chunks: happyChunks, delayMs: 5 });
const portHappy = serverHappy.address().port;

const tokensHappy = [];
const metaHappy = [];
let errHappy = null;
try {
  await gatewayClient.callGateway(
    { baseUrl: `http://127.0.0.1:${portHappy}/api/v1`, bearer: 'fos_node_smoke_1234:tok_smoke_secret_5678' },
    { model: 'founder-os-auto', messages: [{ role: 'user', content: 'hi' }], timeoutMs: 5000 },
    { onToken: (d) => tokensHappy.push(d), onMetadata: (m) => metaHappy.push(m), onError: (s, b) => { errHappy = [s, b]; } },
    neverCancel,
  );
} catch (e) {
  errHappy = ['throw', e.message];
}
await close(serverHappy);

check('happy path: no error callback fired', errHappy === null, JSON.stringify(errHappy));
check('happy path: received all 3 token deltas in order',
  tokensHappy.length === 3 && tokensHappy.join('') === 'Hello, Founder!',
  `tokens=${JSON.stringify(tokensHappy)}`);
check('happy path: request Authorization header uses Bearer fos_...',
  serverHappy._lastHeaders.authorization === 'Bearer fos_node_smoke_1234:tok_smoke_secret_5678',
  String(serverHappy._lastHeaders.authorization));
check('happy path: request Accept header is text/event-stream',
  serverHappy._lastHeaders.accept === 'text/event-stream');
try {
  const sent = JSON.parse(serverHappy._lastBody);
  check('happy path: request body has stream=true', sent.stream === true);
  check('happy path: request body has model=founder-os-auto', sent.model === 'founder-os-auto');
} catch (e) {
  check('happy path: request body is valid JSON with stream+model', false, e.message);
}

// 2b. founderOs metadata pre-line is routed to onMetadata, not onToken.
const metaChunk = JSON.stringify({ founderOs: { requestId: 'req_1', tier: 'auto', provider: 'glm', model: 'glm-4.6', ddollarCost: 0.012 } });
const serverMeta = await startMockGateway({ chunks: [metaChunk, happyChunks[0], '[DONE]'], delayMs: 5 });
const portMeta = serverMeta.address().port;
const tokensMeta = [];
const metaMeta = [];
await gatewayClient.callGateway(
  { baseUrl: `http://127.0.0.1:${portMeta}/api/v1`, bearer: 'fos_test:test' },
  { model: 'founder-os-auto', messages: [{ role: 'user', content: 'hi' }] },
  { onToken: (d) => tokensMeta.push(d), onMetadata: (m) => metaMeta.push(m), onError: () => {} },
  neverCancel,
).catch(() => {});
await close(serverMeta);
check('metadata line: routed to onMetadata', metaMeta.length === 1, `meta=${JSON.stringify(metaMeta)}`);
check('metadata line: parsed requestId/tier/provider', metaMeta[0]?.tier === 'auto' && metaMeta[0]?.provider === 'glm');
check('metadata line: NOT emitted as a token', tokensMeta.length === 1 && tokensMeta[0] === 'Hello',
  `tokens=${JSON.stringify(tokensMeta)}`);

// 2c. CRLF line endings (some proxies re-encode SSE with \r\n).
const serverCrlf = await startMockGateway({ chunks: happyChunks, delayMs: 5, useCrlf: true });
const portCrlf = serverCrlf.address().port;
const tokensCrlf = [];
await gatewayClient.callGateway(
  { baseUrl: `http://127.0.0.1:${portCrlf}/api/v1`, bearer: 'fos_test:test' },
  { model: 'founder-os-auto', messages: [{ role: 'user', content: 'hi' }] },
  { onToken: (d) => tokensCrlf.push(d), onMetadata: () => {}, onError: () => {} },
  neverCancel,
).catch(() => {});
await close(serverCrlf);
check('CRLF: stream parsed correctly across \\r\\n\\r\\n boundaries',
  tokensCrlf.join('') === 'Hello, Founder!',
  `tokens=${JSON.stringify(tokensCrlf)}`);

// 2d. Non-2xx -> onError with status + body, callGateway rejects.
const serverErr = await startMockGateway({ status: 401, chunks: [] });
const portErr = serverErr.address().port;
let errStatus = null, errBody = null, threw = false;
try {
  await gatewayClient.callGateway(
    { baseUrl: `http://127.0.0.1:${portErr}/api/v1`, bearer: 'fos_bad:bad' },
    { model: 'founder-os-auto', messages: [{ role: 'user', content: 'hi' }] },
    { onToken: () => {}, onMetadata: () => {}, onError: (s, b) => { errStatus = s; errBody = b; } },
    neverCancel,
  );
} catch { threw = true; }
await close(serverErr);
check('error path: onError fired with status 401', errStatus === 401, `status=${errStatus}`);
check('error path: callGateway rejects on non-2xx', threw === true);

// 2e. Execution-profile header passthrough.
const serverProf = await startMockGateway({ chunks: [happyChunks[0], '[DONE]'], delayMs: 0 });
const portProf = serverProf.address().port;
await gatewayClient.callGateway(
  { baseUrl: `http://127.0.0.1:${portProf}/api/v1`, bearer: 'fos_test:test' },
  { model: 'founder-os-code', messages: [{ role: 'user', content: 'hi' }], executionProfile: 'turbo' },
  { onToken: () => {}, onMetadata: () => {}, onError: () => {} },
  neverCancel,
).catch(() => {});
check('execution-profile: X-Execution-Profile header forwarded when set',
  serverProf._lastHeaders['x-execution-profile'] === 'turbo',
  String(serverProf._lastHeaders['x-execution-profile']));
await close(serverProf);

// --- Report ----------------------------------------------------------------
const passed = results.filter((r) => r.pass).length;
const failed = results.filter((r) => !r.pass).length;
console.log(`\n${passed} passed, ${failed} failed, ${results.length} total\n`);

// Clean up the temp APPDATA tree (best effort).
try { fs.rmSync(TMP_APPDATA, { recursive: true, force: true }); } catch { /* */ }

const failedCheck = failed > 0;
if (failedCheck) {
  console.error('RESULT: FAIL');
  for (const r of results.filter((r) => !r.pass)) console.error(`  - ${r.name}${r.detail ? ` (${r.detail})` : ''}`);
}

// Give the event loop one tick to drain, then exit explicitly. Node 24 on
// Windows hits a libuv assertion if http server handles close during process
// teardown, so we never let the process "fall off the end" — we force exit.
// Use exitCode + a 0ms unref'd timer so the loop drains synchronous work first.
process.exitCode = failedCheck ? 1 : 0;
setTimeout(() => process.exit(failedCheck ? 1 : 0), 0).unref();
