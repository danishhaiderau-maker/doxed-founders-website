/** Minimum Founder Node semver — keep in sync with apps/founder-node/package.json */
export const FOUNDER_NODE_MIN_VERSION = '0.7.14';

/** User-facing label for download CTAs and pairing hints */
export const FOUNDER_NODE_MIN_VERSION_LABEL = `v${FOUNDER_NODE_MIN_VERSION}+`;

/** True when the paired desktop node should prompt an update. */
export function founderNodeNeedsUpdate(version: string | null | undefined): boolean {
  if (!version?.trim()) return true;
  const parts = version
    .trim()
    .replace(/^v/i, '')
    .split('.')
    .map((n) => Number(n));
  if (parts.some((n) => Number.isNaN(n))) return true;
  const [major = 0, minor = 0, patch = 0] = parts;
  const [minMajor, minMinor, minPatch] = FOUNDER_NODE_MIN_VERSION.split('.').map((n) => Number(n));
  if (major !== minMajor) return major < minMajor;
  if (minor !== minMinor) return minor < minMinor;
  if (patch !== minPatch) return patch < minPatch;
  return false;
}
