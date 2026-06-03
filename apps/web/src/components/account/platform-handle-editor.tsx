'use client';

import { useState } from 'react';
import { updatePlatformHandle } from '@/lib/api';

type Props = {
  accessToken: string;
  initialHandle: string;
  canEdit: boolean;
  hasTwitterConnected: boolean;
  onUpdated: (handle: string) => void;
};

export function PlatformHandleEditor({
  accessToken,
  initialHandle,
  canEdit,
  hasTwitterConnected,
  onUpdated,
}: Props) {
  const [draft, setDraft] = useState(initialHandle);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  if (!canEdit) {
    return (
      <p className="text-sm text-amber-200/90">
        Platform handles for admin accounts are managed by the platform.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-zinc-500">
        Your public ID mixes animals, birds, reptiles, and countries (e.g. Falcon · Japan). Names like
        Admin or Doxxed Founder are reserved to prevent scams.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setSaved(false);
          }}
          className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm text-white"
          placeholder="Crimson Falcon · Kenya"
        />
        <button
          type="button"
          disabled={busy || draft.trim() === initialHandle}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              const res = await updatePlatformHandle(draft.trim(), accessToken);
              onUpdated(res.platformHandle);
              setDraft(res.platformHandle);
              setSaved(true);
            } catch (e) {
              setErr(e instanceof Error ? e.message : 'Could not update handle');
            } finally {
              setBusy(false);
            }
          }}
          className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? 'Saving…' : 'Save handle'}
        </button>
      </div>
      {saved && <p className="text-xs text-emerald-300">Handle updated.</p>}
      {err && <p className="text-xs text-red-300">{err}</p>}
      {!hasTwitterConnected && (
        <p className="text-xs text-amber-300/90">
          Connect X (Twitter) under Connected Accounts to submit listings and receive admin proof requests.
        </p>
      )}
    </div>
  );
}
