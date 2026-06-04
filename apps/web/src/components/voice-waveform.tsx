'use client';

import type { VoiceInputPhase } from '@/hooks/use-voice-input';

type Props = {
  phase: VoiceInputPhase;
  /** 0–1 from microphone analyser while listening */
  level?: number;
};

const BAR_HEIGHTS = [0.35, 0.55, 0.85, 0.65, 0.45];

export function VoiceWaveform({ phase, level = 0 }: Props) {
  const active = phase === 'listening' || phase === 'starting';
  if (!active) return null;

  const boost = phase === 'starting' ? 0.25 : Math.min(1, level * 1.4 + 0.15);

  return (
    <div
      className="flex h-6 items-end gap-0.5 px-1"
      role="status"
      aria-live="polite"
      aria-label={phase === 'listening' ? 'Listening — speak now' : 'Starting microphone'}
    >
      {BAR_HEIGHTS.map((base, i) => {
        const h = Math.round((base * boost + (phase === 'starting' ? 0.12 : 0)) * 100);
        return (
          <span
            key={i}
            className={`w-1 rounded-full transition-[height] duration-75 ${
              phase === 'listening' ? 'bg-red-400' : 'bg-amber-400 animate-pulse'
            }`}
            style={{
              height: `${Math.max(18, Math.min(100, h))}%`,
              animationDelay: phase === 'starting' ? `${i * 80}ms` : undefined,
            }}
          />
        );
      })}
    </div>
  );
}
