'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { LIFECYCLE_STAGES } from '@dcf/utils';
import { FounderNodeDownloads } from '@/components/founder-node-downloads';
import {
  connectGitHubRepo,
  connectGitHubToken,
  createFounderNodePairingCode,
  fetchFounderOnboardingStatus,
  submitFounderApplication,
  updateBuilderSettings,
  type FounderOnboardingStatus,
  type FounderOnboardingStep,
} from '@/lib/api';

const DISMISS_KEY = 'dcf-founder-onboarding-dismissed';

const STEP_ORDER: FounderOnboardingStep['id'][] = [
  'founder',
  'github',
  'ai_stack',
  'goal',
  'founder_node',
];

type Props = {
  accessToken: string;
  onRefresh?: () => void;
  onMessage?: (msg: string) => void;
};

function stepById(status: FounderOnboardingStatus | null, id: FounderOnboardingStep['id']) {
  return status?.steps.find((s) => s.id === id);
}

export function FounderOnboardingWizard({ accessToken, onRefresh, onMessage }: Props) {
  const [status, setStatus] = useState<FounderOnboardingStatus | null>(null);
  const [dismissed, setDismissed] = useState(true);
  const [stepIndex, setStepIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [projectName, setProjectName] = useState('');
  const [ideaDescription, setIdeaDescription] = useState('');
  const [repoInput, setRepoInput] = useState('');
  const [githubToken, setGithubToken] = useState('');
  const [goalFocus, setGoalFocus] = useState('');
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [pairingExpires, setPairingExpires] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchFounderOnboardingStatus(accessToken);
      setStatus(data);
      setProjectName((prev) => prev || data.projectName || '');
      const goalStep = stepById(data, 'goal');
      setGoalFocus((prev) => prev || goalStep?.detail || '');
      const ghStep = stepById(data, 'github');
      setRepoInput((prev) => prev || ghStep?.detail || '');
    } catch {
      setStatus(null);
    }
  }, [accessToken]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    setDismissed(window.localStorage.getItem(DISMISS_KEY) === '1');
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const currentStepId = STEP_ORDER[stepIndex] ?? 'founder';
  const currentStep = stepById(status, currentStepId);
  const requiredComplete = status?.requiredComplete ?? false;

  const completedCount = useMemo(() => {
    if (!status) return 0;
    return status.steps.filter((s) => s.complete && !s.optional).length;
  }, [status]);

  const requiredTotal = useMemo(() => {
    if (!status) return 4;
    return status.steps.filter((s) => !s.optional).length;
  }, [status]);

  useEffect(() => {
    if (!status) return;
    const firstIncomplete = STEP_ORDER.findIndex((id) => !stepById(status, id)?.complete);
    if (firstIncomplete >= 0) setStepIndex(firstIncomplete);
  }, [status]);

  if (dismissed || requiredComplete || !status) return null;

  function dismiss() {
    if (typeof window !== 'undefined') window.localStorage.setItem(DISMISS_KEY, '1');
    setDismissed(true);
  }

  async function activateFounder() {
    if (!projectName.trim() || !ideaDescription.trim()) {
      setErr('Project name and idea are required');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
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
      setStepIndex((i) => Math.min(i + 1, STEP_ORDER.length - 1));
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
      onMessage?.('GitHub connected — commits auto-sync every 15 min and when you open Copilot');
      await load();
      onRefresh?.();
      setStepIndex((i) => Math.min(i + 1, STEP_ORDER.length - 1));
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
      onMessage?.('Goal saved — Copilot will reference this in briefings');
      await load();
      setStepIndex((i) => Math.min(i + 1, STEP_ORDER.length - 1));
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
    setStepIndex((i) => Math.min(i + 1, STEP_ORDER.length - 1));
  }

  function goBack() {
    setErr(null);
    setStepIndex((i) => Math.max(i - 1, 0));
  }

  return (
    <div className="relative overflow-hidden rounded-2xl border border-violet-500/35 bg-gradient-to-br from-violet-950/40 via-[#0a0a12] to-cyan-950/20 p-5 sm:p-6">
      <div className="pointer-events-none absolute -right-8 -top-8 h-32 w-32 rounded-full bg-violet-600/10 blur-2xl" />
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-300">
            Windows setup wizard
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">Get Founder OS running in ~5 minutes</h2>
          <p className="mt-1 max-w-xl text-sm text-zinc-400">
            Connect your repo, AI stack, and optional local vault. GitHub commits sync automatically — no manual
            &ldquo;Sync commits&rdquo; button needed.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-violet-500/30 bg-violet-950/40 px-3 py-1 text-xs text-violet-200">
            {completedCount}/{requiredTotal} required
          </span>
          <button
            type="button"
            onClick={dismiss}
            className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
          >
            Continue later
          </button>
        </div>
      </div>

      <div className="relative mt-5 flex flex-wrap gap-2">
        {STEP_ORDER.map((id, i) => {
          const step = stepById(status, id);
          const active = i === stepIndex;
          const done = step?.complete;
          return (
            <button
              key={id}
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
              {step?.label ?? id}
              {step?.optional ? ' (optional)' : ''}
            </button>
          );
        })}
      </div>

      <div className="relative mt-6 rounded-xl border border-zinc-800/80 bg-black/30 p-4 sm:p-5">
        {currentStepId === 'founder' && (
          <>
            <h3 className="font-semibold text-white">Step 1 — Activate founder profile</h3>
            <p className="mt-1 text-sm text-zinc-500">Unlocks Copilot, GitHub sync, and your project room.</p>
            {currentStep?.complete ? (
              <p className="mt-4 text-sm text-emerald-300">Profile active{status.projectName ? `: ${status.projectName}` : ''}.</p>
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

        {currentStepId === 'github' && (
          <>
            <h3 className="font-semibold text-white">Step 2 — Connect GitHub repo</h3>
            <p className="mt-1 text-sm text-zinc-500">
              We poll your repo every 15 minutes and when you open Copilot. New commits draft build updates
              automatically (deduped by SHA).
            </p>
            <div className="mt-4 flex flex-col gap-2 sm:flex-row">
              <input
                value={repoInput}
                onChange={(e) => setRepoInput(e.target.value)}
                placeholder="owner/repo"
                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              />
            </div>
            <label className="mt-3 block text-sm">
              <span className="text-zinc-400">GitHub token (repo scope — private repos &amp; issues)</span>
              <input
                type="password"
                value={githubToken}
                onChange={(e) => setGithubToken(e.target.value)}
                placeholder="ghp_… optional now, required for private repos"
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              />
            </label>
            {status.githubLastSyncedAt && (
              <p className="mt-2 text-xs text-emerald-400/90">
                Auto-sync active · last checked {new Date(status.githubLastSyncedAt).toLocaleString()}
              </p>
            )}
            {currentStep?.complete && currentStep.detail && (
              <p className="mt-2 text-xs text-emerald-300">Connected: {currentStep.detail}</p>
            )}
          </>
        )}

        {currentStepId === 'ai_stack' && (
          <>
            <h3 className="font-semibold text-white">Step 3 — Connect AI Stack</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Without an LLM or Cursor, Copilot falls back to rule-based replies. Add at least one provider.
            </p>
            {currentStep?.complete ? (
              <p className="mt-4 text-sm text-emerald-300">
                AI Stack connected{currentStep.detail ? ` (${currentStep.detail})` : ''}.
              </p>
            ) : (
              <ul className="mt-4 list-inside list-disc space-y-1 text-sm text-zinc-400">
                <li>DeepSeek, OpenAI, Claude, Gemini, or OpenRouter API key</li>
                <li>Or Cursor Cloud Agents for remote builds</li>
                <li>Or Ollama / Phala via AI Stack settings</li>
              </ul>
            )}
            <Link
              href="/settings/builder"
              className="mt-4 inline-flex rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500"
            >
              Open AI Stack →
            </Link>
          </>
        )}

        {currentStepId === 'goal' && (
          <>
            <h3 className="font-semibold text-white">Step 4 — Set your current goal</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Copilot uses this in welcome-back briefings and task suggestions.
            </p>
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
            <h3 className="font-semibold text-white">Step 5 — Founder Node on Windows (optional)</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Self-custody vault on your PC. Founder OS only receives metadata snapshots — full memory stays local.
            </p>
            <div className="mt-4">
              <FounderNodeDownloads />
            </div>
            <ol className="mt-4 list-inside list-decimal space-y-1 text-xs text-zinc-400">
              <li>Install Founder Node (.exe) and launch from the system tray</li>
              <li>Generate a pairing code below and paste it in the tray menu</li>
              <li>In AI Stack → Memory storage, choose Founder Node</li>
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

        <div className="mt-5 flex flex-wrap items-center gap-2">
          {stepIndex > 0 && (
            <button
              type="button"
              onClick={goBack}
              className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:text-white"
            >
              Back
            </button>
          )}

          {currentStepId === 'founder' && !currentStep?.complete && (
            <button
              type="button"
              disabled={busy}
              onClick={activateFounder}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? 'Activating…' : 'Activate profile'}
            </button>
          )}

          {currentStepId === 'github' && (
            <button
              type="button"
              disabled={busy}
              onClick={connectRepo}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? 'Connecting…' : currentStep?.complete ? 'Update repo & sync' : 'Connect & auto-sync'}
            </button>
          )}

          {currentStepId === 'ai_stack' && (
            <>
              <button
                type="button"
                onClick={() => {
                  load();
                  if (currentStep?.complete) goNext();
                }}
                className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300"
              >
                I connected — refresh
              </button>
              {currentStep?.complete && (
                <button type="button" onClick={goNext} className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white">
                  Next
                </button>
              )}
            </>
          )}

          {currentStepId === 'goal' && (
            <button
              type="button"
              disabled={busy}
              onClick={saveGoal}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? 'Saving…' : 'Save goal'}
            </button>
          )}

          {currentStepId === 'founder_node' && (
            <>
              <button type="button" onClick={dismiss} className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white">
                Finish setup
              </button>
              <button type="button" onClick={dismiss} className="text-xs text-zinc-500 underline">
                Skip vault for now
              </button>
            </>
          )}

          {currentStep?.complete &&
            currentStepId !== 'founder_node' &&
            currentStepId !== 'ai_stack' &&
            currentStepId !== 'github' &&
            stepIndex < STEP_ORDER.length - 1 && (
              <button type="button" onClick={goNext} className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300">
                Next
              </button>
            )}

          {currentStep?.complete && currentStepId === 'github' && stepIndex < STEP_ORDER.length - 1 && (
            <button type="button" onClick={goNext} className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-300">
              Next
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function clearFounderOnboardingDismiss() {
  if (typeof window !== 'undefined') window.localStorage.removeItem(DISMISS_KEY);
}
