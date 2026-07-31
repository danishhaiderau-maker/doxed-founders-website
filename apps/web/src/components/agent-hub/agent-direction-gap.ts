export type DirectionGap = {
  raw: number;
  bucket: number;
  bucketLabel: string;
};

/** Convert the AI's raw 0-100 LONG/SHORT difference to the execution bucket. */
export function directionGap(rawValue: unknown): DirectionGap | null {
  if (rawValue == null || rawValue === '') return null;
  const raw = Number(rawValue);
  if (!Number.isFinite(raw) || raw < 0) return null;
  const rounded = Math.min(100, Math.floor(raw));
  const bucket = Math.floor(rounded / 10);
  return {
    raw: rounded,
    bucket,
    bucketLabel: bucket >= 5 ? '5+' : String(bucket),
  };
}

export function directionGapLabel(rawValue: unknown): string {
  const gap = directionGap(rawValue);
  if (!gap) return 'Gap not recorded';
  return `Raw AI gap ${gap.raw}/100 · execution bucket ${gap.bucketLabel}`;
}

export function chaseSelectionLabel(buckets: number[] | null | undefined): string {
  const normalized = [...new Set((buckets ?? []).map(Number).filter(Number.isFinite))]
    .map(Math.floor)
    .filter((value) => value >= 0)
    .sort((a, b) => a - b);
  return normalized.length > 0
    ? normalized.map((value) => (value >= 5 ? '5+' : String(value))).join(', ')
    : 'not reported';
}
