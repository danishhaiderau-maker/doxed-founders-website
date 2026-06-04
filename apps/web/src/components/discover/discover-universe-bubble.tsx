'use client';

import Link from 'next/link';
import { motion } from 'framer-motion';
import {
  DISCOVER_UNIVERSE_COLORS,
  bubbleRadiusFromActivityScore,
  formatUsd,
  type DiscoverUniverseStage,
} from '@dcf/utils';
import type { DiscoverUniverseProject } from '@/lib/api';

function formatDd(amount: number) {
  if (amount >= 1000) return `${Math.round(amount / 1000)}K DD`;
  return `${Math.round(amount)} DD`;
}

export function DiscoverUniverseBubble({
  project,
  x,
  y,
  index,
}: {
  project: DiscoverUniverseProject;
  x: number;
  y: number;
  index: number;
}) {
  const size = bubbleRadiusFromActivityScore(project.activityScore);
  const stage = project.universeStage as DiscoverUniverseStage;
  const colors = DISCOVER_UNIVERSE_COLORS[stage] ?? DISCOVER_UNIVERSE_COLORS.building;
  const isLive = stage === 'live';
  const borderPx = isLive ? 4 : 3;
  const outerRingPx = isLive ? 3 : 2;
  const floatDuration = 4 + (index % 5) * 0.6;

  return (
    <motion.div
      className="absolute"
      style={{
        left: x - size / 2,
        top: y - size / 2,
        width: size,
        height: size,
        zIndex: Math.round(project.activityScore),
      }}
      initial={{ opacity: 0, scale: 0.6 }}
      animate={{
        opacity: 1,
        scale: 1,
        y: [0, -6, 0],
      }}
      transition={{
        opacity: { duration: 0.4, delay: index * 0.03 },
        scale: { duration: 0.4, delay: index * 0.03 },
        y: { duration: floatDuration, repeat: Infinity, ease: 'easeInOut' },
      }}
    >
      <Link
        href={`/project/${project.slug}`}
        className="group relative block h-full w-full"
        title={project.name}
      >
        <div
          className="absolute inset-0 rounded-full opacity-60 blur-md transition group-hover:opacity-90"
          style={{ background: colors.glow }}
        />
        <div
          className="relative flex h-full w-full flex-col items-center justify-center overflow-hidden rounded-full transition group-hover:scale-105"
          style={{
            borderWidth: borderPx,
            borderStyle: 'solid',
            borderColor: colors.border,
            background: `radial-gradient(circle at 35% 30%, ${colors.color}55, #0a0a0f 70%)`,
            boxShadow: `0 0 0 ${outerRingPx}px ${colors.border}, 0 0 ${isLive ? 40 : 28}px ${colors.glow}, inset 0 0 20px ${colors.color}22`,
          }}
        >
          {project.logoUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={project.logoUrl}
              alt=""
              className="h-[52%] w-[52%] rounded-full object-cover"
            />
          ) : (
            <span className="text-sm font-bold text-white">{project.ticker.slice(0, 3)}</span>
          )}
          <span className="mt-0.5 max-w-[85%] truncate text-[9px] font-bold uppercase tracking-wide text-white/90">
            {project.ticker}
          </span>
          {project.ddInflow24h > 0 && (
            <span className="text-[8px] font-medium text-emerald-300/90">
              {formatDd(project.ddInflow24h)}
            </span>
          )}
        </div>
        <span
          className="absolute -bottom-0.5 left-1/2 flex h-5 min-w-5 -translate-x-1/2 items-center justify-center rounded-full px-1 text-[9px] font-bold text-white shadow-lg"
          style={{ background: colors.color }}
        >
          {project.activityScore}
        </span>
        {project.recentlyListed && (
          <span
            className="absolute -right-0.5 -top-0.5 rounded-full border border-violet-400/60 bg-violet-950 px-1 py-0.5 text-[7px] font-bold uppercase tracking-wide text-violet-200"
            title="Listed on platform within the last 14 days"
          >
            New
          </span>
        )}

        {/* Hover preview card */}
        <div className="pointer-events-none absolute left-1/2 top-full z-50 mt-3 w-52 -translate-x-1/2 scale-95 rounded-xl border border-zinc-700/80 bg-zinc-950/95 p-3 opacity-0 shadow-2xl backdrop-blur-md transition group-hover:scale-100 group-hover:opacity-100">
          <p className="font-semibold text-white">{project.name}</p>
          {project.lastActivityPreview && (
            <p className="mt-1 line-clamp-2 text-[11px] text-zinc-400">
              {project.lastActivityPreview.text}
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-2 text-[10px]">
            {project.ddInflow24h > 0 && (
              <span className="text-emerald-400">+{formatUsd(project.ddInflow24h, 0)}</span>
            )}
            <span className="text-zinc-500">{project.convictionScore} conviction</span>
          </div>
        </div>
      </Link>
    </motion.div>
  );
}
