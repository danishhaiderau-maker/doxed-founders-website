'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { formatUsd, formatTierLabel, LIFECYCLE_STAGES } from '@dcf/utils';
import {
  createSimulatedRaise,
  fetchFounderDashboard,
  fetchProjectRoom,
  FounderDashboard,
  ProjectRoom,
  submitFounderApplication,
} from '@/lib/api';
import { ProjectLifecycleBar } from '@/components/lifecycle-bar';
import { StartupGenomePanel } from '@/components/startup-genome';

const TABS = ['Control center', 'Community', 'Demand', 'Build log', 'Launch'] as const;

const FOUNDER_STAGES = ['IDEA', 'BRAINSTORMING', 'PROTOTYPE', 'MVP', 'BETA', 'DEMAND_VALIDATION'];

export default function FounderDenPage() {
  const { data: session } = useSession();
  const [dashboard, setDashboard] = useState<FounderDashboard | null>(null);
  const [room, setRoom] = useState<ProjectRoom | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>('Control center');
  const [wizardStep, setWizardStep] = useState(0);
  const [showWizard, setShowWizard] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [appForm, setAppForm] = useState({
    projectName: '',
    websiteUrl: '',
    twitterHandle: '',
    githubUrl: '',
    videoUrl: '',
    ideaDescription: '',
    lifecycleStage: 'IDEA',
  });

  const [raiseForm, setRaiseForm] = useState({
    goalUsd: '500000',
    durationDays: '30',
    tokenAllocation: '15%',
    plannedLaunchDate: '',
  });

  const load = useCallback(async () => {
    if (!session?.accessToken) return;
    try {
      const dash = await fetchFounderDashboard(session.accessToken);
      setDashboard(dash);
      if (dash.primaryProjectSlug) {
        setRoom(await fetchProjectRoom(dash.primaryProjectSlug, session.accessToken));
      }
    } catch {
      setDashboard(null);
    }
  }, [session?.accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function submitApplication() {
    if (!session?.accessToken) return;
    setError(null);
    try {
      const result = await submitFounderApplication(appForm, session.accessToken);
      setMessage(`Founder profile live → /project/${result.projectSlug}`);
      setShowWizard(false);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Application failed');
    }
  }

  async function launchRaise() {
    if (!session?.accessToken || !room) return;
    try {
      await createSimulatedRaise(
        {
          projectId: room.id,
          goalUsd: Number(raiseForm.goalUsd),
          durationDays: Number(raiseForm.durationDays),
          tokenAllocation: raiseForm.tokenAllocation,
          plannedLaunchDate: raiseForm.plannedLaunchDate || undefined,
        },
        session.accessToken,
      );
      setMessage('Simulated raise is live — community can allocate paper capital.');
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start raise');
    }
  }

  if (!session) {
    return (
      <main className="min-h-screen bg-[#050508] px-6 py-20">
        <div className="mx-auto max-w-lg text-center">
          <h1 className="text-2xl font-bold">Founder-to-Market OS</h1>
          <p className="mt-4 text-zinc-400">
            Idea → validation → community → simulated raise → launch. Sign in to enter your control center.
          </p>
          <Link href="/login?callbackUrl=/founder-den" className="mt-6 inline-block rounded-xl bg-emerald-600 px-6 py-3 font-semibold text-white">
            Sign in
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <Link href="/" className="text-sm font-semibold text-white hover:text-emerald-400">
              Doxxed crypto
            </Link>
            <h1 className="mt-1 text-xl font-semibold">Founder control center</h1>
            <p className="text-xs text-zinc-500">
              Tier: {formatTierLabel(dashboard?.progressTier ?? 'EXPLORER')}
            </p>
          </div>
          <div className="flex gap-2">
            <Link href="/discover" className="rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-emerald-500/50">
              Discover
            </Link>
            <Link href="/build-feed" className="rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-emerald-500/50">
              Build feed
            </Link>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-6xl space-y-8 px-6 py-8">
        {message && <p className="text-sm text-emerald-300">{message}</p>}
        {error && <p className="text-sm text-red-300">{error}</p>}

        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {[
            ['Founder score', dashboard?.founderScore ?? '—'],
            ['Current stage', LIFECYCLE_STAGES.find((s) => s.key === dashboard?.currentStage)?.label ?? '—'],
            ['Followers', dashboard?.followers?.toLocaleString() ?? '0'],
            ['Build streak', `${dashboard?.buildStreakDays ?? 0} days`],
            ['Simulated demand', formatUsd(dashboard?.simulatedDemand ?? 0, 0)],
          ].map(([label, value]) => (
            <div key={label} className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
              <p className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</p>
              <p className="mt-1 text-lg font-semibold text-white">{value}</p>
            </div>
          ))}
        </section>

        {!dashboard?.hasFounderProfile && (
          <section className="rounded-2xl border border-emerald-500/30 bg-emerald-950/15 p-6">
            <h2 className="text-lg font-semibold text-emerald-200">Become a founder</h2>
            <p className="mt-2 text-sm text-zinc-400">
              Everyone starts as Explorer. Activate your founder profile to build in public and run simulated raises.
            </p>
            {!showWizard ? (
              <button
                type="button"
                onClick={() => setShowWizard(true)}
                className="mt-4 rounded-lg bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white"
              >
                Start founder application
              </button>
            ) : (
              <div className="mt-6 space-y-4">
                <div className="flex gap-2 text-xs text-zinc-500">
                  {['Project', 'Links', 'Idea', 'Stage'].map((s, i) => (
                    <span key={s} className={wizardStep === i ? 'text-emerald-300' : ''}>
                      {i + 1}. {s}
                    </span>
                  ))}
                </div>
                {wizardStep === 0 && (
                  <input
                    value={appForm.projectName}
                    onChange={(e) => setAppForm({ ...appForm, projectName: e.target.value })}
                    placeholder="Project name"
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-3 text-sm"
                  />
                )}
                {wizardStep === 1 && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <input value={appForm.websiteUrl} onChange={(e) => setAppForm({ ...appForm, websiteUrl: e.target.value })} placeholder="Website" className="rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-sm" />
                    <input value={appForm.twitterHandle} onChange={(e) => setAppForm({ ...appForm, twitterHandle: e.target.value })} placeholder="X handle" className="rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-sm" />
                    <input value={appForm.githubUrl} onChange={(e) => setAppForm({ ...appForm, githubUrl: e.target.value })} placeholder="GitHub" className="rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-sm" />
                    <input value={appForm.videoUrl} onChange={(e) => setAppForm({ ...appForm, videoUrl: e.target.value })} placeholder="Intro video URL" className="rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-sm" />
                  </div>
                )}
                {wizardStep === 2 && (
                  <textarea
                    value={appForm.ideaDescription}
                    onChange={(e) => setAppForm({ ...appForm, ideaDescription: e.target.value })}
                    rows={5}
                    placeholder="Describe your idea — what problem, who is it for?"
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-3 text-sm"
                  />
                )}
                {wizardStep === 3 && (
                  <select
                    value={appForm.lifecycleStage}
                    onChange={(e) => setAppForm({ ...appForm, lifecycleStage: e.target.value })}
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-4 py-3 text-sm"
                  >
                    {FOUNDER_STAGES.map((s) => (
                      <option key={s} value={s}>
                        {LIFECYCLE_STAGES.find((x) => x.key === s)?.label ?? s}
                      </option>
                    ))}
                  </select>
                )}
                <div className="flex gap-2">
                  {wizardStep > 0 && (
                    <button type="button" onClick={() => setWizardStep((s) => s - 1)} className="rounded-lg border border-zinc-700 px-4 py-2 text-sm">
                      Back
                    </button>
                  )}
                  {wizardStep < 3 ? (
                    <button type="button" onClick={() => setWizardStep((s) => s + 1)} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white">
                      Next
                    </button>
                  ) : (
                    <button type="button" onClick={submitApplication} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white">
                      Activate founder profile
                    </button>
                  )}
                </div>
              </div>
            )}
          </section>
        )}

        {room && (
          <>
            <ProjectLifecycleBar currentStage={room.lifecycleStage} />

            <div className="flex flex-wrap gap-2 border-b border-zinc-800 pb-3">
              {TABS.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTab(t)}
                  className={`rounded-lg px-3 py-1.5 text-sm ${
                    tab === t ? 'bg-emerald-500/20 font-semibold text-emerald-200' : 'text-zinc-500 hover:text-white'
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>

            {tab === 'Control center' && (
              <div className="grid gap-6 lg:grid-cols-2">
                <StartupGenomePanel genome={room.genome} />
                <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
                  <p className="text-xs uppercase tracking-widest text-zinc-500">Launch readiness</p>
                  <p className="mt-2 text-4xl font-bold text-emerald-300">{room.launchReadiness}%</p>
                  <p className="mt-4 text-sm text-zinc-400">
                    Cash available for allocations: {formatUsd(dashboard?.cashBalance ?? 0, 0)} · Top up at $1,000 for $25
                  </p>
                  {room.slug && (
                    <Link href={`/project/${room.slug}`} className="mt-4 inline-block text-sm text-emerald-400 hover:underline">
                      Open public project room →
                    </Link>
                  )}
                </div>
              </div>
            )}

            {tab === 'Launch' && (
              <div className="space-y-6">
                {!room.activeRaise ? (
                  <div className="rounded-xl border border-zinc-800 p-5">
                    <h3 className="font-semibold">Start simulated raise</h3>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                      <input value={raiseForm.goalUsd} onChange={(e) => setRaiseForm({ ...raiseForm, goalUsd: e.target.value })} placeholder="Funding goal USD" className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm" />
                      <input value={raiseForm.durationDays} onChange={(e) => setRaiseForm({ ...raiseForm, durationDays: e.target.value })} placeholder="Duration days" className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm" />
                      <input value={raiseForm.tokenAllocation} onChange={(e) => setRaiseForm({ ...raiseForm, tokenAllocation: e.target.value })} placeholder="Token allocation %" className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm" />
                      <input type="date" value={raiseForm.plannedLaunchDate} onChange={(e) => setRaiseForm({ ...raiseForm, plannedLaunchDate: e.target.value })} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm" />
                    </div>
                    <button type="button" onClick={launchRaise} className="mt-4 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white">
                      Launch simulation
                    </button>
                  </div>
                ) : (
                  <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/15 p-5">
                    <p className="font-semibold text-emerald-200">Active raise</p>
                    <p className="mt-2 text-sm text-zinc-400">
                      Goal {formatUsd(room.activeRaise.goalUsd, 0)} · Demand {formatUsd(room.activeRaise.totalAllocated, 0)} ·{' '}
                      {room.activeRaise.allocatorCount} investors · Conviction {room.activeRaise.convictionScore}%
                    </p>
                  </div>
                )}
                <div className="rounded-xl border border-zinc-800 p-5">
                  <p className="font-semibold">Request launchpad access</p>
                  <ul className="mt-3 space-y-1 text-sm">
                    {Object.entries(room.launchpadAccess.checks).map(([k, ok]) => (
                      <li key={k} className={ok ? 'text-emerald-300' : 'text-zinc-500'}>
                        {ok ? '✓' : '○'} {k.replace(/([A-Z])/g, ' $1')}
                      </li>
                    ))}
                  </ul>
                  {room.launchpadAccess.unlocked ? (
                    <p className="mt-3 text-sm text-emerald-300">Eligible — scouts can discover you on Discover.</p>
                  ) : (
                    <p className="mt-3 text-sm text-zinc-500">Keep building — requirements unlock automatically.</p>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
