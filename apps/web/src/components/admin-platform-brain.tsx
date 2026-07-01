'use client';
import { useState, useEffect } from 'react';
import { fetchPlatformBrainStatus, savePlatformBrainKey, removePlatformBrainKey, type PlatformBrainStatus } from '@/lib/api';

export function AdminPlatformBrain({ token }: { token: string }) {
  const [status, setStatus] = useState<PlatformBrainStatus | null>(null);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { fetchPlatformBrainStatus(token).then(setStatus).catch(() => setErr('Failed to load')); }, [token]);

  const handleSave = async () => {
    if (!key.trim()) return;
    setBusy(true); setErr(null); setMsg(null);
    try { const s = await savePlatformBrainKey(token, key); setStatus(s); setKey(''); setMsg('Platform DeepSeek key saved. Chat will always have a fallback brain.'); }
    catch { setErr('Failed to save key'); } finally { setBusy(false); }
  };

  const handleRemove = async () => {
    setBusy(true); setErr(null); setMsg(null);
    try { const s = await removePlatformBrainKey(token); setStatus(s); setMsg('Platform DeepSeek key removed.'); }
    catch { setErr('Failed to remove key'); } finally { setBusy(false); }
  };

  return (
    <div className='rounded-xl border border-white/10 bg-[#0a0a0f] p-6'>
      <h3 className='text-lg font-semibold text-zinc-100'>Platform Brain</h3>
      <p className='mt-1 text-sm text-zinc-400'>Set a permanent DeepSeek API key as the fallback brain for all users. When a user has no brain key connected, this ensures chat always works.</p>
      {status?.configured ? (
        <div className='mt-4 flex items-center gap-2 text-sm text-emerald-400'><span className='h-2 w-2 rounded-full bg-emerald-400' /> DeepSeek platform key configured{status.updatedAt && <span className='text-zinc-500'>- updated {new Date(status.updatedAt).toLocaleDateString()}</span>}</div>
      ) : status !== null ? (
        <div className='mt-4 flex items-center gap-2 text-sm text-amber-400'><span className='h-2 w-2 rounded-full bg-amber-400' /> No platform key configured</div>
      ) : null}
      <div className='mt-4 flex items-end gap-2'>
        <input type='password' value={key} onChange={(e) => setKey(e.target.value)} placeholder='Paste DeepSeek API key (sk-...)' className='flex-1 rounded-lg border border-white/10 bg-[#12121a] px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-emerald-400/40' />
        <button onClick={handleSave} disabled={busy || !key.trim()} className='rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-400 disabled:opacity-40'>{busy ? 'Saving...' : 'Save key'}</button>
        {status?.configured && <button onClick={handleRemove} disabled={busy} className='rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-400 transition hover:bg-white/5 disabled:opacity-40'>Remove</button>}
      </div>
      {msg && <div className='mt-3 text-sm text-emerald-400'>{msg}</div>}
      {err && <div className='mt-3 text-sm text-rose-400'>{err}</div>}
    </div>
  );
}