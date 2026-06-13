'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  createSignalApiKey,
  fetchSignalApiKeys,
  revokeSignalApiKey,
  type SignalApiKeySummary,
} from '@/lib/api';

export function SignalApiPanel({
  slug,
  token,
  signedIn,
}: {
  slug: string;
  token?: string;
  signedIn: boolean;
}) {
  const [keys, setKeys] = useState<SignalApiKeySummary[]>([]);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setKeys(await fetchSignalApiKeys(slug, token));
    } catch {
      setKeys([]);
    }
  }, [slug, token]);

  useEffect(() => {
    void load();
  }, [load]);

  if (slug !== 'conservative-btc') return null;

  return (
    <section className="mx-4 mt-6 rounded-xl border border-violet-500/30 bg-violet-950/20 p-5 sm:mx-6">
      <h2 className="text-lg font-semibold text-white">Signal API (exchange-neutral)</h2>
      <p className="mt-1 text-sm text-zinc-400">
        Pay <strong className="text-zinc-200">10% success fee on profitable closes only</strong>. Mandatory
        exchange stop at fill. Works on Hyperliquid, Bitfinex, or any venue using your local mark.
      </p>
      <Link href="/docs/signal-api" className="mt-2 inline-block text-sm text-violet-400 hover:underline">
        Subscriber docs →
      </Link>

      {!signedIn ? (
        <p className="mt-4 text-sm text-zinc-500">Sign in to create an API key.</p>
      ) : (
        <div className="mt-4 space-y-3">
          <button
            type="button"
            disabled={busy}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
            onClick={async () => {
              if (!token) return;
              setBusy(true);
              setError(null);
              setNewKey(null);
              try {
                const res = await createSignalApiKey(slug, token, 'default');
                setNewKey(res.apiKey);
                await load();
              } catch (e) {
                setError(e instanceof Error ? e.message : 'Failed to create key');
              } finally {
                setBusy(false);
              }
            }}
          >
            Create Signal API key
          </button>

          {newKey && (
            <div className="rounded border border-amber-500/40 bg-amber-950/40 p-3 text-sm">
              <p className="font-medium text-amber-200">Copy now — shown once:</p>
              <code className="mt-1 block break-all text-xs text-amber-100">{newKey}</code>
            </div>
          )}

          {error && <p className="text-sm text-red-400">{error}</p>}

          {keys.length > 0 && (
            <ul className="space-y-2 text-sm">
              {keys.map((k) => (
                <li
                  key={k.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-700 px-3 py-2"
                >
                  <span className="font-mono text-zinc-300">{k.keyPrefix}…</span>
                  <button
                    type="button"
                    className="text-xs text-red-400 hover:underline"
                    onClick={async () => {
                      if (!token) return;
                      await revokeSignalApiKey(slug, k.id, token);
                      await load();
                    }}
                  >
                    Revoke
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
