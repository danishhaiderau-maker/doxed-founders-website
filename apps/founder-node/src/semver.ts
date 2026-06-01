/** Compare semver strings (major.minor.patch). Returns true if a < b. */
export function semverLt(a: string, b: string): boolean {
  const pa = a.split('.').map((n) => parseInt(n, 10) || 0);
  const pb = b.split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i += 1) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (x < y) return true;
    if (x > y) return false;
  }
  return false;
}

export function semverGt(a: string, b: string): boolean {
  return semverLt(b, a);
}
