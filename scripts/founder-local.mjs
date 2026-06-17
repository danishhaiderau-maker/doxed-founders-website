#!/usr/bin/env node
/**
 * Phase 5 — Founder Cloud local stack orchestrator.
 * Wraps self-host bootstrap/start/stop for Founder Node tray + CLI.
 *
 * Usage: node scripts/founder-local.mjs [status|bootstrap|start|stop]
 */
import { spawnSync, execSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = process.env.FOUNDER_CLOUD_REPO ?? findRepoRoot();
const action = (process.argv[2] ?? 'status').toLowerCase();

function findRepoRoot() {
  let dir = process.cwd();
  for (let i = 0; i < 8; i += 1) {
    const pkg = path.join(dir, 'package.json');
    if (fs.existsSync(pkg)) {
      try {
        const name = JSON.parse(fs.readFileSync(pkg, 'utf8')).name;
        if (name === 'doxedcryptofounder') return dir;
      } catch {
        /* continue */
      }
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
}

function statusFile() {
  return path.join(os.homedir(), 'FounderVault', 'founder-cloud-status.json');
}

function writeStatus(patch) {
  const file = statusFile();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  let prev = {};
  if (fs.existsSync(file)) {
    try {
      prev = JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch {
      prev = {};
    }
  }
  const next = { ...prev, ...patch, updatedAt: new Date().toISOString(), repoPath: root };
  fs.writeFileSync(file, JSON.stringify(next, null, 2));
  return next;
}

function testPort(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port, timeout: 800 }, () => {
      socket.end();
      resolve(true);
    });
    socket.on('error', () => resolve(false));
    socket.on('timeout', () => {
      socket.destroy();
      resolve(false);
    });
  });
}

async function readPorts() {
  const envFile = path.join(root, '.env.self-host');
  let webPort = 3000;
  let apiPort = 4000;
  if (fs.existsSync(envFile)) {
    for (const line of fs.readFileSync(envFile, 'utf8').split('\n')) {
      const w = line.match(/^\s*WEB_PORT=(.*)$/);
      const a = line.match(/^\s*API_PORT=(.*)$/);
      if (w) webPort = Number(w[1].trim().replace(/"/g, '')) || 3000;
      if (a) apiPort = Number(a[1].trim().replace(/"/g, '')) || 4000;
    }
  }
  return { webPort, apiPort };
}

function run(cmd, label) {
  console.log(`\n> ${label}\n> ${cmd}\n`);
  const r = spawnSync(cmd, { cwd: root, shell: true, stdio: 'inherit' });
  if (r.status !== 0) {
    throw new Error(`${label} failed (exit ${r.status ?? 'unknown'})`);
  }
}

async function cmdStatus() {
  const { webPort, apiPort } = await readPorts();
  const [webUp, apiUp] = await Promise.all([testPort(webPort), testPort(apiPort)]);
  const status = writeStatus({
    running: webUp && apiUp,
    webUrl: `http://127.0.0.1:${webPort}`,
    apiUrl: `http://127.0.0.1:${apiPort}`,
    webUp,
    apiUp,
  });
  console.log(JSON.stringify(status, null, 2));
  process.exit(webUp && apiUp ? 0 : 1);
}

async function main() {
  if (!fs.existsSync(path.join(root, 'package.json'))) {
    console.error('Founder Cloud repo not found. Set FOUNDER_CLOUD_REPO to your checkout path.');
    process.exit(2);
  }

  switch (action) {
    case 'bootstrap':
      run('node scripts/bootstrap-self-host.mjs', 'bootstrap self-host');
      writeStatus({ bootstrapped: true });
      break;
    case 'start':
      if (process.platform === 'win32') {
        run(
          'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-self-host.ps1',
          'start self-host',
        );
      } else {
        run('npm run start:self-host', 'start self-host');
      }
      await cmdStatus();
      return;
    case 'stop':
      if (process.platform === 'win32') {
        run(
          'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop-self-host.ps1',
          'stop self-host',
        );
      } else {
        run('npm run stop:self-host', 'stop self-host');
      }
      writeStatus({ running: false, webUp: false, apiUp: false });
      break;
    case 'status':
      await cmdStatus();
      return;
    default:
      console.error(`Unknown action: ${action}. Use status|bootstrap|start|stop`);
      process.exit(2);
  }
}

main().catch((err) => {
  writeStatus({ lastError: err instanceof Error ? err.message : String(err), running: false });
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
