'use client';

import Link from 'next/link';
import {
  ORACLE_SCORE_FORMULA_NOTE,
  PREDICTION_MARKET_CREATE_COST_DD,
  PREDICTION_MARKET_MIN_STAKE_DD,
  PREDICTION_CONVICTION_MULTIPLIERS,
  TRADER_SCORE_FORMULA_NOTE,
} from '@dcf/utils';

const MARKET_TYPES = [
  {
    title: 'Founder markets',
    examples: ['Ship mobile app before July 31?', 'Reach 1,000 users this quarter?'],
    resolution: 'GitHub proof · release proof · founder milestone',
  },
  {
    title: 'Project / conviction markets',
    examples: ['Reach $50M market cap before September?', 'Hold top-100 mindshare slot?'],
    resolution: 'DexScreener · market cap APIs · on-chain metrics',
  },
  {
    title: 'Builder markets',
    examples: ['20 commits this week?', 'Roadmap milestone on time?'],
    resolution: 'GitHub API · public build feed',
  },
  {
    title: 'Launch markets',
    examples: ['Mainnet before August?', 'Token live on CEX?'],
    resolution: 'Founder attestation · admin · community proof',
  },
  {
    title: 'Trading conviction markets',
    examples: ['GRID outperform BTC in 30 days?', 'Position thesis plays out?'],
    resolution: 'Paper trade record · price feeds · community challenge',
  },
] as const;

const RESOLUTION_TYPES = [
  { type: 'Automatic', when: 'Price, market cap, volume, GitHub commits, launch timestamps', preferred: true },
  { type: 'Community (Scout)', when: 'Subjective delivery — scout vote with public evidence', preferred: false },
  { type: 'Admin', when: 'Dispute, fraud, or missing data source only', preferred: false },
] as const;

