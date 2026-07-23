'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  FOUNDER_BRAIN_MODE_LABELS,
  FOUNDER_BRAIN_MODES,
  type FounderBrainMode,
} from '@dcf/utils';
import {
  fetchFounderBrainProviders,
  testFounderBrainProviders,
  updateFounderBrainProviders,
  type FounderBrainProviderTestResult,
  type FounderBrainProvidersSettings,
} from '@/lib/api';

type Props = {
  token: string;
};

function KeyStatusRow({
  label,
  status,
}: {
  label: string;
  status: FounderBrainProvidersSettings['keys']['deepseek'];
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 bg-zinc-950/80 px-3 py-2 text-xs">
      <span className="font-medium text-zinc-300">{label}</span>
      {status.configured ? (
        <span className="inline-flex items-center gap-1.5 text-emerald-300">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          Configured
          {status.source ? <span className="text-emerald-200/70">via {status.source}</span> : null}
          {status.last4 ? <span className="font-mono text-zinc-500">···{status.last4}</span> : null}
        </span>
      ) : (
        <span className="text-amber-300">Missing — set Railway env or Admin → AI Keys</span>
      )}
    </div>
  );
}

/** Admin panel for the managed DeepSeek-only Founder Brain boundary. */
export function AdminFounderBrainProvidersPanel({ token }: Props) {
  const [settings, setSettings] = useState<FounderBrainProvidersSettings | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<FounderBrainProviderTestResult[] | null>(null);

  const reload = useCallback(async () => {
    try {
      const row = await fetchFounderBrainProviders(token);
      setSettings(row);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load Founder Brain settings');
    }
  }, [token]);

  useEffect(() => {
    void reload();
  }, [reload]);

  function flash(kind: 'ok' | 'err', text: string) {
    if (kind === 'ok') {
      setMsg(text);
      setErr(null);
    } else {
      setErr(text);
      setMsg(null);
    }
  }

  async function save(patch: Parameters<typeof updateFounderBrainProviders>[1]) {
    setBusy('save');
    try {
      const next = await updateFounderBrainProviders(token, {
        ...patch,
        twoModelRoutingEnabled: false,
      });
      setSettings(next);
      flash('ok', 'Founder Brain routing saved.');
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  async function runTests() {
    setBusy('test');
    setTestResults(null);
    try {
      const results = await testFounderBrainProviders(token, 'deepseek');
      setTestResults(results.filter((result) => result.provider === 'deepseek'));
      flash('ok', 'Tested managed DeepSeek');
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Test failed');
    } finally {
      setBusy(null);
    }
  }

  if (!settings) {
    return (
      <div className="rounded-xl border border-zinc-800 p-5 text-sm text-zinc-500">
        Loading Founder Brain providers…
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-cyan-500/25 bg-cyan-950/10 p-5 space-y-4">
      <div>
        <h3 className="text-lg font-semibold text-white">Founder Brain Providers</h3>
        <p className="mt-1 text-sm text-zinc-400">
          Managed V1 routing uses DeepSeek V4 Flash for everyday work and DeepSeek V4 Pro for
          explicit code or reasoning tasks. Other providers belong to personal BYOK profiles.
        </p>
      </div>

      {msg && <p className="text-sm text-emerald-300">{msg}</p>}
      {err && <p className="text-sm text-red-300">{err}</p>}

      <div className="grid gap-2">
        <KeyStatusRow label="DeepSeek" status={settings.keys.deepseek} />
      </div>

      {settings.twoModelRoutingEnabled ? (
        <button
          type="button"
          disabled={busy != null}
          onClick={() => void save({ twoModelRoutingEnabled: false })}
          className="w-fit rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-semibold text-amber-100"
        >
          Disable legacy multi-provider routing
        </button>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <p className="text-[11px] uppercase tracking-wider text-zinc-500">DeepSeek Fast Model</p>
          <input
            value={settings.deepseekFastModel}
            disabled={busy != null}
            onChange={(e) => setSettings({ ...settings, deepseekFastModel: e.target.value })}
            onBlur={() => void save({ deepseekFastModel: settings.deepseekFastModel })}
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-white"
          />
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wider text-zinc-500">DeepSeek Coding Model</p>
          <input
            value={settings.deepseekCodingModel}
            disabled={busy != null}
            onChange={(e) => setSettings({ ...settings, deepseekCodingModel: e.target.value })}
            onBlur={() => void save({ deepseekCodingModel: settings.deepseekCodingModel })}
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-white"
          />
        </div>
      </div>

      <div>
        <p className="text-[11px] uppercase tracking-wider text-zinc-500">Default Founder Brain mode</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {FOUNDER_BRAIN_MODES.map((mode) => (
            <button
              key={mode}
              type="button"
              disabled={busy != null}
              onClick={() => void save({ defaultMode: mode as FounderBrainMode })}
              className={`rounded-full px-3 py-1 text-[11px] font-semibold ${
                settings.defaultMode === mode
                  ? 'bg-cyan-500/20 text-cyan-100 ring-1 ring-cyan-500/40'
                  : 'border border-zinc-700 text-zinc-400 hover:text-white'
              }`}
            >
              {FOUNDER_BRAIN_MODE_LABELS[mode]}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy != null}
          onClick={() => void runTests()}
          className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-600 disabled:opacity-40"
        >
          Test managed DeepSeek
        </button>
      </div>

      {testResults && (
        <ul className="space-y-2 text-xs">
          {testResults.map((r) => (
            <li
              key={r.provider}
              className={`rounded-lg border px-3 py-2 ${
                r.ok ? 'border-emerald-500/30 text-emerald-200' : 'border-red-500/30 text-red-200'
              }`}
            >
              {r.provider}: {r.message} ({r.latencyMs}ms)
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
