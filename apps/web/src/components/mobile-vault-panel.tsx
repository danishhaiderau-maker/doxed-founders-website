'use client';

import { useEffect, useState } from 'react';
import { readMobileAppMode } from '@/lib/mobile-app-mode';
import { isCapacitorNative } from '@/lib/mobile-vault/capacitor';
import {
  getMobileVaultStatus,
  pairMobileVaultWithCode,
  pullVaultFromRelayNode,
  subscribeMobileVaultStatus,
  unpairMobileVault,
} from '@/lib/mobile-vault/service';
import {
  createFounderNodePairingCode,
  fetchFounderNodeStatus,
  fetchFounderNodeVaultRelays,
  type FounderNodeStatusRow,
  type FounderNodeVaultRelayRow,
} from '@/lib/api';

type Props = {
  accessToken: string;
};

export function MobileVaultPanel({ accessToken }: Props) {
  const [vaultStatus, setVaultStatus] = useState(getMobileVaultStatus());
  const [nodes, setNodes] = useState<FounderNodeStatusRow[]>([]);
  const [relays, setRelays] = useState<FounderNodeVaultRelayRow[]>([]);
  const [code, setCode] = useState('');
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const inApk = readMobileAppMode() && isCapacitorNative();

  useEffect(() => subscribeMobileVaultStatus(setVaultStatus), []);

  useEffect(() => {
    if (!inApk) return;
    void (async () => {
      try {
        const [st, rel] = await Promise.all([
          fetchFounderNodeStatus(accessToken),
          fetchFounderNodeVaultRelays(accessToken),
        ]);
        setNodes(st.nodes);
        setRelays(rel.relays);
      } catch {
        /* optional */
      }
    })();
  }, [accessToken, inApk, vaultStatus.lastSyncAt]);

  if (!readMobileAppMode()) {
    return (
      <div className="mt-4 rounded-lg border border-violet-500/25 bg-violet-950/15 p-4">
        <h4 className="text-sm font-semibold text-violet-100">Android vault (install APK)</h4>
        <p className="mt-1 text-xs text-violet-100/75">
          On-device vault sync runs inside the Android app, not in the browser. On your computer, open{' '}
          <a href="/settings/builder" className="text-violet-200 underline">
            Founder Node settings
          </a>{' '}
          → Step 2 → <strong className="text-white">Code for Android</strong>. On your phone, install from{' '}
          <a href="/mobile" className="text-violet-200 underline">
            /mobile
          </a>{' '}
          and paste the code under Android vault.
        </p>
      </div>
    );
  }

  async function generateMobileCode() {
    setBusy(true);
    setErr(null);
    try {
      const res = await createFounderNodePairingCode(accessToken, 'mobile');
      setPairingCode(res.code);
      setMsg('Enter this code below to pair vault on this phone.');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not create code');
    } finally {
      setBusy(false);
    }
  }

  async function handlePair() {
    if (!code.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await pairMobileVaultWithCode(code.trim());
      setCode('');
      setPairingCode(null);
      setMsg('Mobile vault paired. Sync runs every ~45s while the app is open.');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Pairing failed');
    } finally {
      setBusy(false);
    }
  }

  async function handleImport(sourceNodeId: string) {
    setBusy(true);
    setErr(null);
    try {
      await pullVaultFromRelayNode(sourceNodeId, accessToken);
      setMsg('Vault files restored from encrypted relay.');
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Import failed';
      if (/decrypt|operation/i.test(message)) {
        setErr(
          'Encrypted backup is from another device key. Goals and tasks still merge automatically when both devices sync — use Restore for this phone’s relay only.',
        );
      } else {
        setErr(message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 rounded-lg border border-violet-500/35 bg-violet-950/20 p-4">
      <h4 className="text-sm font-semibold text-violet-100">Android vault (Phase 4 merge)</h4>
      <p className="mt-1 text-xs text-violet-100/75">
        Private <code className="text-violet-200">FounderVault/</code> on this phone syncs with desktop via encrypted
        relay plus plaintext merge patches (goals, tasks, roadmap). When your PC wakes, Founder Node applies phone
        edits automatically.
      </p>

      {!inApk && (
        <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-950/20 px-3 py-2 text-xs text-amber-100">
          Install the Android APK to enable on-device vault. Browser mode uses cloud Founder OS only.
        </p>
      )}

      {inApk && (
        <>
          {vaultStatus.paired ? (
            <div className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-100">
              ✓ <strong>{vaultStatus.label}</strong> ({vaultStatus.nodeId?.slice(0, 12)}…)
              {vaultStatus.lastSyncAt && (
                <span className="block text-emerald-200/80">
                  Last sync {new Date(vaultStatus.lastSyncAt).toLocaleString()}
                </span>
              )}
              {vaultStatus.syncing && <span className="block animate-pulse">Syncing…</span>}
              {vaultStatus.lastError && (
                <span className="block text-red-300">{vaultStatus.lastError}</span>
              )}
            </div>
          ) : (
            <div className="mt-3 space-y-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void generateMobileCode()}
                className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
              >
                Generate mobile pairing code
              </button>
              {pairingCode && (
                <p className="font-mono text-lg font-bold tracking-widest text-white">{pairingCode}</p>
              )}
              <div className="flex gap-2">
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value.toUpperCase())}
                  placeholder="Paste code from website"
                  className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-black px-2 py-1.5 text-sm text-white"
                />
                <button
                  type="button"
                  disabled={busy || !code.trim()}
                  onClick={() => void handlePair()}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
                >
                  Pair phone
                </button>
              </div>
            </div>
          )}

          {vaultStatus.paired && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void unpairMobileVault()}
              className="mt-3 text-xs text-zinc-500 underline hover:text-zinc-300"
            >
              Unpair mobile vault
            </button>
          )}

          {relays.length > 0 && vaultStatus.paired && (
            <div className="mt-4 border-t border-violet-500/20 pt-3">
              <p className="text-[11px] font-semibold text-violet-200">Encrypted backups on relay</p>
              <ul className="mt-2 space-y-2">
                {relays.map((r) => {
                  const node = nodes.find((n) => n.nodeId === r.nodeId);
                  const isSelf = r.nodeId === vaultStatus.nodeId;
                  return (
                    <li
                      key={r.nodeId}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 bg-zinc-950/50 px-2 py-1.5 text-[11px]"
                    >
                      <span className="text-zinc-300">
                        {r.label ?? node?.label ?? r.nodeId}
                        {r.platform ? ` · ${r.platform}` : ''}
                        <span className="block text-zinc-600">
                          {Math.round(r.blobBytes / 1024)} KB
                          {r.vaultSyncVersion != null ? ` · v${r.vaultSyncVersion}` : ''}
                          {r.hasMergePatch ? ' · merge' : ''} · {new Date(r.updatedAt).toLocaleString()}
                        </span>
                      </span>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void handleImport(r.nodeId)}
                        className="rounded bg-violet-700 px-2 py-0.5 text-white disabled:opacity-50"
                      >
                        {isSelf ? 'Restore' : 'Try import'}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </>
      )}

      {msg && <p className="mt-2 text-xs text-emerald-200">{msg}</p>}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </div>
  );
}
