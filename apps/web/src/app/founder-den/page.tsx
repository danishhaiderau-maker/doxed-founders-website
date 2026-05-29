'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { formatUsd, formatTierLabel, LIFECYCLE_STAGES } from '@dcf/utils';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { FounderJourneyProgress } from '@/components/founder-journey-progress';
import { FounderOsPanel } from '@/components/founder-os-panel';
import { DiscoverProjectCard } from '@/components/discover-project-card';
import {
  createBuildPost,
  createSimulatedRaise,
  EcosystemPulse,
  fetchEcosystemPulse,
  fetchFounderDashboard,
  fetchProjectRoom,
  FounderDashboard,
  ProjectRoom,
  submitFounderApplication,
} from '@/lib/api';

const FOUNDER_STAGES = ['IDEA', 'BRAINSTORMING', 'PROTOTYPE', 'MVP', 'BETA', 'DEMAND_VALIDATION'];

function OsSection({
  title,
  subtitle,
  disabled,
  id,
  children,
}: {
  title: string;
  subtitle: string;
  disabled?: boolean;
  id?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      className={`rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5 ${
        disabled ? 'opacity-60' : ''
      }`}
    >
      <h3 className="font-semibold text-white">{title}</h3>
      <p className="mt-1 text-xs text-zinc-500">{subtitle}</p>
      <div className="mt-4">{children}</div>
      {disabled && (
        <p className="mt-3 text-xs text-amber-400/90">Activate your founder profile below to edit this section.</p>
      )}
    </section>
  );
}

