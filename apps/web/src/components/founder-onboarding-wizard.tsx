'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { LIFECYCLE_STAGES, type OnboardingPathId, type StarterPackId } from '@dcf/utils';
import { FounderNodeDownloads } from '@/components/founder-node-downloads';
import { FounderImportWizard } from '@/components/founder-import-wizard';
import { FounderCloudPanel } from '@/components/founder-cloud-panel';
import { FounderPathSelector } from '@/components/founder-path-selector';
import { FounderStarterPackPicker } from '@/components/founder-starter-pack-picker';
import { FounderOnboardingAiStack } from '@/components/founder-onboarding-ai-stack';
import { FounderOnboardingComplete } from '@/components/founder-onboarding-complete';
import {
  connectGitHubRepo,
  connectGitHubToken,
  createFounderNodePairingCode,
  fetchFounderOnboardingStatus,
  fetchGitHubOAuthStart,
  submitFounderApplication,
  updateBuilderSettings,
  updateFounderOnboardingPath,
  type FounderOnboardingStatus,
  type FounderOnboardingStep,
} from '@/lib/api';

const DISMISS_KEY = 'dcf-founder-onboarding-dismissed';
const PATH_STORAGE_KEY = 'dcf-founder-onboarding-path';

type Props = {
  accessToken: string;
  onRefresh?: () => void;
  onMessage?: (msg: string) => void;
  onLaunchPrompt?: (prompt: string) => void;
  onDismiss?: () => void;
  initialPath?: OnboardingPathId | null;
};

function stepById(status: FounderOnboardingStatus | null, id: string) {
  return status?.steps.find((s) => s.id === id);
}

