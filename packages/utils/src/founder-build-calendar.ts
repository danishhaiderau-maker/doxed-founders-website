/** UTC calendar day key (YYYY-MM-DD) for streak / public build day math. */
export function founderCalendarDayKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function daysBetweenUtc(startKey: string, endKey: string): number {
  const start = new Date(`${startKey}T00:00:00.000Z`).getTime();
  const end = new Date(`${endKey}T00:00:00.000Z`).getTime();
  return Math.round((end - start) / 86_400_000);
}

/**
 * Consecutive calendar-day streak after a new build activity.
 * Same calendar day → unchanged; next day → +1; gap ≥2 days → reset to 1.
 */
export function computeNextBuildStreakDays(
  lastBuildPostAt: Date | null,
  currentStreak: number,
  now = new Date(),
): number {
  if (!lastBuildPostAt) return 1;

  const lastKey = founderCalendarDayKey(lastBuildPostAt);
  const nowKey = founderCalendarDayKey(now);
  if (lastKey === nowKey) return Math.max(1, currentStreak);

  const gap = daysBetweenUtc(lastKey, nowKey);
  if (gap === 1) return Math.max(1, currentStreak) + 1;
  return 1;
}

/**
 * Public "Day N" label: days since founder started building in public (inclusive).
 * Day 1 = first calendar day on the platform / first public build anchor.
 */
export function computePublicBuildDayNumber(anchor: Date, now = new Date()): number {
  const startKey = founderCalendarDayKey(anchor);
  const nowKey = founderCalendarDayKey(now);
  return daysBetweenUtc(startKey, nowKey) + 1;
}

/** Active streak from sorted unique post day keys (must end today or yesterday). */
export function computeActiveBuildStreakFromDayKeys(
  dayKeys: string[],
  now = new Date(),
): number {
  const sorted = [...new Set(dayKeys)].sort();
  if (sorted.length === 0) return 0;

  const nowKey = founderCalendarDayKey(now);
  const lastKey = sorted[sorted.length - 1]!;
  const daysSinceLast = daysBetweenUtc(lastKey, nowKey);
  if (daysSinceLast > 1) return 0;

  let streak = 1;
  for (let i = sorted.length - 2; i >= 0; i--) {
    const gap = daysBetweenUtc(sorted[i]!, sorted[i + 1]!);
    if (gap === 1) streak += 1;
    else break;
  }
  return streak;
}
