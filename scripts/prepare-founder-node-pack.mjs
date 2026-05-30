#!/usr/bin/env node
/**
 * Ensures @dcf workspace packages are resolvable when electron-builder packs from apps/founder-node.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');
const appNodeModules = path.join(root, 'apps/founder-node/node_modules/@dcf');

function copyPackage(pkgDirName) {
  const srcRoot = path.join(root, 'packages', pkgDirName);
  const destRoot = path.join(appNodeModules, pkgDirName);

  fs.mkdirSync(destRoot, { recursive: true });
  fs.copyFileSync(path.join(srcRoot, 'package.json'), path.join(destRoot, 'package.json'));

  const srcDist = path.join(srcRoot, 'dist');
  const destDist = path.join(destRoot, 'dist');
  fs.rmSync(destDist, { recursive: true, force: true });
  fs.cpSync(srcDist, destDist, { recursive: true });
}

fs.mkdirSync(appNodeModules, { recursive: true });
copyPackage('founder-vault');
copyPackage('utils');

console.log('Prepared apps/founder-node/node_modules/@dcf for packaging');