export default function FounderDenPage() {
  const { data: session } = useSession();
  const [pulse, setPulse] = useState<EcosystemPulse | null>(null);
  const [dashboard, setDashboard] = useState<FounderDashboard | null>(null);
  const [room, setRoom] = useState<ProjectRoom | null>(null);
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

  const [buildForm, setBuildForm] = useState({ headline: '', body: '', dayNumber: '' });
  const [raiseForm, setRaiseForm] = useState({
    goalUsd: '500000',
    durationDays: '30',
    tokenAllocation: '15%',
    plannedLaunchDate: '',
  });

  const hasFounder = dashboard?.hasFounderProfile ?? false;
  const currentStage = room?.lifecycleStage ?? dashboard?.currentStage ?? 'IDEA';

  const load = useCallback(async () => {
    fetchEcosystemPulse().then(setPulse).catch(() => setPulse(null));
    if (!session?.accessToken) return;
    try {
      const dash = await fetchFounderDashboard(session.accessToken);
      setDashboard(dash);
      if (dash.primaryProjectSlug) {
        setRoom(await fetchProjectRoom(dash.primaryProjectSlug, session.accessToken));
      } else {
        setRoom(null);
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
      setMessage(`You're live → /project/${result.projectSlug}`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not activate profile');
    }
  }

  async function postBuildUpdate() {
    if (!session?.accessToken || !hasFounder) return;
    try {
      await createBuildPost(
        {
          headline: buildForm.headline.trim(),
          body: buildForm.body.trim(),
          dayNumber: buildForm.dayNumber ? Number(buildForm.dayNumber) : undefined,
        },
        session.accessToken,
      );
      setMessage('Build update published');
      setBuildForm({ headline: '', body: '', dayNumber: '' });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not publish');
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
      setMessage('Simulated raise is live');
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start raise');
    }
  }

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Founder OS</h1>
            <p className="text-sm text-zinc-500">
              Build in public · GitHub → translate → publish · Credits · Community · Raise · Launch
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl space-y-8 px-6 py-8">
        {message && <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-2 text-sm text-emerald-200">{message}</p>}
        {error && <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-2 text-sm text-red-300">{error}</p>}

        {/* Journey — always visible, never empty zeros */}
        <FounderJourneyProgress
          currentStage={currentStage}
          label={hasFounder ? 'Your journey' : 'The founder journey — start anywhere'}
        />

        {/* Three paths */}
        <section className="grid gap-4 md:grid-cols-3">
          {[
            {
              emoji: '💡',
              title: 'Start an idea',
              desc: 'Validate demand before you build. Polls, followers, simulated interest.',
              href: '#founder-os',
            },
            {
              emoji: '🛠',
              title: 'Build publicly',
              desc: 'Post daily updates. GitHub, videos, build streak — earn trust in public.',
              href: '#build',
            },
            {
              emoji: '🚀',
              title: 'Launch a project',
              desc: 'Simulated raise, launch readiness score, launchpad access when ready.',
              href: '#launch',
            },
          ].map((path) => (
            <a
              key={path.title}
              href={path.href}
              className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6 transition hover:border-emerald-500/40 hover:bg-zinc-900/80"
            >
              <span className="text-3xl">{path.emoji}</span>
              <h2 className="mt-3 text-lg font-semibold text-white">{path.title}</h2>
              <p className="mt-2 text-sm text-zinc-400">{path.desc}</p>
            </a>
          ))}
        </section>

        {/* Ecosystem pulse — makes page feel alive */}
        {pulse && (
          <section className="grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Recent founder activity</h2>
              <ul className="mt-3 space-y-3">
                {pulse.recentActivity.slice(0, 6).map((post) => (
                  <li key={post.id} className="rounded-xl border border-zinc-800 bg-zinc-900/30 px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                      {post.dayNumber != null && (
                        <span className="font-semibold text-emerald-500">Day {post.dayNumber}</span>
                      )}
                      <Link href={`/founder/${post.founder.slug}`} className="hover:text-emerald-400">
                        {post.founder.name}
                      </Link>
                      {post.project && (
                        <Link href={`/project/${post.project.slug}`} className="text-zinc-600 hover:text-white">
                          · {post.project.name}
                        </Link>
                      )}
                    </div>
                    <p className="mt-1 font-medium text-white">{post.headline}</p>
                    <p className="mt-1 line-clamp-2 text-sm text-zinc-500">{post.body}</p>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Ecosystem pulse</h2>
              <div className="mt-3 space-y-2 rounded-xl border border-zinc-800 bg-zinc-900/30 p-4 text-sm">
                <p className="flex justify-between"><span className="text-zinc-500">Live tokens</span><span className="font-semibold text-purple-300">{pulse.liveTokenCount}</span></p>
                <p className="flex justify-between"><span className="text-zinc-500">Building</span><span className="font-semibold text-emerald-300">{pulse.buildingCount}</span></p>
                <p className="flex justify-between"><span className="text-zinc-500">Idea stage</span><span className="font-semibold text-blue-300">{pulse.ideaCount}</span></p>
              </div>
              <Link href="/discover" className="mt-4 inline-block text-sm text-emerald-400 hover:underline">
                Explore bubble map →
              </Link>
            </div>
          </section>
        )}

        {/* Trending */}
        {pulse && pulse.trendingProjects.length > 0 && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Trending projects</h2>
            <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {pulse.trendingProjects.slice(0, 3).map((p) => (
                <DiscoverProjectCard key={p.slug} project={p} />
              ))}
            </div>
          </section>
        )}

        {/* Full Founder OS — everything visible */}
        <section id="founder-os">
          <h2 className="text-lg font-semibold text-white">Founder operating system</h2>
          <p className="mt-1 text-sm text-zinc-500">Every workflow visible on day one — fill in what applies to you.</p>

          {!session && (
            <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-sm text-amber-100">
              <Link href="/login?callbackUrl=/founder-den" className="font-semibold underline">
                Sign in
              </Link>{' '}
              to save your profile and run simulated raises with $10,000 paper capital.
            </div>
          )}

          {session?.accessToken && !hasFounder && (
            <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-sm text-amber-100">
              <strong className="text-white">Activate founder profile</strong> below to unlock GitHub
              sync, Cursor Build Room, stack integrations, and Publish Everywhere.
            </div>
          )}

          {session?.accessToken && (
            <div className="mt-6">
              <FounderOsPanel
                accessToken={session.accessToken}
                founderCredits={dashboard?.founderCredits}
                communityRewardPool={dashboard?.communityRewardPool}
                projectId={room?.id}
                onRefresh={load}
                founderActive={hasFounder}
              />
            </div>
          )}

          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <OsSection title="Founder profile" subtitle="Video · GitHub · Website · X" disabled={!hasFounder}>
              <div className="grid gap-3 sm:grid-cols-2">
                <input value={appForm.videoUrl} onChange={(e) => setAppForm({ ...appForm, videoUrl: e.target.value })} placeholder="Intro video URL" disabled={hasFounder} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
                <input value={appForm.githubUrl} onChange={(e) => setAppForm({ ...appForm, githubUrl: e.target.value })} placeholder="GitHub" disabled={hasFounder} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
                <input value={appForm.websiteUrl} onChange={(e) => setAppForm({ ...appForm, websiteUrl: e.target.value })} placeholder="Website" disabled={hasFounder} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
                <input value={appForm.twitterHandle} onChange={(e) => setAppForm({ ...appForm, twitterHandle: e.target.value })} placeholder="X handle" disabled={hasFounder} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
              </div>
            </OsSection>

            <OsSection title="Project" subtitle="Name · description · stage" disabled={!hasFounder}>
              <input value={appForm.projectName} onChange={(e) => setAppForm({ ...appForm, projectName: e.target.value })} placeholder="Project name" disabled={hasFounder} className="mb-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
              <textarea value={appForm.ideaDescription} onChange={(e) => setAppForm({ ...appForm, ideaDescription: e.target.value })} placeholder="What are you building?" rows={3} disabled={hasFounder} className="mb-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
              <select value={appForm.lifecycleStage} onChange={(e) => setAppForm({ ...appForm, lifecycleStage: e.target.value })} disabled={hasFounder} className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50">
                {FOUNDER_STAGES.map((s) => (
                  <option key={s} value={s}>{LIFECYCLE_STAGES.find((x) => x.key === s)?.label ?? s}</option>
                ))}
              </select>
              {!hasFounder && session && (
                <button type="button" onClick={submitApplication} className="mt-4 w-full rounded-lg bg-emerald-600 py-2.5 text-sm font-semibold text-white">
                  Activate founder profile
                </button>
              )}
              {hasFounder && room && (
                <Link href={`/project/${room.slug}`} className="mt-4 inline-block text-sm text-emerald-400 hover:underline">
                  View public project room →
                </Link>
              )}
            </OsSection>

            <OsSection title="Validation" subtitle="Simulated raise · demand · followers" disabled={!hasFounder} id="launch">
              {room?.activeRaise ? (
                <div className="text-sm text-zinc-300">
                  <p>Goal {formatUsd(room.activeRaise.goalUsd, 0)}</p>
                  <p className="mt-1">Demand {formatUsd(room.activeRaise.totalAllocated, 0)} · {room.activeRaise.allocatorCount} backers</p>
                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-zinc-800">
                    <div className="h-full bg-emerald-500" style={{ width: `${room.activeRaise.convictionScore}%` }} />
                  </div>
                </div>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2">
                  <input value={raiseForm.goalUsd} onChange={(e) => setRaiseForm({ ...raiseForm, goalUsd: e.target.value })} placeholder="Funding goal" disabled={!hasFounder} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
                  <input value={raiseForm.durationDays} onChange={(e) => setRaiseForm({ ...raiseForm, durationDays: e.target.value })} placeholder="Days" disabled={!hasFounder} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
                  <input value={raiseForm.tokenAllocation} onChange={(e) => setRaiseForm({ ...raiseForm, tokenAllocation: e.target.value })} placeholder="Token %" disabled={!hasFounder} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
                  <input type="date" value={raiseForm.plannedLaunchDate} onChange={(e) => setRaiseForm({ ...raiseForm, plannedLaunchDate: e.target.value })} disabled={!hasFounder} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
                  <button type="button" onClick={launchRaise} disabled={!hasFounder} className="sm:col-span-2 rounded-lg border border-emerald-500/40 py-2 text-sm text-emerald-200 disabled:opacity-40">
                    Launch simulated raise
                  </button>
                </div>
              )}
              {hasFounder && (
                <p className="mt-3 text-xs text-zinc-500">
                  Followers: {dashboard?.followers ?? 0} · Launch readiness: {room?.launchReadiness ?? dashboard?.launchReadiness ?? 0}%
                </p>
              )}
            </OsSection>

            <OsSection title="Community" subtitle="Build log · updates · discussion" disabled={!hasFounder} id="build">
              <input value={buildForm.headline} onChange={(e) => setBuildForm({ ...buildForm, headline: e.target.value })} placeholder="Update headline" disabled={!hasFounder} className="mb-2 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
              <textarea value={buildForm.body} onChange={(e) => setBuildForm({ ...buildForm, body: e.target.value })} placeholder="What did you ship?" rows={3} disabled={!hasFounder} className="mb-2 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
              <input value={buildForm.dayNumber} onChange={(e) => setBuildForm({ ...buildForm, dayNumber: e.target.value })} placeholder="Day number" disabled={!hasFounder} className="mb-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm disabled:opacity-50" />
              <button type="button" onClick={postBuildUpdate} disabled={!hasFounder} className="w-full rounded-lg border border-zinc-600 py-2 text-sm disabled:opacity-40">
                Publish build update
              </button>
              {room && room.buildPosts.length > 0 && (
                <p className="mt-3 text-xs text-zinc-500">Latest: {room.buildPosts[0].headline}</p>
              )}
            </OsSection>
          </div>
        </section>

        {session && dashboard && (
          <p className="text-center text-xs text-zinc-600">
            Tier: {formatTierLabel(dashboard.progressTier)} · Paper cash: {formatUsd(dashboard.cashBalance, 0)}
          </p>
        )}
      </div>
    </main>
  );
}