export function PredictionConstitutionPanel() {
  return (
    <div className="space-y-6 text-sm leading-relaxed text-zinc-300">
      <section className="rounded-xl border border-indigo-500/30 bg-indigo-950/20 p-5">
        <h2 className="text-lg font-bold text-white">Prediction Market Constitution</h2>
        <p className="mt-2 text-zinc-400">
          This is not another Polymarket clone. Markets are{' '}
          <strong className="text-indigo-200">public conviction</strong> tied to doxxed founders,
          curated projects, traders, and scouts — validated by proof, not hype.
        </p>
        <ul className="mt-4 list-inside list-disc space-y-1.5 text-zinc-400">
          <li>Every market must name a question, resolution date, resolution method, and evidence source.</li>
          <li>
            Markets originate from <strong className="text-white">founders, community, traders, or admins</strong> — never from silent AI creation.
          </li>
          <li>AI may only <strong className="text-white">suggest</strong> drafts on project pages; a human must approve.</li>
          <li>DDollar is the only stake currency — same ecosystem as paper trading and scout votes.</li>
        </ul>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
          <h3 className="font-semibold text-white">DDollar economics</h3>
          <dl className="mt-3 space-y-2 text-xs">
            <div className="flex justify-between gap-4 border-b border-zinc-800/80 py-2">
              <dt className="text-zinc-500">Create a market</dt>
              <dd className="font-mono text-indigo-200">{PREDICTION_MARKET_CREATE_COST_DD} DD</dd>
            </div>
            <div className="flex justify-between gap-4 border-b border-zinc-800/80 py-2">
              <dt className="text-zinc-500">Minimum stake per vote</dt>
              <dd className="font-mono text-indigo-200">{PREDICTION_MARKET_MIN_STAKE_DD} DD</dd>
            </div>
            <div className="flex justify-between gap-4 border-b border-zinc-800/80 py-2">
              <dt className="text-zinc-500">Winning side</dt>
              <dd className="text-right text-emerald-300">Parimutuel pool share by stake</dd>
            </div>
            <div className="flex justify-between gap-4 py-2">
              <dt className="text-zinc-500">Wrong side</dt>
              <dd className="text-right text-red-300">Stake stays in pool (reputation hit)</dd>
            </div>
          </dl>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
          <h3 className="font-semibold text-white">Conviction levels (ranking)</h3>
          <p className="mt-2 text-xs text-zinc-500">
            Every vote should declare conviction. Correct extreme calls earn more Oracle points; wrong
            extreme calls lose more.
          </p>
          <ul className="mt-3 space-y-2">
            {(Object.entries(PREDICTION_CONVICTION_MULTIPLIERS) as [string, number][]).map(
              ([level, mult]) => (
                <li
                  key={level}
                  className="flex items-center justify-between rounded-lg bg-black/40 px-3 py-2 text-xs"
                >
                  <span className="font-medium text-zinc-200">{level}</span>
                  <span className="font-mono text-indigo-300">×{mult}</span>
                </li>
              ),
            )}
          </ul>
        </div>
      </section>

      <section>
        <h3 className="font-semibold text-white">Market categories we prioritize</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {MARKET_TYPES.map((m) => (
            <div key={m.title} className="rounded-xl border border-zinc-800 bg-black/30 p-4">
              <p className="text-xs font-bold uppercase tracking-wide text-indigo-300">{m.title}</p>
              <ul className="mt-2 space-y-1 text-xs text-zinc-400">
                {m.examples.map((ex) => (
                  <li key={ex}>· {ex}</li>
                ))}
              </ul>
              <p className="mt-2 text-[10px] text-zinc-500">
                Resolution: {m.resolution}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3 className="font-semibold text-white">Resolution engine</h3>
        <div className="mt-3 space-y-2">
          {RESOLUTION_TYPES.map((r) => (
            <div
              key={r.type}
              className={`rounded-lg border px-4 py-3 ${
                r.preferred ? 'border-emerald-500/30 bg-emerald-950/15' : 'border-zinc-800 bg-zinc-950/40'
              }`}
            >
              <p className="text-xs font-bold uppercase tracking-wide text-zinc-400">
                {r.type}
                {r.preferred && (
                  <span className="ml-2 text-emerald-400">preferred</span>
                )}
              </p>
              <p className="mt-1 text-xs text-zinc-400">{r.when}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-amber-500/25 bg-amber-950/15 p-5">
        <h3 className="font-semibold text-amber-100">Three separate reputations</h3>
        <p className="mt-2 text-xs text-amber-200/80">
          Great trader ≠ great predictor ≠ great founder. We never merge these leaderboards.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <div className="rounded-lg bg-black/30 p-3">
            <p className="text-xs font-bold text-emerald-300">Trader score</p>
            <p className="mt-1 text-[10px] text-zinc-500">{TRADER_SCORE_FORMULA_NOTE}</p>
            <Link href="/predict?tab=traders" className="mt-2 inline-block text-xs text-emerald-400 hover:underline">
              Trading rank →
            </Link>
          </div>
          <div className="rounded-lg bg-black/30 p-3">
            <p className="text-xs font-bold text-indigo-300">Oracle score</p>
            <p className="mt-1 text-[10px] text-zinc-500">{ORACLE_SCORE_FORMULA_NOTE}</p>
            <Link href="/predict?tab=oracle" className="mt-2 inline-block text-xs text-indigo-400 hover:underline">
              Oracle rank →
            </Link>
          </div>
          <div className="rounded-lg bg-black/30 p-3">
            <p className="text-xs font-bold text-violet-300">Founder validation</p>
            <p className="mt-1 text-[10px] text-zinc-500">
              Scout votes, GitHub shipping, Trust Center investigations
            </p>
            <Link href="/trust-center" className="mt-2 inline-block text-xs text-violet-400 hover:underline">
              Trust Center →
            </Link>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
        <h3 className="font-semibold text-white">What we block</h3>
        <ul className="mt-3 list-inside list-disc space-y-1 text-xs text-zinc-500">
          <li>Generic gambling questions unrelated to ecosystem projects</li>
          <li>AI auto-launching markets without human approval</li>
          <li>Duplicate or misleading questions (DDollar penalty + reputation loss)</li>
          <li>Fake engagement and bot voting</li>
        </ul>
      </section>

      <div className="flex flex-wrap gap-3">
        <Link
          href="/predict?tab=markets"
          className="rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500"
        >
          Browse live markets
        </Link>
        <Link
          href="/paper-trading"
          className="rounded-lg border border-amber-500/40 px-4 py-2.5 text-sm font-semibold text-amber-100 hover:bg-amber-950/30"
        >
          Trading Alpha desk
        </Link>
      </div>
    </div>
  );
}
