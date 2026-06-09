'use client';

import { motion } from 'framer-motion';
import { formatPercent } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';

function bubbleSize(agent: TradingAgentSummary): number {
  const score = Math.min(
    100,
    agent.followerCount * 2 +
      agent.tradeCount * 0.5 +
      Math.abs(agent.netReturnPct) * 2 +
      (agent.botConnected ? 20 : 0),
  );
  if (score >= 76) return 120;
  if (score >= 51) return 96;
  if (score >= 26) return 72;
  return 56;
}

function layout(count: number, w: number, h: number) {
  const cx = w / 2;
  const cy = h / 2;
  const golden = Math.PI * (3 - Math.sqrt(5));
  const maxR = Math.min(w, h) * 0.38;
  const pad = 56;
  return Array.from({ length: count }, (_, i) => {
    const t = (i + 0.5) / count;
    const r = maxR * Math.sqrt(t);
    const angle = i * golden;
    return {
      x: Math.min(w - pad, Math.max(pad, cx + Math.cos(angle) * r)),
      y: Math.min(h - pad, Math.max(pad, cy + Math.sin(angle) * r * 0.88)),
    };
  });
}

export function AgentBubbleMap({ agents }: { agents: TradingAgentSummary[] }) {
  const w = 720;
  const h = 400;
  const positions = layout(agents.length, w, h);
  const live = agents.filter((a) => a.status !== 'PAUSED');

  if (live.length === 0) {
    return (
      <p className="flex h-[400px] items-center justify-center text-sm text-zinc-500">
        No live agents yet — check back soon.
      </p>
    );
  }

  return (
    <div className="relative mx-auto w-full max-w-3xl overflow-hidden rounded-2xl border border-zinc-800/80 bg-[#030308]" style={{ height: h }}>
      <div
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          background:
            'radial-gradient(ellipse 70% 55% at 50% 45%, rgba(16,185,129,0.2), transparent 60%), radial-gradient(ellipse 50% 40% at 80% 70%, rgba(139,92,246,0.25), transparent 50%)',
        }}
      />
      {live.map((agent, i) => {
        const size = bubbleSize(agent);
        const pos = positions[i] ?? { x: w / 2, y: h / 2 };
        const isLive = agent.botConnected && agent.status !== 'PAUSED';
        return (
          <motion.a
            key={agent.slug}
            href={`/agent-hub/${agent.slug}`}
            className="absolute flex flex-col items-center justify-center rounded-full border-2 border-emerald-500/50 bg-gradient-to-br from-emerald-950/80 to-zinc-950 text-center shadow-lg shadow-emerald-900/30 transition hover:scale-105 hover:border-emerald-400"
            style={{
              width: size,
              height: size,
              left: pos.x - size / 2,
              top: pos.y - size / 2,
              zIndex: 10 + Math.round(agent.netReturnPct),
            }}
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: i * 0.05 }}
            title={`${agent.name} — ${formatPercent(agent.netReturnPct)} return`}
          >
            <span className="text-[10px] font-bold uppercase tracking-wide text-emerald-300">
              {agent.assetSymbol}
            </span>
            <span className="mt-0.5 max-w-[85%] truncate text-[9px] font-semibold text-white">
              {agent.name.split(' ')[0]}
            </span>
            <span className={`text-[10px] font-bold ${agent.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {formatPercent(agent.netReturnPct)}
            </span>
            {isLive && (
              <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-emerald-400 ring-2 ring-zinc-950" />
            )}
          </motion.a>
        );
      })}
      <p className="absolute bottom-3 left-0 right-0 text-center text-[10px] text-zinc-600">
        Bubble size = followers + trades + performance · click to open
      </p>
    </div>
  );
}
