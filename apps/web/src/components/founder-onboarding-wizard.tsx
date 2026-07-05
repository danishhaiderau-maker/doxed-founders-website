'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FOUNDER_OS_IDENTITY,
  IDE_ADAPTERS,
  getAvailableIdeAdapters,
  getComingSoonIdeAdapters,
  type IdeAdapter,
  type IdeAdapterId,
  type OnboardingPathId,
} from '@dcf/utils';
import { FounderNodeDownloads } from '@/components/founder-node-downloads';
import { FOUNDER_NODE_MIN_VERSION_LABEL } from '@/lib/founder-node-requirements';
import { FounderOnboardingAiStack } from '@/components/founder-onboarding-ai-stack';
import { FounderOnboardingComplete } from '@/components/founder-onboarding-complete';
import { trackOnboardingStep } from '@/lib/onboarding-track';
import {
  createFounderNodePairingCode,
  fetchFounderOnboardingStatus,
  submitFounderApplication,
  updateBuilderSettings,
  type FounderOnboardingStatus,
} from '@/lib/api';

const DISMISS_KEY = 'dcf-founder-onboarding-dismissed';
const IDE_STORAGE_KEY = 'dcf-founder-onboarding-ide';
const BRAIN_STORAGE_KEY = 'dcf-founder-onboarding-brain';
const BUILD_PUBLIC_STORAGE_KEY = 'dcf-founder-onboarding-build-public';

type BrainChoice = 'ide' | 'external';

type Props = {
  accessToken: string;
  onRefresh?: () => void;
  onMessage?: (msg: string) => void;
  onLaunchPrompt?: (prompt: string) => void;
  onDismiss?: () => void;
  initialPath?: OnboardingPathId | null;
};

const WIZARD_STEPS = [
  { id: 'ide', label: 'Choose IDE' },
  { id: 'pair', label: 'Pair Founder Node' },
  { id: 'sync', label: 'Synchronize Desktop' },
  { id: 'brain', label: 'Choose Brain' },
  { id: 'community', label: 'Community' },
] as const;

const DISCOVERY_ITEMS = [
  'Repositories',
  'Recent Workspaces',
  'Recent Chats',
  'Branches',
  'Running Agents',
  'Terminal',
  'Deployments',
  'Current Workspace',
  'Git Status',
] as const;

