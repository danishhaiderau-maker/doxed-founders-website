'use client';

import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { FounderWorkspace, WorkspaceTab } from '@/components/founder-workspace';
import {
  createBuildPost,
  createSimulatedRaise,
  fetchFounderDashboard,
  fetchProjectRoom,
  FounderDashboard,
  ProjectRoom,
  submitFounderApplication,
} from '@/lib/api';

const VALID_TABS: WorkspaceTab[] = [
  'activity',
  'tasks',
  'community',
  'funding',
  'agents',
  'build',
  'analytics',
];

function parseTab(value: string | null): WorkspaceTab {
  if (value && VALID_TABS.includes(value as WorkspaceTab)) return value as WorkspaceTab;
  return 'activity';
}

export default function FounderDenPageClient() {
  const { data: session } = useSession();
  const searchParams = useSearchParams();
  const [tab, setTab] = useState<WorkspaceTab>(() => parseTab(searchParams.get('tab')));
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
    communityTokenPercent: '10',
    maxParticipantSlots: '',
    plannedLaunchDate: '',
  });

  useEffect(() => {
    setTab(parseTab(searchParams.get('tab')));
  }, [searchParams]);

  const hasFounder = dashboard?.hasFounderProfile ?? false;
  const currentStage = room?.lifecycleStage ?? dashboard?.currentStage ?? 'IDEA';

  const load = useCallback(async () => {
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
          communityTokenPercent: Number(raiseForm.communityTokenPercent) || 10,
          maxParticipantSlots: raiseForm.maxParticipantSlots
            ? Number(raiseForm.maxParticipantSlots)
            : undefined,
          plannedLaunchDate: raiseForm.plannedLaunchDate || undefined,
        },
        session.accessToken,
      );
      setMessage('Raise Room is live — public ICO slots open');
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start raise');
    }
  }

  function handleTabChange(next: WorkspaceTab) {
    setTab(next);
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', next);
      window.history.replaceState({}, '', url.toString());
    }
  }

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-5 sm:px-6 lg:px-10">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Founder OS</h1>
            <p className="text-sm text-zinc-500">
              Mission control · build in public · validate demand · launch with trust
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto w-full max-w-[90rem] space-y-6 px-4 py-8 sm:px-6 lg:px-10">
        {message && (
          <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-2 text-sm text-emerald-200">
            {message}
          </p>
        )}
        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-2 text-sm text-red-300">
            {error}
          </p>
        )}

        <FounderWorkspace
          tab={tab}
          onTabChange={handleTabChange}
          session={session?.accessToken ? { accessToken: session.accessToken } : null}
          hasFounder={hasFounder}
          currentStage={currentStage}
          dashboard={dashboard}
          room={room}
          onRefresh={load}
          onWorkspaceMessage={(msg) => {
            setMessage(msg);
            load();
          }}
          appForm={appForm}
          setAppForm={setAppForm}
          buildForm={buildForm}
          setBuildForm={setBuildForm}
          raiseForm={raiseForm}
          setRaiseForm={setRaiseForm}
          onSubmitApplication={submitApplication}
          onPostBuildUpdate={postBuildUpdate}
          onLaunchRaise={launchRaise}
        />
      </div>
    </main>
  );
}
