'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  TRADING_AGENT_AI_PROVIDERS,
  TRADING_AGENT_AI_PROVIDER_LABELS,
  type TradingAgentAiProvider,
} from '@dcf/utils';
import {
  fetchAdminFounderPromoSettings,
  fetchPlatformBrainStatus,
  removePlatformBrainKey,
  saveAdminFounderPromoCredentials,
  savePlatformBrainKey,
  saveShowcaseCredentials,
  updateShowcaseConfig,
  type AdminControlOverview,
  type FounderPromoPlatformSettings,
  type PlatformBrainStatus,
} from '@/lib/api';
import { AdminAiRoutingPanel } from './admin-ai-routing-panel';
import { AdminFounderBrainProvidersPanel } from './admin-founder-brain-providers-panel';

type Props = {
  token: string;
  overview: AdminControlOverview | null;
  onOverviewChange: (ov: AdminControlOverview) => void;
};

type KeyId = 'showcase' | 'brain' | 'gemini' | 'openai' | 'glm';

function StatusPill({ configured, lastUpdated }: { configured: boolean; lastUpdated?: string | null }) {
  if (configured) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-300">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
        Configured
        {lastUpdated ? (
          <span className="font-normal text-emerald-200/70">
            · updated {new Date(lastUpdated).toLocaleDateString()}
          </span>
        ) : null}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-800 px-2.5 py-0.5 text-[11px] font-semibold text-zinc-400">
      <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
      Not configured
    </span>
  );
}

