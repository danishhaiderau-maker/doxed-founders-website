'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchAdminFounderPromoSettings,
  updateAdminFounderPromoSettings,
  type FounderPromoPlatformSettings,
} from '@/lib/api';

type Props = {
  accessToken: string;
};

export function AdminFounderPromoPanel({ accessToken }: Props) {
  const [settings, setSettings] = useState<FounderPromoPlatformSettings | null>(null);
  const [message, setMessage] = useState('');
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

  if (!settings) return null;

  return (
    <div className="rounded-xl border border-amber-500/40 bg-gradient-to-br from-amber-950/30 to-zinc-950/50 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wider text-amber-400">Admin only</p>
          <h3 className="mt-1 text-lg font-semibold text-white">Founder AI promo</h3>
          <p className="mt-1 max-w-xl text-xs text-zinc-400">
            Turn on to offer new founders 1 month of platform-billed AI (Gemini, DeepSeek, Cursor, Ollama). Timer
            starts at founder registration. Cap: {(settings.tokenCap / 1_000_000).toFixed(0)}M tokens per user.
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
          placeholder="Join now — 1 month free Cursor, Gemini & DeepSeek on Founder OS"
        />
      </label>

      <p className="mt-3 text-[11px] text-zinc-500">
        Platform keys: configure Gemini / DeepSeek / Cursor in{' '}
        <a href="/admin/control" className="text-amber-300 underline">
          Admin Control
        </a>
        . Usage is logged to platform adoption metrics with <code className="text-zinc-400">platform_promo</code>{' '}
        billing source.
      </p>

      {msg && <p className="mt-2 text-xs text-emerald-300">{msg}</p>}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </div>
  );
}
