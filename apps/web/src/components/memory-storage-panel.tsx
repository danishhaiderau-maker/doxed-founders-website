'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  MEMORY_STORAGE_MODES,
  extractVaultRelaySummary,
  type MemoryStorageModeKey,
} from '@dcf/utils';
import {
  fetchDeviceMemorySync,
  pushDeviceMemorySync,
  syncFounderOsMemory,
  updateBuilderSettings,
} from '@/lib/api';
import { FounderNodePairingPanel } from '@/components/founder-node-pairing-panel';
import { MobileVaultPanel } from '@/components/mobile-vault-panel';
import { FounderVaultStatusBanner } from '@/components/founder-vault-status-banner';

type Props = {
  accessToken: string;
  currentMode: MemoryStorageModeKey;
  onModeChange: (mode: MemoryStorageModeKey) => void;
  phalaPrivateAi?: {
    ready: boolean;
    userKeyConnected: boolean;
    platformAvailable: boolean;
    model: string;
  } | null;
  /** Render inside Founder Node hub without duplicate outer chrome */
  embedded?: boolean;
};

export function MemoryStoragePanel({ accessToken, currentMode, onModeChange, phalaPrivateAi, embedded }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmLocal, setConfirmLocal] = useState(false);
  const [confirmLeaveVault, setConfirmLeaveVault] = useState(false);
  const [pendingMode, setPendingMode] = useState<MemoryStorageModeKey | null>(null);
  const [vaultRelay, setVaultRelay] = useState<ReturnType<typeof extractVaultRelaySummary>>(null);

  const loadVaultRelay = useCallback(async () => {
    try {
      const remote = await fetchDeviceMemorySync(accessToken);
      setVaultRelay(
        extractVaultRelaySummary({
          memoryStorageMode: currentMode,
          deviceSync: remote.payload
            ? {
                updatedAt: remote.updatedAt ?? new Date().toISOString(),
                deviceLabel: remote.deviceLabel,
                payload: remote.payload,
              }
            : null,
        }),
      );
    } catch {
      setVaultRelay(null);
    }
  }, [accessToken, currentMode]);

  useEffect(() => {
    if (currentMode === 'FOUNDER_NODE' || currentMode === 'LOCAL_SYNC') {
      loadVaultRelay();
    } else {
      setVaultRelay(null);
    }
  }, [currentMode, loadVaultRelay]);

  const saveMode = useCallback(
    async (mode: MemoryStorageModeKey, skipConfirm = false) => {
      setErr(null);
      setMsg(null);

      if (mode === 'LOCAL_DEVICE' && !confirmLocal && !skipConfirm) {
        setConfirmLocal(true);
        setPendingMode(mode);
        return;
      }

      if (
        currentMode === 'FOUNDER_NODE' &&
        mode !== 'FOUNDER_NODE' &&
        !confirmLeaveVault &&
        !skipConfirm
      ) {
        setConfirmLeaveVault(true);
        setPendingMode(mode);
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
              ? 'Local + encrypted relay enabled — metadata syncs when online.'
              : mode === 'GITHUB'
                ? 'GitHub repo memory selected — connect PAT and sync to write .github/founder-os/.'
                : mode === 'FOUNDER_NODE'
                  ? 'Founder Vault enabled — pair Founder Node below. Full memory stays on your machine.'
                  : 'Using Founder OS cloud memory.',
        );
        setConfirmLocal(false);
        setConfirmLeaveVault(false);
        setPendingMode(null);
        if (mode === 'FOUNDER_NODE' || mode === 'LOCAL_SYNC') {
          await loadVaultRelay();
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : 'Could not save mode');
      } finally {
        setBusy(null);
      }
    },
    [accessToken, confirmLocal, confirmLeaveVault, currentMode, loadVaultRelay, onModeChange],
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
    <div className={embedded ? '' : 'rounded-xl border border-violet-500/30 bg-violet-950/15 p-5'}>
      {!embedded && (
        <>
          <h3 className="font-semibold text-violet-100">Founder Vault — memory storage</h3>
          <p className="mt-1 text-xs text-violet-200/70">
            Choose where goals, tasks, and private notes live. Founder Vault (Founder Node) keeps full
            company memory on your machine — we only relay encrypted metadata.
          </p>
        </>
      )}
      {!embedded && (
        <p className="mt-2 text-[10px] text-violet-300/60">
          Tagline: own your memory · own your agents · own your company intelligence.
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {phalaPrivateAi?.ready && (
          <span className="rounded-full bg-fuchsia-500/20 px-2.5 py-1 text-[10px] font-semibold text-fuchsia-100">
            Private AI (Phala TEE)
            {phalaPrivateAi.model ? ` · ${phalaPrivateAi.model}` : ''}
          </span>
        )}
      </div>

      {(currentMode === 'FOUNDER_NODE' || currentMode === 'LOCAL_SYNC') && (
        <div className="mt-4">
          <FounderVaultStatusBanner relay={vaultRelay} memoryStorageMode={currentMode} />
        </div>
      )}

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

      {confirmLeaveVault && pendingMode && (
        <div className="mt-4 rounded-lg border border-amber-500/40 bg-amber-950/25 p-3 text-xs text-amber-100">
          <p className="font-semibold">Leave Founder Vault mode?</p>
          <p className="mt-2 text-amber-100/90">
            Your vault files stay on your machine in ~/FounderVault/. Switching modes changes where
            Copilot reads memory — we will not delete your local vault.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => pendingMode && saveMode(pendingMode, true)}
              className="rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-medium text-white"
            >
              Switch to {MEMORY_STORAGE_MODES.find((m) => m.key === pendingMode)?.label}
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmLeaveVault(false);
                setPendingMode(null);
              }}
              className="rounded-lg border border-zinc-600 px-3 py-1.5 text-xs text-zinc-400"
            >
              Keep Founder Vault
            </button>
          </div>
        </div>
      )}

      {confirmLocal && pendingMode === 'LOCAL_DEVICE' && (
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
              onClick={() => saveMode('LOCAL_DEVICE', true)}
              className="rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-medium text-white"
            >
              Yes, this device only
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmLocal(false);
                setPendingMode(null);
                saveMode('LOCAL_SYNC', true);
              }}
              className="rounded-lg border border-emerald-500/50 px-3 py-1.5 text-xs text-emerald-300"
            >
              Use Local + cloud sync instead
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmLocal(false);
                setPendingMode(null);
              }}
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
        <>
          <div className="mt-4 rounded-lg border border-zinc-700 bg-black/20 p-3 text-[10px] text-zinc-400">
            <p className="font-medium text-zinc-300">Vault files on your machine</p>
            <p className="mt-1">
              ~/FounderVault/project-context.md · roadmap.md · tasks.json · decisions.md ·
              private-notes.md
            </p>
            <p className="mt-1">Edit locally — Founder Node syncs encrypted snapshots every ~60s.</p>
          </div>
          <FounderNodePairingPanel accessToken={accessToken} active={currentMode === 'FOUNDER_NODE'} />
        </>
      )}

      <MobileVaultPanel accessToken={accessToken} />

      {msg && <p className="mt-3 text-xs text-emerald-300">{msg}</p>}
      {err && <p className="mt-3 text-xs text-red-300">{err}</p>}
    </div>
  );
}
