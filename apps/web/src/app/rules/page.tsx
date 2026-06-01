'use client';

import Link from 'next/link';
import {
  INVESTIGATION_SCAM_THRESHOLD_PERCENT,
  INVESTIGATION_WINDOW_HOURS,
  LISTING_MIN_APPROVAL_PERCENT,
  MAX_TRUST_WEIGHT,
  POINTS,
  VALIDATION_LABELS,
  VOTING_WINDOW_HOURS,
  computeTrustWeight,
  computeVotingThreshold,
} from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';

export default function RulesPage() {
  const exampleVote = computeVotingThreshold(250);
  const exampleWeight = computeTrustWeight({
    verifiedAccount: true,
    contributorLevel: 3,
    reputationPoints: 1200,
    accountAgeDays: 45,
  });

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-zinc-500 hover:text-white">
              ← Home
            </Link>
            <h1 className="mt-1 text-2xl font-bold">Platform rules</h1>
            <p className="mt-1 text-sm text-zinc-400">
              How listing votes, trust investigations, DDollar, and agent rentals work — transparent math.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-3xl space-y-10 px-6 py-10 text-sm leading-relaxed text-zinc-300">
        <section>
          <h2 className="text-lg font-semibold text-white">DDollar (reputation points)</h2>
          <p className="mt-2">
            DDollar is the ecosystem participation currency stored as reputation points. It is not withdrawable and
            has no intrinsic cash value. Earn it by scouting listings, validating projects, paper trading, and
            founder contributions.
          </p>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-zinc-400">
            <li>Listing submit: +{POINTS.LISTING_SUBMIT} DDollar</li>
            <li>Cast a validation vote: +{POINTS.LISTING_VOTE} DDollar</li>
            <li>Helpful validation review (40+ chars): +{POINTS.VALIDATION_HELPFUL} DDollar</li>
            <li>Correct validation after approval: +{POINTS.VALIDATION_CORRECT} DDollar</li>
            <li>Scout bonus when admin approves: +{POINTS.LISTING_SCOUT_APPROVED} DDollar</li>
            <li>Confirmed scam report: +{POINTS.SCAM_CONFIRMED} DDollar</li>
          </ul>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-white">Listing community vote</h2>
          <p className="mt-2">
            After a scout submits a project, the community has <strong className="text-white">{VOTING_WINDOW_HOURS} hours</strong>{' '}
            to validate it using six categories (not just yes/no). Weighted approval must reach{' '}
            <strong className="text-white">{LISTING_MIN_APPROVAL_PERCENT}%</strong> with enough distinct voters to
            fast-track admin review. After 48h, every listing moves to the admin queue regardless — votes are signal,
            not automatic approval.
          </p>
          <p className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/50 p-4 text-xs text-zinc-400">
            Example at 250 active users: need {exampleVote.requiredVoters} voters at {exampleVote.minYesPercent}% weighted
            yes · formula: {exampleVote.formula}
          </p>
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {Object.entries(VALIDATION_LABELS).map(([key, label]) => (
              <div key={key} className="rounded-lg border border-zinc-800 px-3 py-2 text-xs">
                {label}
              </div>
            ))}
          </div>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-white">Trust weight (vote power)</h2>
          <p className="mt-2">
            Trust weight is earned — not purchased. Base 1 + verified identity +1 + scout level 0–3 + community
            reputation 0–3 + account age 0–2, capped at{' '}
            <strong className="text-white">{MAX_TRUST_WEIGHT}</strong>.
          </p>
          <p className="mt-2 text-zinc-400">
            Example verified analyst (level 3, 1,200 DDollar, 45 days): weight {exampleWeight}.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-white">Investigations & delist</h2>
          <p className="mt-2">
            Listed projects can receive trust reports. When weighted suspicious signal reaches{' '}
            <strong className="text-white">{INVESTIGATION_SCAM_THRESHOLD_PERCENT}%</strong> with minimum voters, the
            case escalates to admin review — there is no instant delist. Investigations run for{' '}
            {INVESTIGATION_WINDOW_HOURS}h before auto-escalation. Admins resolve KEEP or DELIST; accurate scam reporters
            earn +{POINTS.SCAM_CONFIRMED} DDollar.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-white">Trading agent rental</h2>
          <p className="mt-2">
            Following a live trading agent costs DDollar per day (rental fee shown on the agent card). Followers receive
            alerts when the agent opens or closes positions. Paper research agents may be free during beta.
          </p>
          <Link href="/agent-hub" className="mt-3 inline-block text-emerald-400 hover:underline">
            Open Agents →
          </Link>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-white">Related</h2>
          <div className="mt-3 flex flex-wrap gap-4 text-emerald-400">
            <Link href="/trust-center" className="hover:underline">
              Trust Center
            </Link>
            <Link href="/scout-votes" className="hover:underline">
              Scout vote board
            </Link>
            <Link href="/reputation" className="hover:underline">
              DDollar leaderboard
            </Link>
          </div>
        </section>
      </div>
    </main>
  );
}