export function FounderOnboardingWizard({
  accessToken,
  onRefresh,
  onMessage,
  onLaunchPrompt,
  onDismiss: onDismissProp,
}: Props) {
  const [status, setStatus] = useState<FounderOnboardingStatus | null>(null);
  const [dismissed, setDismissed] = useState(true);
  const [stepIndex, setStepIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [selectedIde, setSelectedIde] = useState<IdeAdapterId | null>(null);
  const [brainChoice, setBrainChoice] = useState<BrainChoice | null>(null);
  const [showExternalBrain, setShowExternalBrain] = useState(false);
  const [showIdeInfo, setShowIdeInfo] = useState(true);
  const [buildInPublic, setBuildInPublic] = useState(true);

  const [projectName, setProjectName] = useState('');
  const [ideaDescription, setIdeaDescription] = useState('');
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [pairingExpires, setPairingExpires] = useState<string | null>(null);

  // Step 3 — progressive discovery animation state
  const [discoveredCount, setDiscoveredCount] = useState(0);
  const [discoveryDone, setDiscoveryDone] = useState(false);

  const availableIde = useMemo(() => getAvailableIdeAdapters(), []);
  const comingSoonIde = useMemo(() => getComingSoonIdeAdapters(), []);
  const ideList = useMemo(() => IDE_ADAPTERS, []);

  const load = useCallback(async () => {
    try {
      const data = await fetchFounderOnboardingStatus(accessToken);
      setStatus(data);
      setProjectName((prev) => prev || data.projectName || '');
    } catch {
      setStatus(null);
    }
  }, [accessToken]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    setDismissed(window.localStorage.getItem(DISMISS_KEY) === '1');
    const storedIde = window.localStorage.getItem(IDE_STORAGE_KEY) as IdeAdapterId | null;
    if (storedIde) setSelectedIde(storedIde);
    const storedBrain = window.localStorage.getItem(BRAIN_STORAGE_KEY) as BrainChoice | null;
    if (storedBrain) setBrainChoice(storedBrain);
    const storedBip = window.localStorage.getItem(BUILD_PUBLIC_STORAGE_KEY);
    if (storedBip === '0') setBuildInPublic(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const currentStepId = WIZARD_STEPS[stepIndex].id;

  useEffect(() => {
    if (!currentStepId) return;
    trackOnboardingStep(currentStepId, 'view', status?.onboardingPath ?? status?.effectivePath);
  }, [currentStepId, status?.onboardingPath, status?.effectivePath]);

  // Drive the discovery animation while the user is on Step 3.
  useEffect(() => {
    if (currentStepId !== 'sync' || discoveryDone) return;
    if (discoveredCount >= DISCOVERY_ITEMS.length) {
      setDiscoveryDone(true);
      return;
    }
    const t = window.setTimeout(() => {
      setDiscoveredCount((c) => Math.min(c + 1, DISCOVERY_ITEMS.length));
    }, 420);
    return () => window.clearTimeout(t);
  }, [currentStepId, discoveredCount, discoveryDone]);

  if (dismissed) return null;

  if (status?.requiredComplete && status) {
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

  function chooseIde(id: IdeAdapterId) {
    setSelectedIde(id);
    if (typeof window !== 'undefined') window.localStorage.setItem(IDE_STORAGE_KEY, id);
  }

  function chooseBrain(choice: BrainChoice) {
    setBrainChoice(choice);
    if (typeof window !== 'undefined') window.localStorage.setItem(BRAIN_STORAGE_KEY, choice);
    if (choice === 'external') setShowExternalBrain(true);
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

  async function finishWizard() {
    // Activate founder profile if not already live — preserves existing API path
    // so the server flips `requiredComplete` and the complete screen can render.
    const needsProfile = !status?.projectName;
    if (needsProfile && (!projectName.trim() || !ideaDescription.trim())) {
      setErr('Project name and idea are required to go live');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(BUILD_PUBLIC_STORAGE_KEY, buildInPublic ? '1' : '0');
      }
      await updateBuilderSettings(
        { autoPublishOnEvent: buildInPublic, currentGoalFocus: ideaDescription.trim() || undefined },
        accessToken,
      );
      if (needsProfile) {
        await submitFounderApplication(
          {
            projectName: projectName.trim(),
            ideaDescription: ideaDescription.trim(),
            lifecycleStage: 'IDEA',
          },
          accessToken,
        );
      }
      onMessage?.(buildInPublic ? 'Build in Public enabled — welcome to Founder OS' : 'Setup complete');
      const refreshed = await fetchFounderOnboardingStatus(accessToken);
      setStatus(refreshed);
      onRefresh?.();
      if (!refreshed.requiredComplete) {
        dismiss();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not finish setup');
    } finally {
      setBusy(false);
    }
  }

  function goNext() {
    setErr(null);
    setStepIndex((i) => Math.min(i + 1, WIZARD_STEPS.length - 1));
  }

  function goBack() {
    setErr(null);
    setStepIndex((i) => Math.max(i - 1, 0));
  }

  const stepLabel = WIZARD_STEPS[stepIndex].label;

  return (
    <div className="relative overflow-hidden rounded-2xl border border-violet-500/35 bg-gradient-to-br from-violet-950/40 via-[#0a0a12] to-cyan-950/20 p-5 sm:p-6">
      <div className="pointer-events-none absolute -right-8 -top-8 h-32 w-32 rounded-full bg-violet-600/10 blur-2xl" />
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-300">
            Founder OS · IDE-first setup
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">{FOUNDER_OS_IDENTITY.tagline}</h2>
          <p className="mt-1 max-w-2xl text-sm text-zinc-400">{FOUNDER_OS_IDENTITY.promise}</p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
        >
          Continue later
        </button>
      </div>

      {/* Step pills */}
      <div className="relative mt-5 flex flex-wrap gap-2">
        {WIZARD_STEPS.map((step, i) => {
          const active = i === stepIndex;
          const done = i < stepIndex;
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
              {i + 1}. {step.label}
            </button>
          );
        })}
      </div>

      <div className="relative mt-6 rounded-xl border border-zinc-800/80 bg-black/30 p-4 sm:p-5">
        {/* STEP 1 — Choose your IDE */}
        {currentStepId === 'ide' && (
          <section>
            <h3 className="font-semibold text-white">Step 1 — Choose your IDE</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Connect your IDE to Founder OS so you can continue your work remotely from any browser.
            </p>

            {/* Collapsible info — how IDE integration works */}
            <div className="mt-4 overflow-hidden rounded-lg border border-violet-500/20 bg-violet-500/5">
              <button
                type="button"
                onClick={() => setShowIdeInfo((v) => !v)}
                className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm font-medium text-violet-200 hover:bg-violet-500/10"
              >
                <span className="flex items-center gap-2">
                  <span className="text-violet-400">&#9432;</span>
                  How this works — read before choosing
                </span>
                <span className="text-xs text-violet-400">{showIdeInfo ? 'Collapse ▲' : 'Expand ▼'}</span>
              </button>
              {showIdeInfo && (
                <div className="space-y-3 px-4 py-3 text-xs leading-relaxed text-zinc-300">
                  <p>
                    <span className="font-semibold text-emerald-300">No paid IDE subscription needed.</span>{' '}
                    You can start coding right now using the platform&apos;s free{' '}
                    <span className="font-semibold text-emerald-300">GLM 5.2</span> brain — it handles code
                    generation, specs, and chat without any IDE subscription.
                  </p>
                  <p>
                    <span className="font-semibold text-white">What the IDE connection does:</span> It bridges
                    your local development environment to Founder OS. Your chats, agents, workspaces, git
                    branches, and file changes are synced so you can continue work from any browser, anywhere.
                  </p>
                  <ul className="ml-4 list-disc space-y-1.5 text-zinc-400">
                    <li>
                      <span className="text-zinc-200">Use any external agent</span> — plug in any LLM key
                      (DeepSeek, Claude, GPT, GLM, Ollama) and Founder OS routes your instructions through it.
                    </li>
                    <li>
                      <span className="text-zinc-200">Use your own Cursor agent</span> — if you have a paid
                      Cursor subscription, your inbuilt Cursor agent works as-is. Founder OS sends messages
                      directly into your Cursor chat, activating the agent to start working on your idea on the
                      go.
                    </li>
                    <li>
                      <span className="text-zinc-200">Remote control, local execution</span> — you
                      can&apos;t change the AI model inside Cursor remotely, but you can send instructions and
                      the agent will work with whatever model is configured. Changes are committed to GitHub
                      and Cursor recognizes them automatically.
                    </li>
                    <li>
                      <span className="text-zinc-200">Plugins welcome</span> — use any Cursor plugin or
                      extension you have a key for. Founder OS doesn&apos;t restrict your toolchain.
                    </li>
                  </ul>
                  <div className="mt-2 rounded-md border border-zinc-700/50 bg-black/20 px-3 py-2">
                    <p>
                      <span className="font-semibold text-violet-300">Cursor is recommended</span> because
                      it&apos;s fully tested and battle-ready. OpenHands and Claude Code are also available.
                      Windsurf and VS Code are in the pipeline — being tested now. You can start with Cursor today
                      and switch later without losing your work.
                    </p>
                  </div>
                </div>
              )}
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {ideList.map((ide) => (
                <IdeCard
                  key={ide.id}
                  ide={ide}
                  selected={selectedIde === ide.id}
                  recommended={ide.id === 'cursor'}
                  disabled={!ide.available}
                  onSelect={() => chooseIde(ide.id)}
                />
              ))}
            </div>
            <p className="mt-3 text-[11px] text-zinc-600">
              {availableIde.length} IDEs available · {comingSoonIde.length} coming soon
            </p>

            {err && <p className="mt-4 text-sm text-red-300">{err}</p>}
            <div className="mt-5 flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={!selectedIde}
                onClick={goNext}
                className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
              >
                Continue
              </button>
            </div>
          </section>
        )}

        {/* STEP 2 — Pair Founder Node */}
        {currentStepId === 'pair' && (
          <section>
            <h3 className="font-semibold text-white">Step 2 — Pair Founder Node</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Founder Node securely pairs your desktop with Founder OS. No Remote Desktop. No VPN.
              Just secure synchronization.
            </p>

            <div className="mt-4">
              <FounderNodeDownloads />
            </div>

            <ol className="mt-4 list-inside list-decimal space-y-1 text-xs text-zinc-400">
              <li>Install Founder Node {FOUNDER_NODE_MIN_VERSION_LABEL}</li>
              <li>Generate a pairing code below</li>
              <li>Paste it in the Founder Node tray menu on your desktop</li>
              <li>Done — your desktop is paired</li>
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
                href="/founder-den?onboard=sovereign#founder-node-download"
                className="rounded-lg border border-zinc-600 px-3 py-1.5 text-xs text-zinc-300 hover:text-white"
              >
                Full setup hub →
              </Link>
            </div>

            {pairingCode && (
              <div className="mt-3 rounded-lg border border-cyan-400/40 bg-black/30 p-3 text-center">
                <p className="text-xs text-zinc-400">Pairing code</p>
                <p className="mt-1 font-mono text-2xl font-bold tracking-[0.3em] text-cyan-300">
                  {pairingCode}
                </p>
                {pairingExpires && (
                  <p className="mt-1 text-[10px] text-zinc-500">
                    Expires {new Date(pairingExpires).toLocaleString()}
                  </p>
                )}
              </div>
            )}

            {err && <p className="mt-4 text-sm text-red-300">{err}</p>}
            <div className="mt-5 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={goBack}
                className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:text-white"
              >
                Back
              </button>
              <button
                type="button"
                onClick={goNext}
                className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white"
              >
                Continue
              </button>
              <button type="button" onClick={goNext} className="text-xs text-zinc-500 underline">
                I'll pair later
              </button>
            </div>
          </section>
        )}

        {/* STEP 3 — Synchronize Desktop */}
        {currentStepId === 'sync' && (
          <section>
            <h3 className="font-semibold text-white">Step 3 — Synchronize Desktop</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Founder Node is discovering your desktop automatically. No manual configuration needed.
            </p>

            <div className="mt-4 rounded-lg border border-zinc-800 bg-black/40 p-4">
              <div className="flex items-center gap-2 text-xs text-zinc-400">
                <span
                  className={`inline-block h-2 w-2 rounded-full ${
                    discoveryDone ? 'bg-emerald-400' : 'animate-pulse bg-cyan-400'
                  }`}
                />
                {discoveryDone ? 'Synchronization complete' : 'Scanning your desktop…'}
              </div>
              <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                {DISCOVERY_ITEMS.map((item, i) => {
                  const found = i < discoveredCount;
                  return (
                    <li
                      key={item}
                      className={`flex items-center gap-2 rounded-md border px-3 py-2 text-xs transition ${
                        found
                          ? 'border-emerald-500/30 bg-emerald-950/20 text-emerald-200'
                          : 'border-zinc-800 bg-zinc-950/40 text-zinc-500'
                      }`}
                    >
                      <span className="font-mono">{found ? '✓' : '·'}</span>
                      <span>{item}</span>
                      {!found && (
                        <span className="ml-auto text-[10px] text-zinc-600">discovering…</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>

            {err && <p className="mt-4 text-sm text-red-300">{err}</p>}
            <div className="mt-5 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={goBack}
                className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:text-white"
              >
                Back
              </button>
              <button
                type="button"
                onClick={goNext}
                className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white"
              >
                Continue
              </button>
            </div>
          </section>
        )}

        {/* STEP 4 — Choose Brain */}
        {currentStepId === 'brain' && (
          <section>
            <h3 className="font-semibold text-white">Step 4 — Choose Brain</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Decide how Founder OS thinks. You can change this anytime.
            </p>

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <BrainCard
                title="Use IDE Brain"
                recommended
                selected={brainChoice === 'ide'}
                onSelect={() => chooseBrain('ide')}
              >
                Founder OS will simply continue using whatever AI your IDE already uses. Example:{' '}
                <span className="text-zinc-200">Cursor → Cursor Auto</span>. No API required. No
                changes.
              </BrainCard>

              <BrainCard
                title="External Brain"
                selected={brainChoice === 'external'}
                onSelect={() => chooseBrain('external')}
              >
                External brains allow Founder Node to execute tasks independently while keeping your
                IDE synchronized.
              </BrainCard>
            </div>

            {brainChoice === 'external' && showExternalBrain && (
              <div className="mt-4 rounded-lg border border-zinc-800 bg-black/30 p-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
                  External brain providers
                </p>
                <p className="mt-1 text-[11px] text-zinc-500">
                  GPT, Claude, GLM, DeepSeek, Gemini, OpenRouter, Surplus, Jatevo, Ollama, Founder
                  Vault Memory.
                </p>
                <div className="mt-3">
                  <FounderOnboardingAiStack
                    accessToken={accessToken}
                    llmConnected={status?.llmConnected ?? false}
                    builderConnected={status?.builderConnected ?? false}
                    promo={status?.promo}
                    onConnected={() => void load()}
                    onMessage={onMessage}
                  />
                </div>
              </div>
            )}

            {err && <p className="mt-4 text-sm text-red-300">{err}</p>}
            <div className="mt-5 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={goBack}
                className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:text-white"
              >
                Back
              </button>
              <button
                type="button"
                disabled={!brainChoice}
                onClick={goNext}
                className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white disabled:opacity-40"
              >
                Continue
              </button>
            </div>
          </section>
        )}

        {/* STEP 5 — Community */}
        {currentStepId === 'community' && (
          <section>
            <h3 className="font-semibold text-white">Step 5 — Community</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Build in Public shares your progress with the Founder OS community.
            </p>

            <label className="mt-4 flex items-start gap-3 rounded-lg border border-zinc-800 bg-black/30 p-3">
              <input
                type="checkbox"
                checked={buildInPublic}
                onChange={(e) => setBuildInPublic(e.target.checked)}
                className="mt-0.5 h-4 w-4 accent-violet-600"
              />
              <span className="text-sm text-zinc-200">
                <span className="font-medium">Enable Build in Public</span>
                <span className="mt-0.5 block text-xs text-zinc-500">
                  Auto-publish key milestones and events to your public founder feed.
                </span>
              </span>
            </label>

            {!status?.projectName && (
              <div className="mt-4 rounded-lg border border-zinc-800 bg-black/30 p-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
                  Go live
                </p>
                <p className="mt-1 text-[11px] text-zinc-500">
                  Name your project so your public profile can be activated.
                </p>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <input
                    value={projectName}
                    onChange={(e) => setProjectName(e.target.value)}
                    placeholder="Project name"
                    className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                  />
                  <textarea
                    value={ideaDescription}
                    onChange={(e) => setIdeaDescription(e.target.value)}
                    placeholder="What are you building?"
                    rows={2}
                    className="sm:col-span-2 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                  />
                </div>
              </div>
            )}

            {err && <p className="mt-4 text-sm text-red-300">{err}</p>}
            <div className="mt-5 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={goBack}
                className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:text-white"
              >
                Back
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={finishWizard}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                {busy ? 'Finishing…' : 'Done'}
              </button>
            </div>
          </section>
        )}
      </div>

      <p className="relative mt-3 text-[11px] text-zinc-600">
        Current step: <span className="text-zinc-400">{stepLabel}</span>
      </p>
    </div>
  );
}

function IdeCard({
  ide,
  selected,
  recommended,
  disabled,
  onSelect,
}: {
  ide: IdeAdapter;
  selected?: boolean;
  recommended?: boolean;
  disabled?: boolean;
  onSelect?: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onSelect}
      className={`relative rounded-xl border p-4 text-left transition ${
        disabled
          ? 'cursor-not-allowed border-zinc-800 bg-zinc-950/40 opacity-60'
          : selected
            ? 'border-violet-400 bg-violet-950/40'
            : 'border-zinc-700 bg-black/30 hover:border-violet-500/60'
      }`}
    >
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-white">{ide.label}</p>
        {recommended && (
          <span className="rounded-full bg-violet-600 px-2 py-0.5 text-[10px] font-medium text-white">
            Recommended
          </span>
        )}
        {disabled && (
          <span className="rounded-full border border-zinc-700 px-2 py-0.5 text-[10px] text-zinc-500">
            Coming Soon
          </span>
        )}
      </div>
      <p className="mt-1 text-[11px] text-zinc-500">
        {disabled
          ? 'Support for this IDE is on the roadmap.'
          : selected
            ? 'Selected — continue when ready.'
            : 'Tap to select this IDE.'}
      </p>
    </button>
  );
}

function BrainCard({
  title,
  recommended,
  selected,
  onSelect,
  children,
}: {
  title: string;
  recommended?: boolean;
  selected?: boolean;
  onSelect: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`relative rounded-xl border p-4 text-left transition ${
        selected
          ? 'border-violet-400 bg-violet-950/40'
          : 'border-zinc-700 bg-black/30 hover:border-violet-500/60'
      }`}
    >
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-white">{title}</p>
        {recommended && (
          <span className="rounded-full bg-violet-600 px-2 py-0.5 text-[10px] font-medium text-white">
            Recommended
          </span>
        )}
      </div>
      <p className="mt-1 text-[11px] text-zinc-400">{children}</p>
    </button>
  );
}

export function clearFounderOnboardingDismiss() {
  if (typeof window !== 'undefined') window.localStorage.removeItem(DISMISS_KEY);
}

export function clearFounderOnboardingPathStorage() {
  if (typeof window !== 'undefined') {
    window.localStorage.removeItem('dcf-founder-onboarding-path');
    window.localStorage.removeItem(IDE_STORAGE_KEY);
    window.localStorage.removeItem(BRAIN_STORAGE_KEY);
    window.localStorage.removeItem(BUILD_PUBLIC_STORAGE_KEY);
  }
}
