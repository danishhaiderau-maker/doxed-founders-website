'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchAiRoutingProviders,
  fetchAiRoutingSections,
  removeAiRoutingProvider,
  setAiSectionProvider,
  upsertAiRoutingProvider,
  type AiRoutingProviderRow,
  type AiSectionRoutingRow,
} from '@/lib/api';

type Props = {
  token: string;
};

type NewProviderDraft = {
  key: string;
  label: string;
  baseUrl: string;
  defaultModel: string;
  adapter: string;
  apiKey: string;
};

const ADAPTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'openai_compat', label: 'OpenAI-compatible (DeepSeek / GLM / OpenAI / Gemini-compat / Xiaomi / Ollama)' },
  { value: 'anthropic', label: 'Anthropic (Claude native messages API)' },
];

const EMPTY_DRAFT: NewProviderDraft = {
  key: '',
  label: '',
  baseUrl: '',
  defaultModel: '',
  adapter: 'openai_compat',
  apiKey: '',
};

/**
 * Collapsible "AI Routing" panel rendered inside the AI Keys admin tab.
 * Lets the owner route any AI section on the platform to any registered
 * provider at runtime — no code change. Adding a new AI is: fill the
 * "Add new provider" form → it appears as a toggle option for every section.
 */
export function AdminAiRoutingPanel({ token }: Props) {
  const [providers, setProviders] = useState<AiRoutingProviderRow[]>([]);
  const [sections, setSections] = useState<AiSectionRoutingRow[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<NewProviderDraft>(EMPTY_DRAFT);

  const reload = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([
        fetchAiRoutingProviders(token).catch(() => [] as AiRoutingProviderRow[]),
        fetchAiRoutingSections(token).catch(() => [] as AiSectionRoutingRow[]),
      ]);
      setProviders(p);
      setSections(s);
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

  async function pickProvider(section: string, providerKey: string) {
    setBusy(`section:${section}`);
    setErr(null);
    setMsg(null);
    try {
      await setAiSectionProvider(section, providerKey, token);
      await reload();
      flash('ok', `Routed ${section} → ${providerKey}.`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Routing update failed');
    } finally {
      setBusy(null);
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

  async function removeProvider(providerKey: string) {
    if (!confirm(`Remove provider "${providerKey}"? Sections using it fall back to deepseek.`)) return;
    setBusy(`provider-remove:${providerKey}`);
    setErr(null);
    setMsg(null);
    try {
      await removeAiRoutingProvider(providerKey, token);
      await reload();
      flash('ok', `Provider ${providerKey} removed.`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Remove failed');
    } finally {
      setBusy(null);
    }
  }

  async function createProvider() {
    if (!draft.key.trim() || !draft.label.trim() || !draft.baseUrl.trim() || !draft.defaultModel.trim()) {
      flash('err', 'Key, label, baseUrl and defaultModel are all required.');
      return;
    }
    setBusy('create-provider');
    setErr(null);
    setMsg(null);
    try {
      await upsertAiRoutingProvider(
        {
          key: draft.key.trim().toLowerCase(),
          label: draft.label.trim(),
          baseUrl: draft.baseUrl.trim(),
          defaultModel: draft.defaultModel.trim(),
          adapter: draft.adapter,
          apiKey: draft.apiKey.trim() || null,
          enabled: Boolean(draft.apiKey.trim()),
        },
        token,
      );
      setDraft(EMPTY_DRAFT);
      await reload();
      flash('ok', 'New provider added — now assignable to any section.');
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Add provider failed');
    } finally {
      setBusy(null);
    }
  }

  const enabledProviders = providers.filter((p) => p.enabled && p.hasKey);

  return (
    <div className="rounded-xl border border-sky-500/30 bg-sky-950/5 p-4">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-3 text-left"
      >
        <div>
          <p className="font-semibold text-white">AI Routing</p>
          <p className="mt-0.5 text-[11px] text-zinc-400">
            Route any AI section on the platform to any provider at runtime.{' '}
            {enabledProviders.length} provider{enabledProviders.length === 1 ? '' : 's'} live ·{' '}
            {sections.length} section{sections.length === 1 ? '' : 's'} routable.
          </p>
        </div>
        <span className="text-zinc-400 text-sm">{expanded ? '▾' : '▸'}</span>
      </button>

      {expanded && (
        <div className="mt-4 space-y-5">
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

          {/* Section → provider routing table */}
          <div>
            <p className="text-[11px] uppercase tracking-wider text-zinc-500">Sections</p>
            <div className="mt-2 space-y-1.5">
              {sections.map((s) => {
                const ready = s.providerEnabled && s.providerHasKey;
                return (
                  <div
                    key={s.section}
                    className="flex flex-wrap items-center gap-2 rounded-lg border border-zinc-800 bg-black/30 px-3 py-2"
                  >
                    <div className="min-w-[180px] flex-1">
                      <p className="text-sm text-zinc-200">{s.label}</p>
                      <p className="font-mono text-[10px] text-zinc-500">{s.section}</p>
                    </div>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                        ready
                          ? 'bg-emerald-500/15 text-emerald-300'
                          : 'bg-amber-500/15 text-amber-300'
                      }`}
                    >
                      {ready ? '✓ ready' : '✗ no key'}
                    </span>
                    <select
                      value={s.providerKey}
                      disabled={busy != null}
                      onChange={(e) => void pickProvider(s.section, e.target.value)}
                      className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-white"
                    >
                      {providers.map((p) => (
                        <option key={p.key} value={p.key}>
                          {p.label}
                          {p.enabled && p.hasKey ? ' ✓' : ' (no key)'}
                        </option>
                      ))}
                    </select>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Provider registry */}
          <div>
            <p className="text-[11px] uppercase tracking-wider text-zinc-500">Providers</p>
            <div className="mt-2 space-y-2">
              {providers.map((p) => (
                <div
                  key={p.key}
                  className="rounded-lg border border-zinc-800 bg-black/30 px-3 py-2.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <p className="text-sm text-zinc-200">
                        {p.label}{' '}
                        <span className="font-mono text-[10px] text-zinc-500">({p.key})</span>
                      </p>
                      <p className="text-[10px] text-zinc-500">
                        {p.baseUrl} · {p.defaultModel} · {p.adapter}
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
                      <button
                        type="button"
                        disabled={busy != null}
                        onClick={() => void removeProvider(p.key)}
                        className="rounded-md border border-red-500/40 px-2 py-0.5 text-[10px] text-red-200 hover:bg-red-950/40"
                      >
                        Remove
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
            </div>
          </div>

          {/* Add new provider */}
          <div className="rounded-lg border border-dashed border-zinc-700 bg-black/20 p-3">
            <p className="text-[11px] uppercase tracking-wider text-zinc-500">Add new provider</p>
            <p className="mt-0.5 text-[10px] text-zinc-500">
              Fill in name + key + baseUrl + model → it shows up as a toggle option for every section above.
            </p>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <input
                placeholder="key slug (e.g. claude / xiaomi / mistral)"
                value={draft.key}
                onChange={(e) => setDraft((d) => ({ ...d, key: e.target.value }))}
                className="rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-white"
              />
              <input
                placeholder="Display label (e.g. Mistral Large)"
                value={draft.label}
                onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))}
                className="rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-xs text-white"
              />
              <input
                placeholder="baseUrl (e.g. https://api.mistral.ai/v1)"
                value={draft.baseUrl}
                onChange={(e) => setDraft((d) => ({ ...d, baseUrl: e.target.value }))}
                className="rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-white"
              />
              <input
                placeholder="defaultModel (e.g. mistral-large-latest)"
                value={draft.defaultModel}
                onChange={(e) => setDraft((d) => ({ ...d, defaultModel: e.target.value }))}
                className="rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-white"
              />
              <select
                value={draft.adapter}
                onChange={(e) => setDraft((d) => ({ ...d, adapter: e.target.value }))}
                className="rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-xs text-white"
              >
                {ADAPTER_OPTIONS.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
              <input
                type="password"
                autoComplete="off"
                placeholder="API key (optional at create — add later)"
                value={draft.apiKey}
                onChange={(e) => setDraft((d) => ({ ...d, apiKey: e.target.value }))}
                className="rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-white"
              />
            </div>
            <button
              type="button"
              disabled={busy != null}
              onClick={() => void createProvider()}
              className="mt-2 rounded-md bg-sky-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-40"
            >
              {busy === 'create-provider' ? 'Adding…' : 'Add provider'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
