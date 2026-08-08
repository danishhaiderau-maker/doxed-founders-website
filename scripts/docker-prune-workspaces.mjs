// Prunes the monorepo manifests so a Docker image that only contains apps/api
// (not apps/web, apps/founder-node) can install cleanly.
//
// npm fails if the root workspaces glob references absent package dirs, and also
// fails (EMISSINGTARGET) if the lockfile has node_modules/@dcf/* link entries
// that point at absent workspace targets. This script fixes both by:
//   1. Setting root workspaces to ["apps/api","packages/*"] in package.json AND
//      the lockfile root entry.
//   2. Deleting lockfile `packages` keys for the absent apps and anything under
//      them (apps/web/**, apps/founder-node/**).
//   3. Deleting lockfile node_modules/@dcf/{web,founder-node} link
//      entries that resolved to those absent targets.
import fs from 'node:fs';

const WORKSPACES = ['apps/api', 'packages/*'];
// Workspace app dirs that are NOT copied into this image.
const ABSENT = ['apps/web', 'apps/founder-node'];
const ABSENT_NAMES = new Set(['@dcf/web', '@dcf/founder-node']);

function prune() {
  const pkgPath = './package.json';
  const lockPath = './package-lock.json';

  const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
  pkg.workspaces = WORKSPACES;
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');

  const lock = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
  lock.packages[''].workspaces = WORKSPACES;

  let removedTargets = 0;
  let removedLinks = 0;
  for (const key of Object.keys(lock.packages)) {
    const isAbsentApp = ABSENT.some((d) => key === d || key.startsWith(d + '/'));
    if (isAbsentApp) {
      delete lock.packages[key];
      removedTargets++;
      continue;
    }
    // Drop node_modules/@dcf/* symlink entries pointing at absent apps.
    if (key.startsWith('node_modules/@dcf/')) {
      const entry = lock.packages[key];
      const name = key.split('node_modules/')[1];
      if (ABSENT_NAMES.has(name) && typeof entry.resolved === 'string' &&
          ABSENT.some((d) => entry.resolved === d || entry.resolved.startsWith(d + '/'))) {
        delete lock.packages[key];
        removedLinks++;
      }
    }
  }
  fs.writeFileSync(lockPath, JSON.stringify(lock, null, 2) + '\n');

  // Verify no dangling references remain.
  const remaining = Object.keys(lock.packages)
    .filter((k) => ABSENT.some((d) => k === d || k.startsWith(d + '/')));
  const danglingLinks = Object.keys(lock.packages)
    .filter((k) => k.startsWith('node_modules/@dcf/'))
    .filter((k) => ABSENT_NAMES.has(k.split('node_modules/')[1]));
  console.log(`[prune-workspaces] workspaces=${JSON.stringify(WORKSPACES)} removedTargets=${removedTargets} removedLinks=${removedLinks} remainingAbsent=${remaining.length} danglingLinks=${danglingLinks.length}`);
  if (remaining.length || danglingLinks.length) {
    console.error('[prune-workspaces] FAILED to fully prune:', { remaining, danglingLinks });
    process.exit(1);
  }
}

prune();
