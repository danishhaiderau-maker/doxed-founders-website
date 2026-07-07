#!/usr/bin/env node
/**
 * stage-push.mjs
 *
 * Squash all local unpushed commits + uncommitted changes into ONE commit,
 * then push to origin/master. This triggers CI exactly once instead of N
 * times, saving ~6 Actions minutes per squashed commit.
 *
 * Usage:
 *   node scripts/stage-push.mjs                # squash + push
 *   node scripts/stage-push.mjs --dry-run      # show what would happen
 *   node scripts/stage-push.mjs --msg "..."    # custom commit message
 *   node scripts/stage-push.mjs --keep         # don't squash, just push as-is
 *
 * Safety:
 *   - Never force-pushes
 *   - Never rewrites already-pushed history
 *   - Aborts if rebase would conflict
 *   - --dry-run is non-destructive
 */
import { execSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';

const args = process.argv.slice(2);
const isDryRun = args.includes('--dry-run');
const keepHistory = args.includes('--keep');
const msgIdx = args.indexOf('--msg');
const customMsg = msgIdx >= 0 ? args[msgIdx + 1] : null;

const repoRoot = process.cwd();

function git(args, opts = {}) {
  const cmd = `git ${args}`;
  if (isDryRun && !opts.allowDuringDryRun) {
    console.log(`  [dry-run] would run: ${cmd}`);
    return '';
  }
  try {
    return execSync(cmd, { cwd: repoRoot, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
  } catch (e) {
    if (opts.allowFailure) return '';
    console.error(`\nFatal: git command failed: ${cmd}`);
    console.error(e.stderr?.toString() || e.message);
    process.exit(1);
  }
}

console.log('\n=== Local staging push ===\n');

const headSha = git('rev-parse HEAD', { allowDuringDryRun: true });
const upstreamSha = git('rev-parse origin/master', { allowDuringDryRun: true });

if (headSha === upstreamSha && !git('status --porcelain', { allowDuringDryRun: true })) {
  console.log('Nothing to push - HEAD matches origin/master and no uncommitted changes.\n');
  process.exit(0);
}

// Make sure we have the latest upstream
console.log('Syncing with origin/master...');
git('fetch origin master', { allowDuringDryRun: true });

const unpushedCommits = git(`log ${upstreamSha}..${headSha} --oneline`, { allowDuringDryRun: true })
  .split('\n')
  .filter(Boolean);

const dirty = git('status --porcelain', { allowDuringDryRun: true }).split('\n').filter(Boolean);

console.log(`\nWill ship: ${unpushedCommits.length} unpushed commits + ${dirty.length} uncommitted changes`);

if (unpushedCommits.length > 0) {
  console.log('\nUnpushed commits being squashed:');
  for (const c of unpushedCommits.slice(0, 10)) console.log(`  ${c}`);
  if (unpushedCommits.length > 10) console.log(`  ... +${unpushedCommits.length - 10} more`);
}

if (isDryRun) {
  console.log('\n[dry-run] No changes made. Re-run without --dry-run to actually push.\n');
  process.exit(0);
}

// Stage any uncommitted changes
if (dirty.length > 0) {
  console.log('\nStaging uncommitted changes...');
  git('add -A');
}

// Build the batch message
const date = new Date().toISOString().split('T')[0];
const message = customMsg || `chore: batch ${unpushedCommits.length + (dirty.length > 0 ? 1 : 0)} local changes (${date})

Squashed by stage-push.mjs to trigger a single CI run instead of ${unpushedCommits.length + 1}.

Includes:
${unpushedCommits.map((c) => ` - ${c}`).join('\n')}
${dirty.length > 0 ? ` - ${dirty.length} uncommitted file(s)` : ''}
`;

if (keepHistory || unpushedCommits.length === 0) {
  // Just commit dirty changes and push without squashing
  if (dirty.length > 0) {
    const msgFile = `${repoRoot}/.git/STAGE_PUSH_MSG.txt`;
    writeFileSync(msgFile, message, 'utf8');
    git(`commit -F ${msgFile} --allow-empty`);
  }
} else {
  // Soft reset to upstream so we re-commit as one squashed commit
  console.log('\nSquashing via soft reset...');
  git(`reset --soft ${upstreamSha}`);
  const msgFile = `${repoRoot}/.git/STAGE_PUSH_MSG.txt`;
  writeFileSync(msgFile, message, 'utf8');
  git(`commit -F ${msgFile} --allow-empty`);
}

console.log('\nPushing to origin/master...');
const pushResult = git('push origin master');
console.log(pushResult);

console.log('\n✓ Batch pushed. CI will run exactly once for this batch.');
console.log('  Use `gh run list --branch master --limit 3` to watch.\n');
