'use client';

import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { parseOnboardingPathParam, type OnboardingPathId } from '@dcf/utils';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { FounderWorkspace, WorkspaceTab } from '@/components/founder-workspace';
import {
  clearFounderOnboardingDismiss,
  FounderOnboardingWizard,
} from '@/components/founder-onboarding-wizard';
import { FounderSetupRail } from '@/components/founder-setup-rail';
import {
  createBuildPost,
  createSimulatedRaise,
  fetchFounderDashboard,
  fetchFounderOnboardingStatus,
  fetchProjectRoom,
  FounderDashboard,
  FounderOnboardingStatus,
  ProjectRoom,
  submitFounderApplication,
} from '@/lib/api';

const PRIMARY_TABS: WorkspaceTab[] = ['workspace', 'activity', 'social', 'analytics'];

const TAB_ALIASES: Record<string, WorkspaceTab> = {
  build: 'activity',
  launch: 'activity',
  tasks: 'activity',
  community: 'social',
  funding: 'analytics',
  notifications: 'activity',
  copilot: 'activity',
  agents: 'activity',
  mission: 'activity',
};

function parseTab(value: string | null): WorkspaceTab {
  if (value && TAB_ALIASES[value]) return TAB_ALIASES[value];
  if (value && PRIMARY_TABS.includes(value as WorkspaceTab)) return value as WorkspaceTab;
  return 'workspace';
}

export default function FounderDenPageClient() {
  const { data: session } = useSession();
  const searchParams = useSearchParams();
  const [tab, setTab] = useState<WorkspaceTab>(() => parseTab(searchParams.get('tab')));
  const [copilotPrompt, setCopilotPrompt] = useState<string | null>(() => searchParams.get('prompt'));
  const [dashboard, setDashboard] = useState<FounderDashboard | null>(null);
  const [dashboardLoaded, setDashboardLoaded] = useState(false);
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
  const [wizardKey, setWizardKey] = useState(0);
  const [wizardDismissed, setWizardDismissed] = useState(false);
  const [onboardingStatus, setOnboardingStatus] = useState<FounderOnboardingStatus | null>(null);
  const initialPath = parseOnboardingPathParam(searchParams.get('onboard'));

  useEffect(() => {
    if (typeof window === 'undefined') return;
    setWizardDismissed(window.localStorage.getItem('dcf-founder-onboarding-dismissed') === '1');
  }, [wizardKey]);

  useEffect(() => {
    setTab(parseTab(searchParams.get('tab')));
    setCopilotPrompt(searchParams.get('prompt'));
  }, [searchParams]);

  function clearCopilotUrlParams() {
    setCopilotPrompt(null);
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    url.searchParams.delete('prompt');
    url.searchParams.delete('agent');
    window.history.replaceState({}, '', url.toString());
  }

  const hasFounder = dashboard?.hasFounderProfile ?? false;
  const setupIncomplete = Boolean(onboardingStatus && !onboardingStatus.requiredComplete);
  const currentStage = room?.lifecycleStage ?? dashboard?.currentStage ?? 'IDEA';

  const showOnboardingWizard =
    session?.accessToken &&
    (!hasFounder || setupIncomplete || (onboardingStatus?.requiredComplete && !wizardDismissed));

  function launchMissionControlPrompt(prompt: string) {
    setCopilotPrompt(prompt);
    setTab('activity');
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', 'activity');
      url.searchParams.set('prompt', prompt);
      window.history.replaceState({}, '', url.toString());
      window.requestAnimationFrame(() => {
        document.getElementById('founder-mission-control')?.scrollIntoView({ behavior: 'smooth' });
      });
    }
  }

  const load = useCallback(async () => {
    if (!session?.accessToken) return;
    try {
      const dash = await fetchFounderDashboard(session.accessToken);
      setDashboard(dash);
      try {
        setOnboardingStatus(await fetchFounderOnboardingStatus(session.accessToken));
      } catch {
        setOnboardingStatus(null);
      }
      if (dash.primaryProjectSlug) {
        setRoom(await fetchProjectRoom(dash.primaryProjectSlug, session.accessToken));
      } else {
        setRoom(null);
      }
    } catch {
      setDashboard(null);
      setOnboardingStatus(null);
    } finally {
      setDashboardLoaded(true);
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
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 sm:py-5 lg:px-10">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Founder OS</h1>
            <p className="text-sm text-zinc-500">
              AI Development Workspace — your AI engineering control plane
            </p>
            {onboardingStatus?.pathLabel && hasFounder && (
              <span className="mt-2 inline-flex rounded-full border border-violet-500/30 bg-violet-950/40 px-2.5 py-0.5 text-[10px] text-violet-200">
                {onboardingStatus.pathLabel}
              </span>
            )}
          </div>
          <SiteNav />
        </div>
      </header>

      {session?.accessToken && !dashboardLoaded ? (
        <div className="flex min-h-[60vh] items-center justify-center">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-700 border-t-violet-500" />
            <p className="text-xs text-zinc-500">Loading workspace…</p>
          </div>
        </div>
      ) : (
      <div
        className={`mx-auto w-full ${
          session?.accessToken && hasFounder
            ? 'max-w-none px-2 py-2 sm:px-4 lg:px-6'
            : 'max-w-[90rem] space-y-6 px-4 py-8 sm:px-6 lg:px-10'
        }`}
      >
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

        {session?.accessToken && hasFounder && setupIncomplete && onboardingStatus && wizardDismissed && (
          <FounderSetupRail
            status={onboardingStatus}
            onTestBrain={
              onboardingStatus.brainReady
                ? () =>
                    launchMissionControlPrompt(
                      "What's my setup status? Summarize what's connected and what I should do next.",
                    )
                : undefined
            }
            onResumeWizard={() => {
              clearFounderOnboardingDismiss();
              setWizardDismissed(false);
              setWizardKey((k) => k + 1);
            }}
          />
        )}

        {session?.accessToken && showOnboardingWizard && (
          <FounderOnboardingWizard
            key={wizardKey}
            accessToken={session.accessToken}
            initialPath={initialPath as OnboardingPathId | null}
            onRefresh={load}
            onLaunchPrompt={launchMissionControlPrompt}
            onDismiss={() => setWizardDismissed(true)}
            onMessage={(msg) => {
              setMessage(msg);
              load();
            }}
          />
        )}

        {showOnboardingWizard && wizardDismissed && setupIncomplete && (
          <button
            type="button"
            onClick={() => {
              clearFounderOnboardingDismiss();
              setWizardKey((k) => k + 1);
            }}
            className="text-xs text-zinc-500 underline hover:text-zinc-300"
          >
            Resume setup wizard
          </button>
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
          initialCopilotPrompt={copilotPrompt}
          onInitialCopilotPromptConsumed={clearCopilotUrlParams}
        />
      </div>
      )}
    </main>
  );
}
