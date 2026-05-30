'use client';

import { useCallback, useState } from 'react';
import { MEMORY_STORAGE_MODES, type MemoryStorageModeKey } from '@dcf/utils';
import {
  fetchDeviceMemorySync,
  pushDeviceMemorySync,
  syncFounderOsMemory,
  updateBuilderSettings,
} from '@/lib/api';
import { FounderNodePairingPanel } from '@/components/founder-node-pairing-panel';

type Props = {
  accessToken: string;
  currentMode: MemoryStorageModeKey;
  onModeChange: (mode: MemoryStorageModeKey) => void;
};

export function MemoryStoragePanel({ accessToken, currentMode, onModeChange }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmLocal, setConfirmLocal] = useState(false);

  const saveMode = useCallback(
    async (mode: MemoryStorageModeKey) => {
      setErr(null);
      setMsg(null);
      if (mode === 'LOCAL_DEVICE' && !confirmLocal) {
        setConfirmLocal(true);
        return;
      }
      setBusy('mode');
      try {
        await updateBuilderSettings({ memoryStorageMode: mode }, accessToken);
        onModeChange(mode);
        setMsg(
          mode === 'LOCAL_DEVICE'
            ? 'Saved on this device only — data will not sync to other devices.'
            : mode === 'LOCAL_SYNC'
              ? 'Local + cloud sync enabled — online devices share a lightweight memory snapshot.'
              : mode === 'GITHUB'
                ? 'GitHub repo memory selected — connect PAT and sync to write .github/founder-os/.'
              : mode === 'FOUNDER_NODE'
                ? 'Founder Node selected — pair your desktop vault below. Full memory stays on your machine.'
                : 'Using Founder OS cloud memory.',
        );
        setConfirmLocal(false);
      } catch (e) {
        setErr(e instanceof Error ? e.message : 'Could not save mode');
      } finally {
        setBusy(null);
      }
    },
    [accessToken, confirmLocal, onModeChange],
  );

  async function syncGithub() {
    setBusy('github');
    setErr(null);
    try {
      const result = await syncFounderOsMemory(accessToken);
      if (result.synced) {
        setMsg(`Synced memory to GitHub repo ${result.repo ?? ''}.`);
      } else {
        setErr(result.reason ?? 'GitHub sync failed — connect repo + PAT in Builder settings.');
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'GitHub sync failed');
    } finally {
      setBusy(null);
    }
  }

  async function syncCloudRelay() {
    setBusy('cloud');
    setErr(null);
    try {
      const remote = await fetchDeviceMemorySync(accessToken);
      if (remote.payload) {
        setMsg(
          `Cloud snapshot available (updated ${new Date(remote.updatedAt ?? '').toLocaleString()} from ${remote.deviceLabel ?? 'another device'}). Copilot will merge on load.`,
        );
      } else {
        setMsg('No cloud snapshot yet — open Founder Copilot on mobile while online to push one.');
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Cloud sync check failed');
    } finally {
      setBusy(null);
    }
  }

  async function pushNow() {
    setBusy('push');
    setErr(null);
    try {
      const { loadLocalMemory, memoryFromProject, deviceLabel } = await import(
        '@/lib/founder-os-local-memory'
      );
      const local = loadLocalMemory();
      const payload =
        local ??
        memoryFromProject({
          currentGoal: 'Define your next milestone',
        });
      await pushDeviceMemorySync({ ...payload, deviceLabel: deviceLabel() }, accessToken);
      setMsg('Pushed local memory snapshot to secure cloud relay — other signed-in devices can pull it.');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Push failed');
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="rounded-xl border border-violet-500/30 bg-violet-950/15 p-5">
      <h3 className="font-semibold text-violet-100">Project memory storage</h3>
      <p className="mt-1 text-xs text-violet-200/70">
        Choose where Founder Copilot keeps goals, tasks, and resume context. Local modes save platform
        cost — sync when online to use any device.
      </p>

      <div className="mt-4 space-y-2">
        {MEMORY_STORAGE_MODES.map((mode) => (
          <label
            key={mode.key}
            className={`flex cursor-pointer gap-3 rounded-lg border p-3 text-sm ${
              currentMode === mode.key
                ? 'border-violet-400 bg-violet-950/40'
                : 'border-zinc-700 hover:border-zinc-600'
            }`}
          >
            <input
              type="radio"
              name="memoryStorageMode"
              checked={currentMode === mode.key}
              onChange={() => saveMode(mode.key)}
              disabled={busy === 'mode'}
              className="mt-1"
            />
            <span>
              <span className="font-medium text-white">{mode.label}</span>
              <span className="mt-0.5 block text-xs text-zinc-400">{mode.description}</span>
            </span>
          </label>
        ))}
      </div>

      {confirmLocal && currentMode !== 'LOCAL_DEVICE' && (
        <div className="mt-4 rounded-lg border border-amber-500/40 bg-amber-950/25 p-3 text-xs text-amber-100">
          <p className="font-semibold">Before you choose “This device only”</p>
          <ul className="mt-2 list-inside list-disc space-y-1 text-amber-100/90">
            <li>Memory stays in this browser — not our servers.</li>
            <li>Switching phones or clearing browser data is hard to recover from.</li>
            <li>No automatic sync to other devices.</li>
          </ul>
          <p className="mt-2">
            For mobile, <strong>Local + cloud sync</strong> is usually better: free local storage,
            and when online we relay a tiny snapshot (not full AI chats) so you can resume anywhere.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => saveMode('LOCAL_DEVICE')}
              className="rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-medium text-white"
            >
              Yes, this device only
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmLocal(false);
                saveMode('LOCAL_SYNC');
              }}
              className="rounded-lg border border-emerald-500/50 px-3 py-1.5 text-xs text-emerald-300"
            >
              Use Local + cloud sync instead
            </button>
            <button
              type="button"
              onClick={() => setConfirmLocal(false)}
              className="rounded-lg border border-zinc-600 px-3 py-1.5 text-xs text-zinc-400"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {currentMode === 'GITHUB' && (
          <button
            type="button"
            disabled={!!busy}
            onClick={syncGithub}
            className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          >
            {busy === 'github' ? 'Syncing…' : 'Sync to GitHub repo'}
          </button>
        )}
        {currentMode === 'LOCAL_SYNC' && (
          <>
            <button
              type="button"
              disabled={!!busy}
              onClick={pushNow}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              {busy === 'push' ? 'Pushing…' : 'Push snapshot to cloud'}
            </button>
            <button
              type="button"
              disabled={!!busy}
              onClick={syncCloudRelay}
              className="rounded-lg border border-zinc-600 px-3 py-1.5 text-xs text-zinc-300 disabled:opacity-50"
            >
              Check cloud snapshot
            </button>
          </>
        )}
      </div>

      {currentMode === 'FOUNDER_NODE' && (
        <FounderNodePairingPanel accessToken={accessToken} active={currentMode === 'FOUNDER_NODE'} />
      )}

      {msg && <p className="mt-3 text-xs text-emerald-300">{msg}</p>}
      {err && <p className="mt-3 text-xs text-red-300">{err}</p>}
    </section>
  );
}
