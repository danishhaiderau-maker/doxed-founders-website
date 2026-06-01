import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { app } from 'electron';
import { semverGt, semverLt } from './semver';

const EXE_PATTERN = /^Founder-Node-(\d+\.\d+\.\d+)-win-x64\.exe$/i;

type CleanupReport = {
  removed: string[];
  skipped: string[];
  at: string;
  version: string;
};

function markerPath(version: string): string {
  return path.join(app.getPath('userData'), `legacy-portable-cleanup-${version}.json`);
}

function readMarker(version: string): CleanupReport | null {
  try {
    const raw = fs.readFileSync(markerPath(version), 'utf8');
    return JSON.parse(raw) as CleanupReport;
  } catch {
    return null;
  }
}

function writeMarker(version: string, report: CleanupReport): void {
  fs.mkdirSync(path.dirname(markerPath(version)), { recursive: true });
  fs.writeFileSync(markerPath(version), JSON.stringify(report, null, 2), 'utf8');
}

/** Remove stale portable .exe copies from Downloads/Desktop (not the running installer). */
export function cleanupLegacyPortableInstallers(currentVersion: string): CleanupReport {
  const prior = readMarker(currentVersion);
  if (prior) return prior;

  const removed: string[] = [];
  const skipped: string[] = [];
  const runningPortable = process.env.PORTABLE_EXECUTABLE_FILE?.trim();

  const scanDirs = [
    path.join(os.homedir(), 'Downloads'),
    path.join(os.homedir(), 'Desktop'),
  ];

  for (const dir of scanDirs) {
    if (!fs.existsSync(dir)) continue;
    for (const name of fs.readdirSync(dir)) {
      const match = EXE_PATTERN.exec(name);
      if (!match) continue;

      const fileVersion = match[1];
      const full = path.join(dir, name);

      if (runningPortable && path.resolve(runningPortable) === path.resolve(full)) {
        skipped.push(full);
        continue;
      }

      if (semverGt(fileVersion, currentVersion)) {
        skipped.push(full);
        continue;
      }

      if (!semverLt(fileVersion, currentVersion) && fileVersion !== currentVersion) {
        skipped.push(full);
        continue;
      }

      try {
        fs.unlinkSync(full);
        removed.push(full);
      } catch {
        skipped.push(full);
      }
    }
  }

  const report: CleanupReport = {
    removed,
    skipped,
    at: new Date().toISOString(),
    version: currentVersion,
  };
  writeMarker(currentVersion, report);
  if (removed.length) {
    console.log(`Founder Node: removed ${removed.length} old portable installer(s)`);
  }
  return report;
}
