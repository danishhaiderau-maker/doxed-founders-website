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

type Props = {
  token: string;
  overview: AdminControlOverview | null;
  onOverviewChange: (ov: AdminControlOverview) => void;
};

type KeyId = 'showcase' | 'brain' | 'glm' | 'gemini' | 'deepseek';

type ProviderGroup = {
  id: 'platform' | 'promo';
  label: string;
  blurb: string;
  keys: KeyId[];
};

const GROUPS: ProviderGroup[] = [
  {
    id: 'platform',
    label: 'Platform keys',
    blurb: 'Single shared keys that power the showcase bot and the always-on Copilot fallback.',
    keys: ['showcase', 'brain'],
  },
  {
    id: 'promo',
    label: 'Founder promo pool',
    blurb:
      'Keys the platform lends to eligible founders during their free 1-month AI window. Billed as platform_promo. Toggle / cap / window live in the Platform & Treasury tab.',
    keys: ['glm', 'gemini', 'deepseek'],
  },
];

const KEY_META: Record<
  KeyId,
  { label: string; placeholder: string; whereUsed: string[] }
> = {
  showcase: {
    label: 'Showcase AI key',
    placeholder: 'sk-… (DeepSeek / OpenAI / Claude / Gemini / OpenRouter)',
    whereUsed: [
      'BTC Conservative Agent public showcase bot (services/btc-conservative-agent) — live proof-of-skill dashboard.',
      'Pushed to the Railway bot runtime as DEEPSEEK_API_KEY (or GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY) when you click "Apply to Railway runtime" in the Agent Control tab.',
      'Admin-owned only — never reused by user agent instances.',
    ],
  },
  brain: {
    label: 'Platform Brain — DeepSeek fallback',
    placeholder: 'sk-… (DeepSeek)',
    whereUsed: [
      'BuilderService.tryPlatformDeepseekFallback — serves Founder Copilot chat when a user has no BYOK key and the promo path is unavailable.',
      'BuilderService.tryPlatformDeepseekFallbackStream — same fallback for streaming Copilot responses.',
      'Token usage logged as billingSource = "platform_brain".',
    ],
  },
  glm: {
    label: 'GLM 5.2 (ZhipuAI) — promo',
    placeholder: 'xxx.xxx',
    whereUsed: [
      'WallService.runSummarizerLlm — the Chat Summarizer agent that summarizes + sentiment-analyzes each project wall (getDecryptedPlatformGlmKey).',
      'BuilderService copilot_forced_promo path + Quick Build — GLM is the default promo provider for eligible founders (resolvePromoApiKey).',
      'Streaming Copilot promo path (completionWithProviderStream for glm).',
      'Only served to eligible users inside their 1-month promo window; billed as platform_promo.',
    ],
  },
  gemini: {
    label: 'Google Gemini — promo',
    placeholder: 'AIza…',
    whereUsed: [
      'BuilderService copilot_forced_promo fallback + Quick Build — Gemini is tried after GLM/DeepSeek in the promo provider order (resolvePromoApiKey for gemini).',
      'Streaming Copilot promo path (completionWithProviderStream for gemini).',
      'Only served to eligible users inside their 1-month promo window; billed as platform_promo.',
    ],
  },
  deepseek: {
    label: 'DeepSeek — promo',
    placeholder: 'sk-…',
    whereUsed: [
      'BuilderService copilot_forced_promo fallback + Quick Build — DeepSeek is tried in the promo provider order (resolvePromoApiKey for deepseek).',
      'Streaming Copilot promo path (completionWithProviderStream for deepseek).',
      'Distinct from the Platform Brain key: this one is only spent on eligible promo users (platform_promo), not the always-on fallback (platform_brain).',
    ],
  },
};

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

  async function savePromoKey(provider: 'glm' | 'gemini' | 'deepseek') {
    const value = drafts[provider]?.trim();
    if (!value) return;
    setBusy(provider);
    setErr(null);
    setMsg(null);
    try {
      const s = await saveAdminFounderPromoCredentials(token, { [provider]: value });
      setPromo(s);
      setDrafts((d) => ({ ...d, [provider]: '' }));
      flash('ok', `${KEY_META[provider].label} saved.`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  async function clearPromoKey(provider: 'glm' | 'gemini' | 'deepseek') {
    setBusy(provider);
    setErr(null);
    setMsg(null);
    try {
      const s = await saveAdminFounderPromoCredentials(token, { [provider]: null });
      setPromo(s);
      flash('ok', `${KEY_META[provider].label} cleared.`);
    } catch (e) {
      flash('err', e instanceof Error ? e.message : 'Clear failed');
    } finally {
      setBusy(null);
    }
  }

  function renderShowcaseCard() {
    const meta = KEY_META.showcase;
    const configured = Boolean(showcase?.aiConfigured);
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-950/10 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="font-semibold text-white">{meta.label}</p>
            <p className="mt-0.5 text-[11px] text-zinc-500">
              Provider-selectable. Live bot reads <code>DEEPSEEK_API_KEY</code> by default.
            </p>
          </div>
          <StatusPill configured={configured} lastUpdated={showcase?.credentialsUpdatedAt ?? null} />
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
            placeholder={configured ? 'Leave blank to keep · paste new key to replace' : meta.placeholder}
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

        {showcase?.aiRuntimeNote && (
          <p className="mt-2 text-[11px] text-amber-200/80">{showcase.aiRuntimeNote}</p>
        )}

        <WhereUsedBox items={meta.whereUsed} />
      </div>
    );
  }

  function renderBrainCard() {
    const meta = KEY_META.brain;
    const configured = Boolean(brain?.configured);
    return (
      <div className="rounded-xl border border-emerald-500/25 bg-emerald-950/10 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="font-semibold text-white">{meta.label}</p>
            <p className="mt-0.5 text-[11px] text-zinc-500">
              Always-on DeepSeek fallback so Copilot chat never goes dark.
            </p>
          </div>
          <StatusPill configured={configured} lastUpdated={brain?.updatedAt ?? null} />
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <input
            type="password"
            autoComplete="off"
            value={drafts.brain ?? ''}
            onChange={(e) => setDrafts((d) => ({ ...d, brain: e.target.value }))}
            placeholder={configured ? 'Leave blank to keep · paste new key to replace' : meta.placeholder}
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
          {configured && (
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

        <WhereUsedBox items={meta.whereUsed} />
      </div>
    );
  }

  function renderPromoCard(provider: 'glm' | 'gemini' | 'deepseek') {
    const meta = KEY_META[provider];
    const configured = Boolean(promo?.credentialsStatus?.[provider]);
    return (
      <div className="rounded-xl border border-violet-500/25 bg-violet-950/10 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="font-semibold text-white">{meta.label}</p>
            <p className="mt-0.5 text-[11px] text-zinc-500">
              Lent to eligible founders during their free window. Billed as <code>platform_promo</code>.
            </p>
          </div>
          <StatusPill configured={configured} lastUpdated={promo?.credentialsUpdatedAt ?? null} />
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <input
            type="password"
            autoComplete="off"
            value={drafts[provider] ?? ''}
            onChange={(e) => setDrafts((d) => ({ ...d, [provider]: e.target.value }))}
            placeholder={configured ? 'Leave blank to keep · paste new key to replace' : meta.placeholder}
            className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-white"
          />
          <button
            type="button"
            disabled={busy != null || !drafts[provider]?.trim()}
            onClick={() => void savePromoKey(provider)}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500 disabled:opacity-40"
          >
            {busy === provider ? 'Saving…' : 'Save key'}
          </button>
          {configured && (
            <button
              type="button"
              disabled={busy != null}
              onClick={() => void clearPromoKey(provider)}
              className="rounded-lg border border-red-500/40 px-4 py-2 text-sm text-red-200 hover:bg-red-950/40 disabled:opacity-40"
            >
              Clear
            </button>
          )}
        </div>

        <WhereUsedBox items={meta.whereUsed} />
      </div>
    );
  }

  return (
    <section className="space-y-6">
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
        <h2 className="text-lg font-semibold text-white">AI Keys</h2>
        <p className="mt-1 text-sm text-zinc-400">
          Every platform AI / LLM key in one place. Each card shows whether the key is set and a
          collapsible box explaining exactly which services consume it. Secrets are encrypted at
          rest and never sent back to the browser.
        </p>
        <p className="mt-2 text-[11px] text-zinc-500">
          User-owned (BYOK) keys are connected per-account in{' '}
          <span className="text-zinc-300">Account → Connected accounts</span> and are not managed
          here.
        </p>
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

      {GROUPS.map((group) => (
        <div key={group.id} className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-sm font-semibold text-zinc-200">{group.label}</h3>
            <p className="max-w-2xl text-[11px] text-zinc-500">{group.blurb}</p>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {group.keys.map((keyId) => {
              if (keyId === 'showcase') return <div key={keyId}>{renderShowcaseCard()}</div>;
              if (keyId === 'brain') return <div key={keyId}>{renderBrainCard()}</div>;
              return <div key={keyId}>{renderPromoCard(keyId)}</div>;
            })}
          </div>
        </div>
      ))}

      <AdminAiRoutingPanel token={token} />
    </section>
  );
}
