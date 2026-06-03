'use client';

import { useEffect, useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { formatUsd } from '@dcf/utils';
import { fetchPlatformAdoptionMetrics, type PlatformAdoptionMetrics } from '@/lib/api';

function formatDayLabel(isoDate: string) {
  const d = new Date(`${isoDate}T12:00:00Z`);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

export function LandingPlatformAdoption() {
  const [data, setData] = useState<PlatformAdoptionMetrics | null>(null);

  useEffect(() => {
    fetchPlatformAdoptionMetrics(14)
      .then(setData)
      .catch(() => setData(null));
  }, []);

  const chartData =
    data?.series.map((d) => ({
      ...d,
      label: formatDayLabel(d.date),
    })) ?? [];

  const totals = data?.totals;

  return (
    <section
      aria-label="Platform adoption"
      className="rounded-2xl border border-zinc-800/80 bg-zinc-950/40 p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-400">
            Platform adoption
          </p>
          <h2 className="mt-1 text-lg font-bold text-white sm:text-xl">
            AI tokens & real traction — day by day
          </h2>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-zinc-500">
            Input/output tokens are logged on every Mission Control AI call (Copilot, drafts, Quick
            Build). DDollar volume, GitHub sync events, and build posts show trader-visible activity
            — not vanity metrics.
          </p>
        </div>
        {totals && (
          <div className="flex flex-wrap gap-3 text-right text-[10px] uppercase tracking-wide text-zinc-500">
            <span>
              <span className="block text-base font-bold text-sky-300">{formatTokens(totals.tokensIn)}</span>
              Tokens in (14d)
            </span>
            <span>
              <span className="block text-base font-bold text-violet-300">
                {formatTokens(totals.tokensOut)}
              </span>
              Tokens out (14d)
            </span>
            <span>
              <span className="block text-base font-bold text-emerald-300">
                {formatUsd(totals.ddollarVolume, 0)}
              </span>
              DDollar volume
            </span>
          </div>
        )}
      </div>

      <div className="mt-5 h-56 w-full sm:h-64">
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="tokensIn" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.45} />
                  <stop offset="100%" stopColor="#38bdf8" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="tokensOut" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#a78bfa" stopOpacity={0.45} />
                  <stop offset="100%" stopColor="#a78bfa" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: '#71717a', fontSize: 10 }} interval="preserveStartEnd" />
              <YAxis
                tick={{ fill: '#71717a', fontSize: 10 }}
                tickFormatter={(v) => formatTokens(Number(v))}
                width={44}
              />
              <Tooltip
                contentStyle={{
                  background: '#09090b',
                  border: '1px solid #3f3f46',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(value: number, name: string) => [
                  name === 'ddollarVolume' ? formatUsd(value, 0) : formatTokens(value),
                  name === 'tokensIn'
                    ? 'Tokens in'
                    : name === 'tokensOut'
                      ? 'Tokens out'
                      : name === 'ddollarVolume'
                        ? 'DDollar volume'
                        : name,
                ]}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Area
                type="monotone"
                dataKey="tokensIn"
                name="Tokens in"
                stroke="#38bdf8"
                fill="url(#tokensIn)"
                strokeWidth={2}
              />
              <Area
                type="monotone"
                dataKey="tokensOut"
                name="Tokens out"
                stroke="#a78bfa"
                fill="url(#tokensOut)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-zinc-800 text-sm text-zinc-600">
            Adoption chart fills as founders use AI on the platform.
          </div>
        )}
      </div>

      {chartData.length > 0 && (
        <div className="mt-4 h-36 w-full">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            DDollar + shipping signals (daily)
          </p>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: '#71717a', fontSize: 9 }} hide />
              <YAxis tick={{ fill: '#71717a', fontSize: 9 }} width={36} />
              <Tooltip
                contentStyle={{
                  background: '#09090b',
                  border: '1px solid #3f3f46',
                  borderRadius: 8,
                  fontSize: 11,
                }}
              />
              <Bar dataKey="githubEvents" name="GitHub events" fill="#3b82f6" radius={[2, 2, 0, 0]} />
              <Bar dataKey="buildPosts" name="Build posts" fill="#22c55e" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {data && data.projects.length > 0 && (
        <div className="mt-5 overflow-x-auto">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Project traction (last {data.days} days)
          </p>
          <table className="mt-2 w-full min-w-[520px] text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500">
                <th className="py-2 pr-3 font-medium">Project</th>
                <th className="py-2 pr-3 font-medium">Activity</th>
                <th className="py-2 pr-3 font-medium">Tokens in/out</th>
                <th className="py-2 pr-3 font-medium">DDollar vol.</th>
                <th className="py-2 font-medium">GitHub · Posts</th>
              </tr>
            </thead>
            <tbody>
              {data.projects.map((p) => (
                <tr key={p.slug} className="border-b border-zinc-800/60 text-zinc-300">
                  <td className="py-2 pr-3 font-semibold text-white">
                    {p.ticker}
                    <span className="ml-1 font-normal text-zinc-600">· {p.name}</span>
                  </td>
                  <td className="py-2 pr-3 text-emerald-400">{p.activityScore}</td>
                  <td className="py-2 pr-3">
                    {formatTokens(p.tokensIn)} / {formatTokens(p.tokensOut)}
                  </td>
                  <td className="py-2 pr-3">{formatUsd(p.ddollarVolume, 0)}</td>
                  <td className="py-2">
                    {p.githubEvents} · {p.buildPosts}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
