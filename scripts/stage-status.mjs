#!/usr/bin/env node
/**
 * stage-status.mjs
 *
 * Show pending local changes that haven't been pushed to origin/master yet,
 * with an estimate of Actions minutes saved by batching.
 *
 * Output:
 *   - Files changed (added/modified/deleted) vs origin/master
 *   - Count of local commits not yet pushed
 *   - Estimated Actions minutes saved (2 workflows * 3 min avg per commit)
 *   - Suggested batch commit message based on changed paths
 */
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const repoRoot = process.cwd();

function git(args) {
  try {
    return execSync(`git ${args}`, { cwd: repoRoot, encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'] }).trim();
  } catch {
    return '';
  }
}

const headSha = git('rev-parse HEAD');
const upstreamSha = git('rev-parse origin/master');
const unpushedCommits = git(`log ${upstreamSha}..${headSha} --oneline`).split('\n').filter(Boolean);
const changedFiles = git(`diff --name-status ${upstreamSha}..HEAD`)
  .split('\n')
  .filter(Boolean)
  .map((line) => {
    const [status, ...pathParts] = line.split('\t');
    return { status, path: pathParts.join('\t') };
  });

// Plus any unstaged/uncommitted changes. Use regex instead of fixed columns
// because execSync may strip leading whitespace from the first line.
const dirty = git('status --porcelain').split('\n').filter(Boolean).map((line) => {
  const match = line.match(/^([MADRC?U! ]{1,2})\s+(.+)$/);
  if (!match) return null;
  return { status: match[1].trim(), path: match[2].trim() };
}).filter(Boolean);

const totalChanges = changedFiles.length + dirty.length;
const minutesSaved = unpushedCommits.length * 6; // 2 workflows * ~3 min each

console.log('\n=== Local staging status ===\n');

if (totalChanges === 0 && unpushedCommits.length === 0) {
  console.log('  (clean) - nothing staged locally, HEAD matches origin/master\n');
  process.exit(0);
}

if (unpushedCommits.length > 0) {
  console.log(`Unpushed commits: ${unpushedCommits.length}`);
  for (const c of unpushedCommits.slice(0, 8)) console.log(`  ${c}`);
  if (unpushedCommits.length > 8) console.log(`  ... +${unpushedCommits.length - 8} more`);
  console.log('');
}

if (changedFiles.length > 0) {
  console.log(`Committed-but-unpushed file changes: ${changedFiles.length}`);
  for (const f of changedFiles.slice(0, 12)) console.log(`  ${f.status.padEnd(2)} ${f.path}`);
  if (changedFiles.length > 12) console.log(`  ... +${changedFiles.length - 12} more`);
  console.log('');
}

if (dirty.length > 0) {
  console.log(`Uncommitted local changes: ${dirty.length}`);
  for (const f of dirty.slice(0, 12)) console.log(`  ${f.status.padEnd(2)} ${f.path}`);
  if (dirty.length > 12) console.log(`  ... +${dirty.length - 12} more`);
  console.log('');
}

console.log(`Estimated Actions minutes saved by batching: ~${minutesSaved} min`);
console.log(`(Each push triggers ~2 workflows at ~3 min each)\n`);

// Suggest a commit message based on changed paths
const allPaths = [...changedFiles, ...dirty].map((f) => f.path);
const scopeCounts = {};
for (const p of allPaths) {
  let scope;
  if (p.startsWith('apps/api/')) scope = 'api';
  else if (p.startsWith('apps/web/')) scope = 'web';
  else if (p.startsWith('packages/')) scope = 'packages';
  else if (p.startsWith('scripts/')) scope = 'ops';
  else if (p.startsWith('docs/')) scope = 'docs';
  else if (p.startsWith('.github/')) scope = 'ci';
  else if (p.startsWith('prisma/')) scope = 'db';
  else if (p.startsWith('services/')) scope = 'bot';
  else scope = 'misc';
  scopeCounts[scope] = (scopeCounts[scope] || 0) + 1;
}
const topScope = Object.entries(scopeCounts).sort((a, b) => b[1] - a[1])[0];
if (topScope) {
  console.log(`Suggested batch message:`);
  console.log(`  chore(${topScope[0]}): batch ${allPaths.length} local changes (cursor-agent)`);
  console.log('');
}
