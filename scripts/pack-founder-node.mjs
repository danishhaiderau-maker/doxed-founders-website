#!/usr/bin/env node
/**
 * Build Founder Node desktop installers (.exe on Windows, .dmg on macOS).
 * Usage: node scripts/pack-founder-node.mjs [--win] [--mac] [--linux] [--all]
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');
const args = process.argv.slice(2);

function run(cmd, cmdArgs, opts = {}) {
  const result = spawnSync(cmd, cmdArgs, {
    stdio: 'inherit',
    shell: process.platform === 'win32',
    cwd: root,
    env: {
      ...process.env,
      CSC_IDENTITY_AUTO_DISCOVERY: 'false',
    },
    ...opts,
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

run('node', ['scripts/generate-founder-node-icon.mjs']);
run('npm', ['run', 'build:utils']);
run('npm', ['run', 'build', '--workspace=@dcf/founder-node']);
run('node', ['scripts/prepare-founder-node-pack.mjs']);

const platformArgs = [];
if (args.includes('--all')) {
  platformArgs.push('--win', '--mac', '--linux');
} else if (args.includes('--win')) {
  platformArgs.push('--win');
} else if (args.includes('--mac')) {
  platformArgs.push('--mac');
} else if (args.includes('--linux')) {
  platformArgs.push('--linux', 'AppImage');
} else if (process.platform === 'win32') {
  platformArgs.push('--win');
} else if (process.platform === 'darwin') {
  platformArgs.push('--mac');
} else {
  platformArgs.push('--linux', 'AppImage');
}

run('npx', ['electron-builder', ...platformArgs], {
  cwd: path.join(root, 'apps/founder-node'),
  env: {
    ...process.env,
    CSC_IDENTITY_AUTO_DISCOVERY: 'false',
    npm_config_workspace: '@dcf/founder-node',
  },
});

console.log('\nInstallers written to apps/founder-node/release/');
