'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  connectAiProvider,
  connectCursorCloud,
  fetchBuilderSettings,
  type BuilderSettings,
  type FounderOnboardingStatus,
} from '@/lib/api';

type Props = {
  accessToken: string;
  llmConnected: boolean;
  builderConnected: boolean;
  promo?: FounderOnboardingStatus['promo'];
  onConnected: () => void;
  onMessage?: (msg: string) => void;
};

const QUICK_LLM = [
  {
    provider: 'deepseek',
    label: 'DeepSeek',
    hint: 'Recommended — low cost, strong for planning',
    keyUrl: 'https://platform.deepseek.com/api_keys',
  },
  {
    provider: 'openai',
    label: 'OpenAI',
    hint: 'GPT-4o mini for fast chat',
    keyUrl: 'https://platform.openai.com/api-keys',
  },
] as const;

export function FounderOnboardingAiStack({
  accessToken,
  llmConnected,
  builderConnected,
  promo,
  onConnected,
  onMessage,
}: Props) {
  const [settings, setSettings] = useState<BuilderSettings | null>(null);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [cursorKey, setCursorKey] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSettings(await fetchBuilderSettings(accessToken));
    } catch {
      setSettings(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function connectLlm(provider: string) {
    const apiKey = keys[provider]?.trim();
    if (!apiKey) {
      setErr('Paste your API key first');
      return;
    }
    setBusy(provider);
    setErr(null);
    try {
      const result = await connectAiProvider(provider, apiKey, accessToken);
      setKeys((k) => ({ ...k, [provider]: '' }));
      onMessage?.(`${result.accountName} connected — Founder Brain is live`);
      await load();
      onConnected();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Connect failed');
    } finally {
      setBusy(null);
    }
  }

  async function connectCursor() {
    if (!cursorKey.trim()) {
      setErr('Paste your Cursor API key');
      return;
    }
    setBusy('cursor');
    setErr(null);
    try {
      const result = await connectCursorCloud(cursorKey.trim(), accessToken);
      setCursorKey('');
      onMessage?.(
        `${result.accountName} connected — remote builds from phone or browser`,
      );
      await load();
      onConnected();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Cursor connect failed');
    } finally {
      setBusy(null);
    }
  }

  const defaultProvider = settings?.defaultProvider ?? 'RULE_BASED';
  const promoActive = promo?.eligible && (promo.hasLlm || promo.hasCursor);

  return (
    <div className="space-y-5">
      {promoActive && promo?.message && (
        <div className="rounded-lg border border-amber-500/35 bg-amber-950/25 p-3 text-xs text-amber-100">
          <strong className="text-amber-300">Founder AI promo active.</strong> {promo.message}
          {promo.daysRemaining != null && (
            <span className="mt-1 block text-amber-200/80">
              {promo.daysRemaining} days · {(promo.tokensRemaining / 1_000_000).toFixed(1)}M tokens left
              — Cursor, DeepSeek, Gemini & more billed to the platform. Add your own keys anytime.
            </span>
          )}
        </div>
      )}

      <div className="rounded-lg border border-emerald-500/25 bg-emerald-950/15 p-3 text-xs text-emerald-100/90">
        <strong className="text-emerald-300">BYO AI — you pay vendors directly.</strong> Founder OS
        orchestrates; keys stay encrypted on the API. DeepSeek is ~$0.14/M tokens vs hosted wrappers
        charging markup.
      </div>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Step A — Chat brain (required)
        </p>
        {llmConnected ? (
          <p className="mt-2 text-sm text-emerald-300">
            Connected
            {promo?.hasLlm ? ' · platform promo' : ''}
            {defaultProvider !== 'RULE_BASED' ? ` · default: ${defaultProvider}` : ''}.
          </p>
        ) : promo?.eligible && promo?.enabled ? (
          <div className="mt-3 space-y-3">
            {QUICK_LLM.map((item) => (
              <div
                key={item.provider}
                className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-white">{item.label}</p>
                    <p className="text-[11px] text-zinc-500">{item.hint}</p>
                  </div>
                  <a
                    href={item.keyUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[11px] text-violet-300 hover:underline"
                  >
                    Get key →
                  </a>
                </div>
                <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                  <input
                    type="password"
                    value={keys[item.provider] ?? ''}
                    onChange={(e) =>
                      setKeys((k) => ({ ...k, [item.provider]: e.target.value }))
                    }
                    placeholder="sk-…"
                    className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
                  />
                  <button
                    type="button"
                    disabled={busy === item.provider}
                    onClick={() => void connectLlm(item.provider)}
                    className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  >
                    {busy === item.provider ? 'Connecting…' : 'Connect'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Step B — Remote coding (optional · phone-friendly)
        </p>
        <p className="mt-1 text-[11px] text-zinc-500">
          Cursor Cloud runs on your GitHub repo — command builds from your phone. Key from{' '}
          <a
            href="https://cursor.com/dashboard?tab=integrations"
            target="_blank"
            rel="noopener noreferrer"
            className="text-violet-300 hover:underline"
          >
            cursor.com/dashboard → Integrations
          </a>
          .
        </p>
        {builderConnected ? (
          <p className="mt-2 text-sm text-emerald-300">
            Cursor Cloud connected{promo?.hasCursor ? ' (platform promo)' : ''}.
          </p>
        ) : promo?.hasCursor ? (
          <p className="mt-2 text-sm text-emerald-300">
            Cursor covered by founder promo — connect GitHub, then dispatch a build from Mission Control.
          </p>
        ) : (
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <input
              type="password"
              value={cursorKey}
              onChange={(e) => setCursorKey(e.target.value)}
              placeholder="Cursor API key"
              className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
            />
            <button
              type="button"
              disabled={busy === 'cursor'}
              onClick={() => void connectCursor()}
              className="rounded-lg border border-indigo-500/50 bg-indigo-950/40 px-4 py-2 text-sm text-indigo-100 disabled:opacity-50"
            >
              {busy === 'cursor' ? 'Connecting…' : 'Connect Cursor'}
            </button>
          </div>
        )}
      </div>

      {err && <p className="text-sm text-red-300">{err}</p>}

      <p className="text-[11px] text-zinc-600">
        More providers (OpenRouter, Jatevo, Ollama) in{' '}
        <Link href="/settings/builder" className="text-zinc-400 underline hover:text-white">
          Settings → Builder
        </Link>
        .
      </p>
    </div>
  );
}
