'use client';

export function AgentStatsSparkline({
  color = 'emerald',
  seed = 1,
}: {
  color?: 'emerald' | 'blue' | 'violet';
  seed?: number;
}) {
  const stroke =
    color === 'blue' ? '#60a5fa' : color === 'violet' ? '#a78bfa' : '#34d399';
  const points = Array.from({ length: 12 }, (_, i) => {
    const t = i / 11;
    const y = 18 - Math.sin(i * 0.9 + seed) * 6 - t * 4;
    return `${(i / 11) * 80},${y}`;
  }).join(' ');

  return (
    <svg viewBox="0 0 80 24" className="h-6 w-16 opacity-80" aria-hidden>
      <polyline fill="none" stroke={stroke} strokeWidth="2" points={points} />
    </svg>
  );
}
