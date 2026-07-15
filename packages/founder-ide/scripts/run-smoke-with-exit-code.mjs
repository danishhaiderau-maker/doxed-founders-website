#!/usr/bin/env node
// run-smoke-with-exit-code.mjs
//
// Wrapper that runs the pairing + Gateway smoke test as a child process and
// returns the correct exit code based on the test's printed verdict.
//
// Why this exists: Node 24 on Windows hits a libuv assertion
//   "!(handle->flags & UV_HANDLE_CLOSING), file src\win\async.c, line 94"
// when the http-server-based smoke test tears down its listening sockets. The
// crash happens AFTER the test prints "38 passed, 0 failed" / "RESULT: PASS",
// so the test verdict is correct but the process exit code is non-zero, which
// breaks `&&`-chained npm scripts and CI gates.
//
// This wrapper runs the real test, streams its output, and derives the exit
// code from the printed verdict line — independent of the Node/libuv crash.
//
// Run:  node packages/founder-ide/scripts/run-smoke-with-exit-code.mjs

import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(new URL(import.meta.url)));
const SMOKE = path.join(__dirname, 'smoke-pairing-and-gateway.mjs');

const child = spawn(process.execPath, [SMOKE], { stdio: ['ignore', 'pipe', 'pipe'] });

let stdout = '';
let stderr = '';
child.stdout.on('data', (d) => { stdout += d; process.stdout.write(d); });
child.stderr.on('data', (d) => { stderr += d; process.stderr.write(d); });

child.on('close', () => {
  // Derive the verdict from the test's own printed output, not the process
  // exit code (which is unreliable on Node 24/win32 due to the libuv bug).
  const combined = stdout + stderr;

  // The smoke test prints a summary line like "38 passed, 0 failed, 38 total".
  // Pull the failed count out of that exact line.
  const summaryMatch = combined.match(/(\d+)\s+passed,\s*(\d+)\s+failed/);
  let failedCount = null;
  if (summaryMatch) failedCount = parseInt(summaryMatch[2], 10);

  const passed = summaryMatch !== null && failedCount === 0;

  if (passed) {
    console.log(`\n[wrapper] smoke test verdict: PASS (${summaryMatch[1]} passed, 0 failed — exit code normalized)`);
    process.exit(0);
  }
  console.error(`\n[wrapper] smoke test verdict: FAIL (failedCount=${failedCount})`);
  process.exit(1);
});

child.on('error', (e) => {
  console.error('\n[wrapper] failed to spawn smoke test:', e.message);
  process.exit(2);
});
