'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchAiRoutingProviders,
  upsertAiRoutingProvider,
  type AiRoutingProviderRow,
} from '@/lib/api';

type Props = {
  token: string;
};

/** Keep only the providers useful for Platform Brain / Second Brain hardwires. */
const VISIBLE_PROVIDER_KEYS = new Set(['deepseek', 'gemini', 'openai', 'glm']);

/**
 * Slim provider-key status under AI Keys. Per-surface routing theater (copilot,
 * wall, quick build, add-provider) is hidden — platform traffic uses Platform
 * Brain; IDE uses Founder IDE + Second Brain.
 */
export function AdminAiRoutingPanel({ token }: Props) {
  const [providers, setProviders] = useState<AiRoutingProviderRow[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});

  const reload = useCallback(async () => {
    try {
      const p = await fetchAiRoutingProviders(token).catch(() => [] as AiRoutingProviderRow[]);
      setProviders(p.filter((row) => VISIBLE_PROVIDER_KEYS.has(row.key)));
    } catch {
      /* handled per-call */
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

  async function saveProviderKey(providerKey: string) {
    const value = keyDrafts[providerKey]?.trim();
    if (!value) return;
    setBusy(`provider-key:${providerKey}`);
    setErr(null);
    setMsg(null);
    try {
      await upsertAiRoutingProvider({ key: providerKey, apiKey: value }, token);
      setKeyDrafts((d) => ({ ...d, [providerKey]: '' }));
      await reload();
      flash('ok', `Key saved for ${providerKey}.`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Save key failed');
    } finally {
      setBusy(null);
    }
  }

  async function toggleProvider(providerKey: string, currentlyEnabled: boolean) {
    setBusy(`provider-toggle:${providerKey}`);
    setErr(null);
    setMsg(null);
    try {
      await upsertAiRoutingProvider({ key: providerKey, enabled: !currentlyEnabled }, token);
      await reload();
      flash('ok', `${providerKey} ${!currentlyEnabled ? 'enabled' : 'disabled'}.`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Toggle failed');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rounded-xl border border-sky-500/30 bg-sky-950/5 p-4">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-3 text-left"
      >
        <div>
          <p className="font-semibold text-white">Provider registry (advanced)</p>
          <p className="mt-0.5 text-[11px] text-zinc-400">
            Optional extra keys for DeepSeek / Gemini / OpenAI / GLM. Platform messaging uses Platform Brain;
            IDE chat uses Founder IDE + Second Brain — no per-surface routing UI.
          </p>
        </div>
        <span className="text-zinc-400 text-sm">{expanded ? '▾' : '▸'}</span>
      </button>

      {expanded && (
        <div className="mt-4 space-y-4">
          {msg && (
            <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-[11px] text-emerald-200">
              {msg}
            </p>
          )}
          {err && (
            <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-3 py-2 text-[11px] text-red-300">
              {err}
            </p>
          )}

          <div className="space-y-2">
            {providers.map((p) => (
              <div key={p.key} className="rounded-lg border border-zinc-800 bg-black/30 px-3 py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm text-zinc-200">
                      {p.label}{' '}
                      <span className="font-mono text-[10px] text-zinc-500">({p.key})</span>
                    </p>
                    <p className="text-[10px] text-zinc-500">
                      {p.defaultModel} · {p.adapter}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                        p.hasKey ? 'bg-emerald-500/15 text-emerald-300' : 'bg-zinc-800 text-zinc-400'
                      }`}
                    >
                      {p.hasKey ? '✓ key' : '✗ no key'}
                    </span>
                    <button
                      type="button"
                      disabled={busy != null}
                      onClick={() => void toggleProvider(p.key, p.enabled)}
                      className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold ${
                        p.enabled
                          ? 'bg-sky-500/20 text-sky-200 ring-1 ring-sky-500/40'
                          : 'border border-zinc-700 text-zinc-400 hover:text-white'
                      }`}
                    >
                      {p.enabled ? 'Enabled' : 'Disabled'}
                    </button>
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <input
                    type="password"
                    autoComplete="off"
                    value={keyDrafts[p.key] ?? ''}
                    onChange={(e) => setKeyDrafts((d) => ({ ...d, [p.key]: e.target.value }))}
                    placeholder={p.hasKey ? 'Leave blank to keep · paste new key to replace' : 'Paste API key'}
                    className="min-w-0 flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-white"
                  />
                  <button
                    type="button"
                    disabled={busy != null || !keyDrafts[p.key]?.trim()}
                    onClick={() => void saveProviderKey(p.key)}
                    className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-40"
                  >
                    {busy === `provider-key:${p.key}` ? 'Saving…' : 'Save key'}
                  </button>
                </div>
              </div>
            ))}
            {providers.length === 0 && (
              <p className="text-xs text-zinc-500">No providers seeded yet — Railway env keys still work.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