export function FounderOnboardingWizard({
  accessToken,
  onRefresh,
  onMessage,
  onLaunchPrompt,
  onDismiss: onDismissProp,
  initialPath,
}: Props) {
  const [status, setStatus] = useState<FounderOnboardingStatus | null>(null);
  const [dismissed, setDismissed] = useState(true);
  const [stepIndex, setStepIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [selectedPath, setSelectedPath] = useState<OnboardingPathId | null>(initialPath ?? null);
  const [selectedPack, setSelectedPack] = useState<StarterPackId | null>(null);
  const [projectName, setProjectName] = useState('');
  const [ideaDescription, setIdeaDescription] = useState('');
  const [repoInput, setRepoInput] = useState('');
  const [githubToken, setGithubToken] = useState('');
  const [goalFocus, setGoalFocus] = useState('');
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [pairingExpires, setPairingExpires] = useState<string | null>(null);

  const stepOrder = useMemo(
    () => status?.steps.map((s) => s.id) ?? ['path', 'founder'],
    [status],
  );

  const load = useCallback(async () => {
    try {
      const data = await fetchFounderOnboardingStatus(accessToken);
      setStatus(data);
      setProjectName((prev) => prev || data.projectName || '');
      const goalStep = stepById(data, 'goal');
      setGoalFocus((prev) => prev || goalStep?.detail || '');
      const ghStep = stepById(data, 'github');
      setRepoInput((prev) => prev || ghStep?.detail || '');
      if (data.onboardingPath) {
        setSelectedPath(data.onboardingPath as OnboardingPathId);
      }
      if (data.starterPack) {
        setSelectedPack(data.starterPack as StarterPackId);
      }
    } catch {
      setStatus(null);
    }
  }, [accessToken]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    setDismissed(window.localStorage.getItem(DISMISS_KEY) === '1');
    const stored = window.localStorage.getItem(PATH_STORAGE_KEY);
    if (stored && !initialPath) {
      setSelectedPath(stored as OnboardingPathId);
    }
  }, [initialPath]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    if (params.get('github') === 'connected') {
      onMessage?.('GitHub connected via OAuth — pick your repo below');
      load();
      const url = new URL(window.location.href);
      url.searchParams.delete('github');
      window.history.replaceState({}, '', url.toString());
    }
  }, [accessToken, load, onMessage]);

  const currentStepId = stepOrder[stepIndex] ?? stepOrder[0] ?? 'path';
  const currentStep = stepById(status, currentStepId);
  const requiredComplete = status?.requiredComplete ?? false;

  const completedCount = useMemo(() => {
    if (!status) return 0;
    return status.steps.filter((s) => s.complete && !s.optional).length;
  }, [status]);

  const requiredTotal = useMemo(() => {
    if (!status) return 1;
    return status.steps.filter((s) => !s.optional).length;
  }, [status]);

  useEffect(() => {
    if (!status) return;
    const firstIncomplete = status.steps.findIndex((s) => !s.complete && !s.optional);
    const anyIncomplete = status.steps.findIndex((s) => !s.complete);
    const target = firstIncomplete >= 0 ? firstIncomplete : anyIncomplete;
    if (target >= 0) setStepIndex(target);
  }, [status]);

  if (dismissed) return null;

  if (requiredComplete && status) {
    return (
      <FounderOnboardingComplete
        status={status}
        onLaunchPrompt={(prompt) => {
          onLaunchPrompt?.(prompt);
          onRefresh?.();
        }}
        onDismiss={dismiss}
      />
    );
  }

  function dismiss() {
    if (typeof window !== 'undefined') window.localStorage.setItem(DISMISS_KEY, '1');
    setDismissed(true);
    onDismissProp?.();
  }

  async function savePath(path: OnboardingPathId) {
    setSelectedPath(path);
    if (typeof window !== 'undefined') window.localStorage.setItem(PATH_STORAGE_KEY, path);
    setBusy(true);
    setErr(null);
    try {
      await updateFounderOnboardingPath({ onboardingPath: path }, accessToken);
      onMessage?.(`Path saved: ${path.replace(/_/g, ' ').toLowerCase()}`);
      await load();
      setStepIndex((i) => Math.min(i + 1, stepOrder.length - 1));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save path');
    } finally {
      setBusy(false);
    }
  }

  async function saveStarterPack(pack: StarterPackId) {
    setSelectedPack(pack);
    setBusy(true);
    setErr(null);
    try {
      await updateFounderOnboardingPath({ starterPack: pack }, accessToken);
      onMessage?.(`Starter pack: ${pack}`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save starter pack');
    } finally {
      setBusy(false);
    }
  }

  async function activateFounder() {
    if (!selectedPath && !status?.onboardingPath) {
      setErr('Choose a path first');
      return;
    }
    if (!projectName.trim() || !ideaDescription.trim()) {
      setErr('Project name and idea are required');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      if (selectedPath && !status?.onboardingPath) {
        await updateFounderOnboardingPath({ onboardingPath: selectedPath }, accessToken);
      }
      const result = await submitFounderApplication(
        {
          projectName: projectName.trim(),
          ideaDescription: ideaDescription.trim(),
          lifecycleStage: 'IDEA',
        },
        accessToken,
      );
      onMessage?.(`Founder profile live → /project/${result.projectSlug}`);
      await load();
      onRefresh?.();
      setStepIndex((i) => Math.min(i + 1, stepOrder.length - 1));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not activate profile');
    } finally {
      setBusy(false);
    }
  }

  async function connectRepo() {
    if (!repoInput.trim()) {
      setErr('Enter owner/repo (e.g. you/your-app)');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await connectGitHubRepo(repoInput.trim(), accessToken);
      if (githubToken.trim()) {
        await connectGitHubToken(githubToken.trim(), accessToken);
        setGithubToken('');
      }
      onMessage?.('GitHub connected — commits auto-sync every 15 min');
      await load();
      onRefresh?.();
      setStepIndex((i) => Math.min(i + 1, stepOrder.length - 1));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'GitHub connect failed');
    } finally {
      setBusy(false);
    }
  }

  async function saveGoal() {
    if (!goalFocus.trim()) {
      setErr('Describe your current focus');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await updateBuilderSettings({ currentGoalFocus: goalFocus.trim() }, accessToken);
      onMessage?.('Goal saved — Founder Brain will reference this');
      await load();
      setStepIndex((i) => Math.min(i + 1, stepOrder.length - 1));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save goal');
    } finally {
      setBusy(false);
    }
  }

  async function generatePairingCode() {
    setBusy(true);
    setErr(null);
    try {
      const result = await createFounderNodePairingCode(accessToken);
      setPairingCode(result.code);
      setPairingExpires(result.expiresAt);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not create pairing code');
    } finally {
      setBusy(false);
    }
  }

  function goNext() {
    setErr(null);
    setStepIndex((i) => Math.min(i + 1, stepOrder.length - 1));
  }

  function goBack() {
    setErr(null);
    setStepIndex((i) => Math.max(i - 1, 0));
  }

  const playbook = status?.playbook ?? [];

  return (
    <div className="relative overflow-hidden rounded-2xl border border-violet-500/35 bg-gradient-to-br from-violet-950/40 via-[#0a0a12] to-cyan-950/20 p-5 sm:p-6">
      <div className="pointer-events-none absolute -right-8 -top-8 h-32 w-32 rounded-full bg-violet-600/10 blur-2xl" />
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-300">
            Founder OS · choose your infrastructure
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">
            The operating system for founders — not another AI wrapper
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-zinc-400">
            Founder Brain is the only interface. Pick how memory, compute, and publish run behind it.
            Recommendations are suggestions — nothing is enforced.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {status && (
            <span className="rounded-full border border-violet-500/30 bg-violet-950/40 px-3 py-1 text-xs text-violet-200">
              {completedCount}/{requiredTotal} required
            </span>
          )}
          <button
            type="button"
            onClick={dismiss}
            className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
          >
            Continue later
          </button>
        </div>
      </div>

      {status?.pathLabel && currentStepId !== 'path' && (
        <p className="relative mt-3 text-xs text-zinc-500">
          Path: <span className="text-violet-300">{status.pathLabel}</span>
          {status.topology && (
            <>
              {' '}
              · Memory: {status.topology.memory} · Compute: {status.topology.compute}
            </>
          )}
        </p>
      )}

      {status && (
        <div className="relative mt-5 flex flex-wrap gap-2">
          {status.steps.map((step, i) => {
            const active = step.id === currentStepId;
            const done = step.complete;
            return (
              <button
                key={step.id}
                type="button"
                onClick={() => setStepIndex(i)}
                className={`rounded-full px-3 py-1 text-[11px] font-medium transition ${
                  active
                    ? 'bg-violet-600 text-white'
                    : done
                      ? 'border border-emerald-500/40 bg-emerald-950/30 text-emerald-200'
                      : 'border border-zinc-700 text-zinc-500 hover:border-zinc-500'
                }`}
              >
                {done ? '✓ ' : ''}
                {step.label.split('(')[0]?.trim() ?? step.id}
                {step.optional ? ' · optional' : ''}
              </button>
            );
          })}
        </div>
      )}

      <div className="relative mt-6 grid gap-6 lg:grid-cols-[1fr_minmax(220px,280px)]">
        <div className="rounded-xl border border-zinc-800/80 bg-black/30 p-4 sm:p-5">
          {currentStepId === 'path' && (
            <>
              <h3 className="font-semibold text-white">Step 0 — Choose how you build</h3>
              <p className="mt-1 text-sm text-zinc-500">
                Sovereign, your cloud, migrate in, a free starter pack, or full Founder Cloud on your PC.
              </p>
              <div className="mt-3 rounded-lg border border-cyan-500/20 bg-cyan-950/15 p-3 text-xs text-cyan-100/85">
                <strong className="text-cyan-300">Save on cloud bills.</strong> Run bots and heavy compute
                on your home PC + tunnel (we cut Railway from ~$10/day to $0). Founder OS stays the control
                plane — Vercel + Neon for sync, your keys for AI.
              </div>
              <div className="mt-4">
                <FounderPathSelector
                  selectedPath={selectedPath ?? (status?.onboardingPath as OnboardingPathId | null) ?? null}
                  onSelect={(path) => void savePath(path)}
                  disabled={busy}
                />
              </div>
            </>
          )}

          {currentStepId === 'founder' && (
            <>
              <h3 className="font-semibold text-white">Activate founder profile</h3>
              <p className="mt-1 text-sm text-zinc-500">Unlocks Mission Control, Founder Brain, and agents.</p>
              {currentStep?.complete ? (
                <p className="mt-4 text-sm text-emerald-300">
                  Profile active{status?.projectName ? `: ${status.projectName}` : ''}.
                </p>
              ) : (
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <input
                    value={projectName}
                    onChange={(e) => setProjectName(e.target.value)}
                    placeholder="Project name"
                    className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                  />
                  <select
                    defaultValue="IDEA"
                    disabled
                    className="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2 text-sm text-zinc-500"
                  >
                    <option value="IDEA">{LIFECYCLE_STAGES.find((x) => x.key === 'IDEA')?.label ?? 'Idea'}</option>
                  </select>
                  <textarea
                    value={ideaDescription}
                    onChange={(e) => setIdeaDescription(e.target.value)}
                    placeholder="What are you building?"
                    rows={3}
                    className="sm:col-span-2 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                  />
                </div>
              )}
            </>
          )}

          {currentStepId === 'starter_pack' && (
            <>
              <h3 className="font-semibold text-white">Pick a starter pack</h3>
              <p className="mt-1 text-sm text-zinc-500">Get a live URL in ~5 minutes. Render is recommended for beginners.</p>
              <div className="mt-4">
                <FounderStarterPackPicker
                  selectedPack={selectedPack ?? (status?.starterPack as StarterPackId | null) ?? null}
                  onSelect={(pack) => void saveStarterPack(pack)}
                  disabled={busy}
                />
              </div>
            </>
          )}

          {currentStepId === 'github' && (
            <>
              <h3 className="font-semibold text-white">Connect GitHub repo</h3>
              <p className="mt-1 text-sm text-zinc-500">
                {currentStep?.optional
                  ? 'Optional on your path — connect when you want commits, PRs, and remote builds.'
                  : 'We poll your repo every 15 minutes and when you open Mission Control.'}
              </p>
              <div className="mt-3">
                <button
                  type="button"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    setErr(null);
                    try {
                      const { url } = await fetchGitHubOAuthStart(accessToken);
                      window.location.href = url;
                    } catch (e) {
                      setErr(e instanceof Error ? e.message : 'GitHub OAuth not configured — use token below');
                      setBusy(false);
                    }
                  }}
                  className="rounded-lg bg-zinc-100 px-4 py-2 text-sm font-medium text-black disabled:opacity-50"
                >
                  Connect with GitHub (OAuth)
                </button>
              </div>
              <div className="mt-4 flex flex-col gap-2 sm:flex-row">
                <input
                  value={repoInput}
                  onChange={(e) => setRepoInput(e.target.value)}
                  placeholder="owner/repo"
                  className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                />
              </div>
              <label className="mt-3 block text-sm">
                <span className="text-zinc-400">GitHub token (optional — private repos)</span>
                <input
                  type="password"
                  value={githubToken}
                  onChange={(e) => setGithubToken(e.target.value)}
                  placeholder="ghp_…"
                  className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                />
              </label>
              {status?.githubLastSyncedAt && (
                <p className="mt-2 text-xs text-emerald-400/90">
                  Auto-sync active · last checked {new Date(status.githubLastSyncedAt).toLocaleString()}
                </p>
              )}
            </>
          )}

          {(currentStepId === 'platform' || currentStepId === 'migrate') && (
            <>
              <h3 className="font-semibold text-white">
                {currentStepId === 'migrate' ? 'Import to private stack' : 'Connect cloud host'}
              </h3>
              <p className="mt-1 text-sm text-zinc-500">
                {currentStepId === 'migrate'
                  ? 'Connect read-only GitHub + hosts, then run the import wizard.'
                  : 'Paste host URL, database string, or deploy token in Settings → Builder.'}
              </p>
              {currentStepId === 'migrate' && accessToken ? (
                <div className="mt-4 space-y-4">
                  <FounderImportWizard accessToken={accessToken} onComplete={() => void load()} />
                  <FounderCloudPanel accessToken={accessToken} />
                </div>
              ) : (
                <>
                  <ul className="mt-4 list-inside list-disc space-y-1 text-sm text-zinc-400">
                    <li>Railway, Render, Vercel, Neon, or Supabase</li>
                    <li>Founder Node for local vault</li>
                    <li>Per-connector toggles: publish / sync / AI context</li>
                  </ul>
                  <Link
                    href="/settings/builder"
                    className="mt-4 inline-flex rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500"
                  >
                    Open connect hub →
                  </Link>
                </>
              )}
            </>
          )}

          {currentStepId === 'ai_stack' && (
            <>
              <h3 className="font-semibold text-white">Connect AI Stack</h3>
              <p className="mt-1 text-sm text-zinc-500">
                Founder Brain routes internally — connect once here, no tab-hopping.
              </p>
              <div className="mt-4">
                <FounderOnboardingAiStack
                  accessToken={accessToken}
                  llmConnected={status?.llmConnected ?? false}
                  builderConnected={status?.builderConnected ?? false}
                  promo={status?.promo}
                  onConnected={() => void load()}
                  onMessage={onMessage}
                />
              </div>
            </>
          )}

          {currentStepId === 'goal' && (
            <>
              <h3 className="font-semibold text-white">Set your current goal</h3>
              <p className="mt-1 text-sm text-zinc-500">Founder Brain uses this in briefings and task suggestions.</p>
              <input
                value={goalFocus}
                onChange={(e) => setGoalFocus(e.target.value)}
                placeholder="e.g. Ship referral system + first 100 waitlist signups"
                className="mt-4 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              />
            </>
          )}

          {currentStepId === 'founder_node' && (
            <>
              <h3 className="font-semibold text-white">Pair Founder Node</h3>
              <p className="mt-1 text-sm text-zinc-500">
                {currentStep?.optional
                  ? 'Optional vault on your PC — full memory stays local.'
                  : 'Required for your path — local vault, Ollama, and Founder Cloud mode.'}
              </p>
              <div className="mt-4">
                <FounderNodeDownloads />
              </div>
              <ol className="mt-4 list-inside list-decimal space-y-1 text-xs text-zinc-400">
                <li>Install Founder Node and launch from the system tray</li>
                <li>Generate a pairing code below and paste it in the tray menu</li>
                <li>Settings → Builder → Memory storage → Founder Node</li>
              </ol>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={generatePairingCode}
                  className="rounded-lg bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                >
                  {busy ? 'Generating…' : 'Generate pairing code'}
                </button>
                <Link
                  href="/founder-node"
                  className="rounded-lg border border-zinc-600 px-3 py-1.5 text-xs text-zinc-300 hover:text-white"
                >
                  Full setup guide →
                </Link>
              </div>
              {pairingCode && (
                <div className="mt-3 rounded-lg border border-cyan-400/40 bg-black/30 p-3 text-center">
                  <p className="text-xs text-zinc-400">Pairing code</p>
                  <p className="mt-1 font-mono text-2xl font-bold tracking-[0.3em] text-cyan-300">{pairingCode}</p>
                  {pairingExpires && (
                    <p className="mt-1 text-[10px] text-zinc-500">
                      Expires {new Date(pairingExpires).toLocaleString()}
                    </p>
                  )}
                </div>
              )}
            </>
          )}

          {err && <p className="mt-4 text-sm text-red-300">{err}</p>}

          <StepActions
            currentStepId={currentStepId}
            currentStep={currentStep}
            stepIndex={stepIndex}
            stepOrderLength={stepOrder.length}
            busy={busy}
            onBack={goBack}
            onNext={goNext}
            onDismiss={dismiss}
            onActivate={activateFounder}
            onConnectRepo={connectRepo}
            onSaveGoal={saveGoal}
            onRefresh={load}
          />
        </div>

        {playbook.length > 0 && (
          <aside className="rounded-xl border border-zinc-800/60 bg-black/20 p-4">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-cyan-400/90">
              5-minute playbook
            </p>
            <p className="mt-1 text-xs text-zinc-500">Recommended steps — not requirements.</p>
            <ol className="mt-3 space-y-2">
              {playbook.map((item, i) => (
                <li key={item.action} className="flex gap-2 text-xs">
                  <span className="shrink-0 font-mono text-zinc-600">{i + 1}.</span>
                  <span className="text-zinc-300">
                    {item.action}
                    {item.time && <span className="text-zinc-600"> · {item.time}</span>}
                  </span>
                </li>
              ))}
            </ol>
          </aside>
        )}
      </div>
    </div>
  );
}

