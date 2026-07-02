'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchAdminFounderPromoSettings,
  saveAdminFounderPromoCredentials,
  updateAdminFounderPromoSettings,
  type FounderPromoPlatformSettings,
} from '@/lib/api';

type Props = {
  accessToken: string;
  /** When true, hides the platform API-key cards (they are managed elsewhere,
   * e.g. the consolidated Admin → AI Keys panel). Promo toggle/cap/window/message
   * remain editable. Defaults to false (preserves connected-accounts behavior). */
  hideKeyCards?: boolean;
};

const PROMO_KEY_FIELDS = [
  { key: 'glm' as const, label: 'GLM 5.2 API key (ZhipuAI)', placeholder: 'xxx.xxx' },
  { key: 'gemini' as const, label: 'Google Gemini API key', placeholder: 'AIza…' },
  { key: 'deepseek' as const, label: 'DeepSeek API key', placeholder: 'sk-…' },
];

export function AdminFounderPromoPanel({ accessToken, hideKeyCards = false }: Props) {
  const [settings, setSettings] = useState<FounderPromoPlatformSettings | null>(null);
  const [message, setMessage] = useState('');
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchAdminFounderPromoSettings(accessToken);
      setSettings(data);
      setMessage(data.message ?? '');
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load promo settings');
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function save(patch: Partial<FounderPromoPlatformSettings>) {
    setBusy(true);
    setErr(null);
    try {
      const data = await updateAdminFounderPromoSettings(accessToken, patch);
      setSettings(data);
      setMsg(patch.enabled !== undefined ? (patch.enabled ? 'Promo ON — new founders get free AI' : 'Promo OFF') : 'Saved');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(false);
    }
  }

  async function saveKey(provider: (typeof PROMO_KEY_FIELDS)[number]['key']) {
    const value = keyDrafts[provider]?.trim();
    if (!value) return;
    setBusy(true);
    setErr(null);
    try {
      const data = await saveAdminFounderPromoCredentials(accessToken, { [provider]: value });
      setSettings(data);
      setKeyDrafts((prev) => ({ ...prev, [provider]: '' }));
      setMsg(`${PROMO_KEY_FIELDS.find((f) => f.key === provider)?.label ?? provider} saved`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save key failed');
    } finally {
      setBusy(false);
    }
  }

  if (!settings && !err) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 px-4 py-3 text-sm text-amber-100/80">
        Loading Founder AI promo settings…
      </div>
    );
  }

  if (!settings) return null;

  const keysMissing = settings.enabled && !settings.credentialsConfigured;

  return (
    <div className="rounded-xl border border-amber-500/40 bg-gradient-to-br from-amber-950/30 to-zinc-950/50 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wider text-amber-400">Admin only</p>
          <h3 className="mt-1 text-lg font-semibold text-white">Founder AI promo</h3>
          <p className="mt-1 max-w-xl text-xs text-zinc-400">
            Offer all signed-up users 3 months of platform-billed AI (GLM 5.2, Gemini, DeepSeek — cheap &amp; best). Timer
            starts at signup. Hard stop after {settings.windowDays} days or {(settings.tokenCap / 1_000_000).toFixed(0)}M
            tokens — users must connect their own keys to continue.
          </p>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => save({ enabled: !settings.enabled })}
          className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
            settings.enabled
              ? 'bg-emerald-600 text-white hover:bg-emerald-500'
              : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'
          }`}
        >
          {settings.enabled ? 'Promo ON' : 'Promo OFF'}
        </button>
      </div>

      {keysMissing && (
        <div className="mt-4 rounded-lg border border-amber-500/50 bg-amber-950/30 px-3 py-2 text-xs text-amber-100">
          Promo is ON but no platform API keys are saved yet. Add at least one key below — founders cannot use free AI
          until keys are configured.
        </div>
      )}

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="block text-xs">
          <span className="text-zinc-500">Token cap (total promo usage)</span>
          <input
            type="number"
            defaultValue={settings.tokenCap}
            onBlur={(e) => {
              const v = parseInt(e.target.value, 10);
              if (v > 0 && v !== settings.tokenCap) save({ tokenCap: v });
            }}
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm text-white"
          />
        </label>
        <label className="block text-xs">
          <span className="text-zinc-500">Free window (days from founder signup)</span>
          <input
            type="number"
            defaultValue={settings.windowDays}
            onBlur={(e) => {
              const v = parseInt(e.target.value, 10);
              if (v > 0 && v !== settings.windowDays) save({ windowDays: v });
            }}
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm text-white"
          />
        </label>
      </div>

      <label className="mt-3 block text-xs">
        <span className="text-zinc-500">Flash message (signup / connected accounts)</span>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onBlur={() => {
            if (message.trim() !== (settings.message ?? '')) save({ message: message.trim() });
          }}
          rows={2}
          className="mt-1 w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm text-white"
          placeholder="Join now — 1 month free GLM 5.2, Gemini & DeepSeek on Founder OS"
        />
      </label>

      <div className="mt-5 border-t border-zinc-800 pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-semibold text-white">Platform API keys (promo pool)</p>
          {hideKeyCards && (
            <span className="text-[11px] text-violet-300">
              Managed in Admin → AI Keys
            </span>
          )}
        </div>
        {hideKeyCards ? (
          <p className="mt-2 text-[11px] text-zinc-500">
            These keys are managed on the consolidated{' '}
            <span className="text-zinc-300">Admin → AI Keys</span> panel. Promo
            eligibility, token cap, free window, and the flash message below stay
            here.
          </p>
        ) : (
          <>
            <p className="mt-1 text-[11px] text-zinc-500">
              These keys are used only for eligible founders during the promo window. Never shared with users. Usage is logged
              as <code className="text-zinc-400">platform_promo</code> billing.
            </p>
            <ul className="mt-3 space-y-3">
              {PROMO_KEY_FIELDS.map((field) => {
                const configured = settings.credentialsStatus?.[field.key] ?? false;
                const isGlm = field.key === 'glm';
                return (
                  <li key={field.key} className="rounded-lg border border-zinc-800 bg-black/40 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-xs font-medium text-zinc-300">{field.label}</span>
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                          configured ? 'bg-emerald-500/20 text-emerald-300' : 'bg-zinc-800 text-zinc-500'
                        }`}
                      >
                        {configured ? 'Saved' : 'Not set'}
                      </span>
                    </div>
                    {isGlm && (
                      <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-400">
                        This is your <span className="text-zinc-200">Z.ai API key</span> (not a Cursor key). Get it from{' '}
                        <a href="https://z.ai" target="_blank" rel="noreferrer" className="text-violet-300 underline hover:text-violet-200">z.ai</a>
                        {' '}— GLM 5.2 is accessed through Z.ai&apos;s OpenAI-compatible API at{' '}
                        <code className="text-zinc-300">https://api.z.ai/api/coding/paas/v4</code>. A Cursor subscription does
                        <span className="text-zinc-200"> not</span> provide external API access; you need a separate Z.ai key.
                      </p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-2">
                      <input
                        type="password"
                        value={keyDrafts[field.key] ?? ''}
                        onChange={(e) => setKeyDrafts((prev) => ({ ...prev, [field.key]: e.target.value }))}
                        placeholder={configured ? 'Enter new key to replace…' : field.placeholder}
                        className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-white"
                      />
                      <button
                        type="button"
                        disabled={busy || !keyDrafts[field.key]?.trim()}
                        onClick={() => saveKey(field.key)}
                        className="rounded-lg bg-amber-600 px-3 py-2 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-40"
                      >
                        Save key
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>

      {msg && <p className="mt-2 text-xs text-emerald-300">{msg}</p>}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </div>
  );
}