function WhereUsedBox({ items }: { items: string[] }) {
  return (
    <details className="mt-3 group rounded-lg border border-zinc-800 bg-black/30 px-3 py-2">
      <summary className="cursor-pointer list-none text-[11px] font-medium text-zinc-400 hover:text-zinc-200">
        <span className="mr-1 inline-block transition group-open:rotate-90">▸</span>
        Where this key is used
      </summary>
      <ul className="mt-2 space-y-1.5 pl-4 text-[11px] leading-relaxed text-zinc-400">
        {items.map((line, i) => (
          <li key={i} className="relative pl-3">
            <span className="absolute left-0 top-1.5 h-1 w-1 rounded-full bg-violet-400/70" />
            {line}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function AdminAiKeysPanel({ token, overview, onOverviewChange }: Props) {
  const [brain, setBrain] = useState<PlatformBrainStatus | null>(null);
  const [promo, setPromo] = useState<FounderPromoPlatformSettings | null>(null);
  const [drafts, setDrafts] = useState<Partial<Record<KeyId, string>>>({});
  const [busy, setBusy] = useState<KeyId | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [b, p] = await Promise.all([
        fetchPlatformBrainStatus(token).catch(() => null),
        fetchAdminFounderPromoSettings(token).catch(() => null),
      ]);
      setBrain(b);
      setPromo(p);
    } catch {
      /* handled per-call */
    }
  }, [token]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const showcase = overview?.showcase;
  const showcaseAiProvider = (showcase?.aiProvider ?? 'deepseek') as TradingAgentAiProvider;

  function flash(kind: 'ok' | 'err', text: string) {
    if (kind === 'ok') {
      setMsg(text);
      setErr(null);
    } else {
      setErr(text);
      setMsg(null);
    }
  }

  async function saveShowcase(provider: TradingAgentAiProvider, apiKey: string) {
    setBusy('showcase');
    setErr(null);
    setMsg(null);
    try {
      const ov = await saveShowcaseCredentials(
        {
          aiProvider: provider,
          aiApiKey: apiKey.trim() || undefined,
        },
        token,
      );
      onOverviewChange(ov);
      setDrafts((d) => ({ ...d, showcase: '' }));
      flash('ok', `Showcase AI key saved (${TRADING_AGENT_AI_PROVIDER_LABELS[provider]}).`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  async function setShowcaseProvider(provider: TradingAgentAiProvider) {
    setBusy('showcase');
    setErr(null);
    setMsg(null);
    try {
      const ov = await updateShowcaseConfig({ aiProvider: provider }, token);
      onOverviewChange(ov);
      flash('ok', `Showcase AI provider set to ${TRADING_AGENT_AI_PROVIDER_LABELS[provider]}.`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  async function saveBrain() {
    const value = drafts.brain?.trim();
    if (!value) return;
    setBusy('brain');
    setErr(null);
    setMsg(null);
    try {
      const s = await savePlatformBrainKey(token, value);
      setBrain(s);
      setDrafts((d) => ({ ...d, brain: '' }));
      flash('ok', 'Platform Brain DeepSeek key saved.');
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  async function removeBrain() {
    setBusy('brain');
    setErr(null);
    setMsg(null);
    try {
      const s = await removePlatformBrainKey(token);
      setBrain(s);
      flash('ok', 'Platform Brain DeepSeek key removed.');
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Remove failed');
    } finally {
      setBusy(null);
    }
  }

  async function saveStoredKey(provider: 'glm' | 'gemini' | 'deepseek', keyId: KeyId, label: string) {
    const value = drafts[keyId]?.trim();
    if (!value) return;
    setBusy(keyId);
    setErr(null);
    setMsg(null);
    try {
      const s = await saveAdminFounderPromoCredentials(token, { [provider]: value });
      setPromo(s);
      setDrafts((d) => ({ ...d, [keyId]: '' }));
      flash('ok', `${label} saved.`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  async function clearStoredKey(provider: 'glm' | 'gemini' | 'deepseek', keyId: KeyId, label: string) {
    setBusy(keyId);
    setErr(null);
    setMsg(null);
    try {
      const s = await saveAdminFounderPromoCredentials(token, { [provider]: null });
      setPromo(s);
      flash('ok', `${label} cleared.`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Clear failed');
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="space-y-6">
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
        <h2 className="text-lg font-semibold text-white">AI Keys</h2>
        <p className="mt-1 text-sm text-zinc-400">
          Hardwire the three roles that matter. Secrets stay encrypted at rest and are never returned to the browser.
        </p>
        <ul className="mt-3 space-y-1.5 text-[12px] leading-relaxed text-zinc-400">
          <li>
            <span className="font-semibold text-zinc-200">Platform Brain</span> — community / in-app messaging
            (walls, share paraphrase, platform fallbacks). DeepSeek only. Not the IDE Second Brain.
          </li>
          <li>
            <span className="font-semibold text-zinc-200">Founder IDE</span> — Builder chat routes DeepSeek V4 Flash
            (fast) + V4 Pro (coding). Configured below.
          </li>
          <li>
            <span className="font-semibold text-zinc-200">Second Brain</span> — expert consult for the IDE. Cheap
            cascade: Gemini Flash → OpenAI gpt-4o-mini / Luna-class if keyed → optional GLM last resort.
            Never DeepSeek (Builder only).
          </li>
        </ul>
      </div>

      {msg && (
        <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-200">
          {msg}
        </p>
      )}
      {err && (
        <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
          {err}
        </p>
      )}

      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-zinc-200">Platform keys</h3>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/10 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-semibold text-white">Showcase AI key</p>
                <p className="mt-0.5 text-[11px] text-zinc-500">
                  Fly / Railway showcase bot only. Not used by Founder IDE.
                </p>
              </div>
              <StatusPill configured={Boolean(showcase?.aiConfigured)} lastUpdated={showcase?.credentialsUpdatedAt ?? null} />
            </div>
            <div className="mt-3">
              <p className="text-[11px] uppercase tracking-wider text-zinc-500">Active provider</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {TRADING_AGENT_AI_PROVIDERS.map((id) => (
                  <button
                    key={id}
                    type="button"
                    disabled={busy != null}
                    onClick={() => void setShowcaseProvider(id)}
                    className={`rounded-full px-3 py-1 text-[11px] transition ${
                      showcaseAiProvider === id
                        ? 'bg-violet-500/20 text-violet-100 ring-1 ring-violet-500/40'
                        : 'border border-zinc-700 text-zinc-400 hover:text-white'
                    }`}
                  >
                    {TRADING_AGENT_AI_PROVIDER_LABELS[id]}
                  </button>
                ))}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <input
                type="password"
                autoComplete="off"
                value={drafts.showcase ?? ''}
                onChange={(e) => setDrafts((d) => ({ ...d, showcase: e.target.value }))}
                placeholder={
                  showcase?.aiConfigured ? 'Leave blank to keep · paste new key to replace' : 'sk-…'
                }
                className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-white"
              />
              <button
                type="button"
                disabled={busy != null || !drafts.showcase?.trim()}
                onClick={() => void saveShowcase(showcaseAiProvider, drafts.showcase ?? '')}
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-500 disabled:opacity-40"
              >
                {busy === 'showcase' ? 'Saving…' : 'Save key'}
              </button>
            </div>
            <WhereUsedBox
              items={[
                'BTC Conservative Agent public showcase bot (Fly runtime).',
                'Pushed as the bot provider env key when you apply credentials from Agent Hub.',
              ]}
            />
          </div>

          <div className="rounded-xl border border-emerald-500/25 bg-emerald-950/10 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-semibold text-white">Platform Brain — DeepSeek</p>
                <p className="mt-0.5 text-[11px] text-zinc-500">
                  Platform / community / in-app messaging activities. Not IDE Second Brain.
                </p>
              </div>
              <StatusPill configured={Boolean(brain?.configured)} lastUpdated={brain?.updatedAt ?? null} />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <input
                type="password"
                autoComplete="off"
                value={drafts.brain ?? ''}
                onChange={(e) => setDrafts((d) => ({ ...d, brain: e.target.value }))}
                placeholder={brain?.configured ? 'Leave blank to keep · paste new key to replace' : 'sk-… (DeepSeek)'}
                className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-white"
              />
              <button
                type="button"
                disabled={busy != null || !drafts.brain?.trim()}
                onClick={() => void saveBrain()}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-40"
              >
                {busy === 'brain' ? 'Saving…' : 'Save key'}
              </button>
              {brain?.configured && (
                <button
                  type="button"
                  disabled={busy != null}
                  onClick={() => void removeBrain()}
                  className="rounded-lg border border-red-500/40 px-4 py-2 text-sm text-red-200 hover:bg-red-950/40 disabled:opacity-40"
                >
                  Remove
                </button>
              )}
            </div>
            <WhereUsedBox
              items={[
                'Project wall summarizer, X share paraphrase, and other platform messaging paths.',
                'Always-on DeepSeek fallback billed as platform_brain.',
              ]}
            />
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold text-zinc-200">Second Brain — cheap expert cascade</h3>
          <p className="mt-0.5 text-[11px] text-zinc-500">
            Primary: Gemini Flash. Cheap fallback:{' '}
            <code className="text-zinc-400">OPENAI_API_KEY</code> gpt-4o-mini / Luna-class if set in Railway.
            GLM is optional last-resort only — not the default. DeepSeek is never used for Second Brain.
          </p>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-sky-500/25 bg-sky-950/10 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-semibold text-white">Gemini Flash (primary)</p>
                <p className="mt-0.5 text-[11px] text-zinc-500">
                  Default Second Brain consult model. Also accepts <code>GEMINI_API_KEY</code> env.
                </p>
              </div>
              <StatusPill configured={Boolean(promo?.credentialsStatus?.gemini)} lastUpdated={promo?.credentialsUpdatedAt ?? null} />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <input
                type="password"
                autoComplete="off"
                value={drafts.gemini ?? ''}
                onChange={(e) => setDrafts((d) => ({ ...d, gemini: e.target.value }))}
                placeholder={promo?.credentialsStatus?.gemini ? 'Leave blank to keep · paste new key' : 'AIza…'}
                className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-white"
              />
              <button
                type="button"
                disabled={busy != null || !drafts.gemini?.trim()}
                onClick={() => void saveStoredKey('gemini', 'gemini', 'Gemini Flash key')}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-40"
              >
                {busy === 'gemini' ? 'Saving…' : 'Save key'}
              </button>
              {promo?.credentialsStatus?.gemini && (
                <button
                  type="button"
                  disabled={busy != null}
                  onClick={() => void clearStoredKey('gemini', 'gemini', 'Gemini Flash key')}
                  className="rounded-lg border border-red-500/40 px-4 py-2 text-sm text-red-200 hover:bg-red-950/40 disabled:opacity-40"
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          <div className="rounded-xl border border-zinc-700/60 bg-zinc-950/40 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-semibold text-white">GLM (optional last resort)</p>
                <p className="mt-0.5 text-[11px] text-zinc-500">
                  Expensive — only used if Gemini + cheap fallbacks fail and spend is explicitly allowed.
                </p>
              </div>
              <StatusPill configured={Boolean(promo?.credentialsStatus?.glm)} lastUpdated={promo?.credentialsUpdatedAt ?? null} />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <input
                type="password"
                autoComplete="off"
                value={drafts.glm ?? ''}
                onChange={(e) => setDrafts((d) => ({ ...d, glm: e.target.value }))}
                placeholder={promo?.credentialsStatus?.glm ? 'Leave blank to keep · paste new key' : 'xxx.xxx'}
                className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-white"
              />
              <button
                type="button"
                disabled={busy != null || !drafts.glm?.trim()}
                onClick={() => void saveStoredKey('glm', 'glm', 'GLM last-resort key')}
                className="rounded-lg border border-zinc-600 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-900 disabled:opacity-40"
              >
                {busy === 'glm' ? 'Saving…' : 'Save key'}
              </button>
              {promo?.credentialsStatus?.glm && (
                <button
                  type="button"
                  disabled={busy != null}
                  onClick={() => void clearStoredKey('glm', 'glm', 'GLM last-resort key')}
                  className="rounded-lg border border-red-500/40 px-4 py-2 text-sm text-red-200 hover:bg-red-950/40 disabled:opacity-40"
                >
                  Clear
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      <AdminFounderBrainProvidersPanel token={token} />

      <AdminAiRoutingPanel token={token} />
    </section>
  );
}
