'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  CommandBarIntent,
  copilotResume,
  fetchCopilotMemory,
  fetchCopilotStandup,
  fetchDeviceMemorySync,
  pushDeviceMemorySync,
  ProjectMemory,
  runCommandBar,
  updateBuilderSettings,
} from '@/lib/api';
import {
  isOnline,
  loadLocalMemory,
  memoryFromProject,
  mergeMemory,
  saveLocalMemory,
} from '@/lib/founder-os-local-memory';

type CommandDef = { intent: CommandBarIntent; label: string; placeholder: string };

type FounderCopilotBriefingProps = {
  accessToken: string;
  variant?: 'full' | 'sidebar';
  founderActive?: boolean;
  onMessage?: (msg: string) => void;
  onRefresh?: () => void;
  commands?: CommandDef[];
};

export function FounderCopilotBriefing({
  accessToken,
  variant = 'full',
  founderActive = true,
  onMessage,
  onRefresh,
  commands,
}: FounderCopilotBriefingProps) {
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [standup, setStandup] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [goalDraft, setGoalDraft] = useState('');
  const [savingGoal, setSavingGoal] = useState(false);
  const [cmdIntent, setCmdIntent] = useState<CommandBarIntent>('roadmap');
  const [cmdPrompt, setCmdPrompt] = useState('');
  const [cmdBusy, setCmdBusy] = useState(false);
  const isSidebar = variant === 'sidebar';

  const load = useCallback(async () => {
    try {
      const [mem, stand] = await Promise.all([
        fetchCopilotMemory(accessToken),
        fetchCopilotStandup(accessToken),
      ]);

      const mode = mem.memoryStorageMode ?? 'PLATFORM';
      let display = mem;

      if (mode === 'LOCAL_DEVICE' || mode === 'LOCAL_SYNC') {
        const localPayload = memoryFromProject({
          projectName: mem.project?.name,
          currentGoal: mem.currentGoal,
          tasksFile: {
            version: 1,
            updatedAt: new Date().toISOString(),
            currentGoal: mem.currentGoal,
            tasks: mem.openTasks.map((t) => ({
              id: t.id,
              title: t.title,
              status: t.status,
              kind: t.kind,
              done: t.done,
            })),
          },
        });
        saveLocalMemory(localPayload);

        if (mode === 'LOCAL_SYNC' && isOnline()) {
          try {
            const remote = await fetchDeviceMemorySync(accessToken);
            const merged = mergeMemory(localPayload, remote.payload);
            if (merged && merged.currentGoal !== mem.currentGoal) {
              display = { ...mem, currentGoal: merged.currentGoal };
            }
            if (merged) {
              await pushDeviceMemorySync(merged, accessToken);
            }
          } catch {
            /* offline or relay disabled */
          }
        }
      }

      setMemory(display);
      setGoalDraft(display.currentGoal);
      setStandup(stand.standup);
    } catch {
      const local = loadLocalMemory();
      if (local) {
        setMemory({
          welcomeMessage: 'Local memory loaded',
          project: null,
          currentGoal: local.currentGoal,
          progressPercent: 0,
          launchReadiness: 0,
          buildStreakDays: 0,
          lastActivityAt: null,
          lastActivityLabel: 'Local device',
          lastCommit: null,
          repoFullName: null,
          currentBranch: null,
          openTasks: (local.tasksFile?.tasks ?? []).map((t) => ({
            id: t.id,
            title: t.title,
            kind: t.kind,
            status: t.status,
            done: t.done,
          })),
          suggestedNextStep: local.tasksFile?.tasks[0]?.title ?? local.currentGoal,
          deployments: [],
          raiseStatus: null,
          community: { followers: 0, featureRequests: 0 },
          defaultAiProvider: 'RULE_BASED',
          memoryStorageMode: 'LOCAL_DEVICE',
          cursorCopy: local.currentGoal,
        });
        setStandup(null);
      } else {
        setMemory(null);
        setStandup(null);
      }
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function saveGoal() {
    const next = goalDraft.trim();
    if (!next || !memory) return;
    setSavingGoal(true);
    try {
      await updateBuilderSettings({ currentGoalFocus: next }, accessToken);
      setMemory({ ...memory, currentGoal: next });
      onMessage?.('Goal updated');
      onRefresh?.();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Could not save goal');
    } finally {
      setSavingGoal(false);
    }
  }

  async function handleCommand() {
    if (!founderActive) {
      onMessage?.('Activate your founder profile first');
      return;
    }
    setCmdBusy(true);
    try {
      const result = await runCommandBar(cmdIntent, cmdPrompt.trim() || undefined, accessToken);
      onMessage?.(`${result.result.title} (${result.creditsSpent} credits)`);
      setCmdPrompt('');
      load();
      onRefresh?.();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Command failed');
    } finally {
      setCmdBusy(false);
    }
  }

  async function handleResume() {
    setBusy(true);
    try {
      const result = await copilotResume(accessToken);
      const dispatch = result.cursorCloudDispatch ?? result.openHandsDispatch;
      if (dispatch && 'error' in dispatch && dispatch.error) {
        onMessage?.(`${result.message} (${dispatch.error})`);
      } else if (dispatch && 'agentUrl' in dispatch && dispatch.agentUrl) {
        onMessage?.(`${result.message} Open agent: ${dispatch.agentUrl}`);
      } else if (dispatch && 'conversationUrl' in dispatch && dispatch.conversationUrl) {
        onMessage?.(`${result.message} Open: ${dispatch.conversationUrl}`);
      } else {
        onMessage?.(result.dispatchHint ?? result.message);
      }
      load();
      onRefresh?.();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Could not resume');
    } finally {
      setBusy(false);
    }
  }

  async function copyCursor() {
    if (!memory?.cursorCopy) return;
    await navigator.clipboard.writeText(memory.cursorCopy);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (!memory) {
    return (
      <div className={`rounded-2xl border border-zinc-800 bg-zinc-900/40 text-sm text-zinc-500 ${isSidebar ? 'p-4' : 'p-6'}`}>
        Loading project memory…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section
        className={`rounded-2xl border border-emerald-500/35 bg-gradient-to-br from-emerald-950/40 to-zinc-950 ${
          isSidebar ? 'p-4' : 'p-5 sm:p-6'
        }`}
      >
        {!isSidebar && (
          <p className="text-lg font-semibold text-white">{memory.welcomeMessage}</p>
        )}
        {isSidebar && (
          <p className="text-sm font-semibold text-white">Project scope</p>
        )}
        <p className={`text-emerald-300/80 ${isSidebar ? 'mt-0.5 text-[10px]' : 'mt-1 text-xs'}`}>
          {memory.memoryStorageMode === 'LOCAL_DEVICE'
            ? 'Local device memory'
            : memory.memoryStorageMode === 'LOCAL_SYNC'
              ? 'Local + cloud sync'
              : memory.memoryStorageMode === 'GITHUB'
                ? 'GitHub repo memory'
                : 'Cloud memory'}
          {memory.repoFullName ? ` · ${memory.repoFullName}` : ''}
        </p>

        <div className={`mt-4 grid gap-3 ${isSidebar ? 'grid-cols-1' : 'sm:grid-cols-2 lg:grid-cols-3'}`}>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-zinc-500">Project</p>
            <p className="mt-0.5 font-semibold text-white">{memory.project?.name ?? 'Activate founder profile'}</p>
          </div>
          <div className="sm:col-span-2">
            <p className="text-[10px] uppercase tracking-wider text-zinc-500">Current goal</p>
            {isSidebar ? (
              <div className="mt-1 flex flex-col gap-2">
                <textarea
                  value={goalDraft}
                  onChange={(e) => setGoalDraft(e.target.value)}
                  rows={2}
                  className="w-full rounded-lg border border-zinc-700 bg-black/40 px-2 py-1.5 text-xs text-violet-100 outline-none focus:border-violet-500/50"
                />
                <button
                  type="button"
                  disabled={savingGoal || goalDraft.trim() === memory.currentGoal}
                  onClick={saveGoal}
                  className="self-start rounded-lg bg-violet-600 px-3 py-1 text-[10px] font-semibold text-white disabled:opacity-50"
                >
                  {savingGoal ? 'Saving…' : 'Save goal'}
                </button>
              </div>
            ) : (
              <p className="mt-0.5 font-medium text-violet-200 line-clamp-2">{memory.currentGoal}</p>
            )}
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-zinc-500">Progress</p>
            <div className="mt-1 flex items-center gap-2">
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-all"
                  style={{ width: `${memory.progressPercent}%` }}
                />
              </div>
              <span className="text-sm font-bold text-emerald-300">{memory.progressPercent}%</span>
            </div>
          </div>
        </div>

        {!isSidebar && (
          <>
            <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
              <div className="rounded-lg bg-black/30 px-3 py-2">
                <p className="text-[10px] uppercase text-zinc-600">Last activity</p>
                <p className="text-zinc-300">{memory.lastActivityLabel}</p>
              </div>
              <div className="rounded-lg bg-black/30 px-3 py-2">
                <p className="text-[10px] uppercase text-zinc-600">Last commit</p>
                <p className="truncate text-zinc-300">{memory.lastCommit ?? 'Connect GitHub to sync'}</p>
              </div>
            </div>

            {memory.openTasks.length > 0 && (
              <div className="mt-4">
                <p className="text-[10px] uppercase text-zinc-500">Remaining</p>
                <ul className="mt-2 space-y-1">
                  {memory.openTasks.map((t) => (
                    <li key={t.id} className="flex items-start gap-2 text-sm text-zinc-300">
                      <span className="text-zinc-600">□</span>
                      {t.title}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        {isSidebar && memory.lastCommit && (
          <p className="mt-3 truncate text-[11px] text-zinc-500">
            Last commit: <span className="text-zinc-400">{memory.lastCommit}</span>
          </p>
        )}

        <div className={`rounded-lg border border-amber-500/30 bg-amber-950/20 px-3 py-2 ${isSidebar ? 'mt-3' : 'mt-4'}`}>
          <p className="text-[10px] uppercase text-amber-400/80">Suggested next step</p>
          <p className="mt-0.5 text-sm font-medium text-amber-100">{memory.suggestedNextStep}</p>
        </div>

        <div className={`flex flex-wrap gap-2 ${isSidebar ? 'mt-3' : 'mt-5'}`}>
          <button
            type="button"
            disabled={busy}
            onClick={handleResume}
            className={`rounded-xl bg-emerald-600 font-semibold text-white disabled:opacity-50 ${
              isSidebar ? 'w-full px-3 py-2 text-xs' : 'px-5 py-2.5 text-sm'
            }`}
          >
            {busy ? 'Loading…' : 'Continue where I left off'}
          </button>
          {!isSidebar && (
            <button
              type="button"
              onClick={copyCursor}
              className="rounded-xl border border-indigo-500/40 px-4 py-2.5 text-sm text-indigo-200"
            >
              {copied ? 'Copied!' : 'Copy for builder'}
            </button>
          )}
        </div>

        {(memory.deployments.length > 0 || memory.raiseStatus) && !isSidebar && (
          <div className="mt-4 flex flex-wrap gap-2 text-[11px]">
            {memory.deployments.map((d) => (
              <span
                key={d.provider}
                className="rounded-full border border-emerald-500/30 bg-emerald-950/30 px-2 py-0.5 text-emerald-300"
              >
                {d.label} · healthy
              </span>
            ))}
            {memory.raiseStatus && (
              <span className="rounded-full border border-violet-500/30 px-2 py-0.5 text-violet-300">
                Raise ${memory.raiseStatus.allocatedUsd.toLocaleString()} / $
                {memory.raiseStatus.goalUsd.toLocaleString()}
              </span>
            )}
          </div>
        )}
      </section>

      {commands && commands.length > 0 && (
        <section className="rounded-xl border border-cyan-500/30 bg-cyan-950/10 p-4">
          <p className="text-xs font-semibold text-cyan-200">Quick commands</p>
          <p className="mt-0.5 text-[10px] text-zinc-500">Roadmap · release notes · weekly summary</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {commands.map((c) => (
              <button
                key={c.intent}
                type="button"
                onClick={() => setCmdIntent(c.intent)}
                className={`rounded-lg px-2.5 py-1 text-[10px] ${
                  cmdIntent === c.intent
                    ? 'bg-cyan-600 text-white'
                    : 'border border-zinc-700 text-zinc-400'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
          <div className="mt-2 flex flex-col gap-2">
            <input
              value={cmdPrompt}
              onChange={(e) => setCmdPrompt(e.target.value)}
              placeholder={commands.find((c) => c.intent === cmdIntent)?.placeholder}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-xs"
            />
            <button
              type="button"
              disabled={cmdBusy}
              onClick={handleCommand}
              className="rounded-lg bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              {cmdBusy ? 'Running…' : 'Run from GitHub context'}
            </button>
          </div>
        </section>
      )}

      {standup && !isSidebar && (
        <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Daily standup</p>
          <pre className="mt-2 whitespace-pre-wrap text-sm text-zinc-300">{standup}</pre>
        </section>
      )}
    </div>
  );
}
