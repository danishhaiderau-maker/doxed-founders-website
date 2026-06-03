'use client';

import { useState } from 'react';
import { lockProjectProfile, unlockProjectProfile } from '@/lib/api';

type Props = {
  slug: string;
  accessToken: string;
  profileLocked: boolean;
  onUpdated: () => void;
};

export function ProjectProfileLockButton({ slug, accessToken, profileLocked, onUpdated }: Props) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (!password.trim()) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      if (profileLocked) {
        await unlockProjectProfile(slug, password, accessToken);
        setMsg('Profile unlocked');
      } else {
        await lockProjectProfile(slug, password, accessToken);
        setMsg('Profile locked — hijack protection on');
      }
      setPassword('');
      setOpen(false);
      onUpdated();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`rounded-lg border px-4 py-2 text-sm font-medium ${
          profileLocked
            ? 'border-emerald-500/50 bg-emerald-950/40 text-emerald-200'
            : 'border-amber-500/40 bg-amber-950/25 text-amber-100 hover:border-amber-400/60'
        }`}
        title="Prevent fake X accounts from stealing this listing later"
      >
        {profileLocked ? '🔒 Locked' : '🔒 Lock profile'}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-2 w-72 rounded-xl border border-zinc-700 bg-zinc-950 p-3 shadow-xl">
          <p className="text-xs font-semibold text-white">
            {profileLocked ? 'Unlock profile' : 'Lock profile (security)'}
          </p>
          <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">
            Stops fraud if someone buys DexScreener or uses a fake X to reclaim. Password required to
            change lock — store it safely.
          </p>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={profileLocked ? 'Current lock password' : 'New password (8+ chars)'}
            className="mt-2 w-full rounded-lg border border-zinc-700 bg-black px-2 py-1.5 text-xs text-white"
          />
          <button
            type="button"
            disabled={busy || password.length < 8}
            onClick={() => void submit()}
            className="mt-2 w-full rounded-lg bg-violet-600 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
          >
            {busy ? 'Saving…' : profileLocked ? 'Unlock' : 'Lock now'}
          </button>
          {err && <p className="mt-1 text-[10px] text-red-300">{err}</p>}
          {msg && <p className="mt-1 text-[10px] text-emerald-300">{msg}</p>}
        </div>
      )}
    </div>
  );
}