function StepActions({
  currentStepId,
  currentStep,
  stepIndex,
  stepOrderLength,
  busy,
  onBack,
  onNext,
  onDismiss,
  onActivate,
  onConnectRepo,
  onSaveGoal,
  onRefresh,
}: {
  currentStepId: string;
  currentStep: FounderOnboardingStep | undefined;
  stepIndex: number;
  stepOrderLength: number;
  busy: boolean;
  onBack: () => void;
  onNext: () => void;
  onDismiss: () => void;
  onActivate: () => void;
  onConnectRepo: () => void;
  onSaveGoal: () => void;
  onRefresh: () => void;
}) {
  const isLast = stepIndex >= stepOrderLength - 1;
  const canSkipOptional = currentStep?.optional && currentStep.complete;

  return (
    <div className="mt-5 flex flex-wrap items-center gap-2">
      {stepIndex > 0 && (
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:text-white"
        >
          Back
        </button>
      )}

      {currentStepId === 'path' && currentStep?.complete && (
        <button type="button" onClick={onNext} className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white">
          Next
        </button>
      )}

      {currentStepId === 'founder' && !currentStep?.complete && (
        <button
          type="button"
          disabled={busy}
          onClick={onActivate}
          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? 'Activating…' : 'Activate profile'}
        </button>
      )}

      {currentStepId === 'starter_pack' && currentStep?.complete && (
        <button type="button" onClick={onNext} className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white">
          Next
        </button>
      )}

      {currentStepId === 'github' && (
        <>
          <button
            type="button"
            disabled={busy}
            onClick={onConnectRepo}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            {busy ? 'Connecting…' : currentStep?.complete ? 'Update repo' : 'Connect & sync'}
          </button>
          {currentStep?.optional && (
            <button type="button" onClick={onNext} className="text-xs text-zinc-500 underline">
              Skip for now
            </button>
          )}
        </>
      )}

      {(currentStepId === 'platform' || currentStepId === 'migrate') && (
        <>
          <button
            type="button"
            onClick={() => {
              onRefresh();
              if (currentStep?.complete) onNext();
            }}
            className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300"
          >
            I connected — refresh
          </button>
          {currentStep?.optional && (
            <button type="button" onClick={onNext} className="text-xs text-zinc-500 underline">
              Skip for now
            </button>
          )}
        </>
      )}

      {currentStepId === 'ai_stack' && (
        <>
          <button
            type="button"
            onClick={() => {
              onRefresh();
              if (currentStep?.complete) onNext();
            }}
            className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300"
          >
            I connected — refresh
          </button>
          {currentStep?.complete && !isLast && (
            <button type="button" onClick={onNext} className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white">
              Next
            </button>
          )}
        </>
      )}

      {currentStepId === 'goal' && (
        <button
          type="button"
          disabled={busy}
          onClick={onSaveGoal}
          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? 'Saving…' : 'Save goal'}
        </button>
      )}

      {currentStepId === 'founder_node' && (
        <>
          <button type="button" onClick={onDismiss} className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white">
            {isLast ? 'Finish setup' : 'Continue'}
          </button>
          {currentStep?.optional && (
            <button type="button" onClick={onDismiss} className="text-xs text-zinc-500 underline">
              Skip vault for now
            </button>
          )}
        </>
      )}

      {currentStep?.complete &&
        !['founder_node', 'ai_stack', 'github', 'path', 'starter_pack', 'platform', 'migrate'].includes(
          currentStepId,
        ) &&
        !isLast && (
          <button type="button" onClick={onNext} className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300">
            Next
          </button>
        )}

      {canSkipOptional && isLast && (
        <button type="button" onClick={onDismiss} className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white">
          Finish setup
        </button>
      )}
    </div>
  );
}

export function clearFounderOnboardingDismiss() {
  if (typeof window !== 'undefined') window.localStorage.removeItem(DISMISS_KEY);
}

export function clearFounderOnboardingPathStorage() {
  if (typeof window !== 'undefined') window.localStorage.removeItem(PATH_STORAGE_KEY);
}
