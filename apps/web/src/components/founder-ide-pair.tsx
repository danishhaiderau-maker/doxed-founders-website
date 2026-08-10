'use client';

import { useEffect, useRef, useState } from 'react';
import { createFounderNodePairingCode, fetchFounderNodeStatus } from '@/lib/api';

type Props = { accessToken: string };

type PairingResponse = {
  code?: string;
  expiresAt?: string;
  targetPlatform?: string | null;
};

/**
 * Pairing widget for the new /founder-ide landing page. Calls the existing
 * POST /founder-node/pairing-code endpoint (via @/lib/api which routes through
 * the Next.js /api proxy in production) and shows the one-time code along with
 * the paste-it-into-the-app steps.
 *
 * After displaying the code we poll GET /founder-node/status every 4s; the
 * moment a paired node shows up we flip `paired` to true and notify the parent
 * via onPaired so the page can transition to the post-pair chat dispatch UI.
 */
export function FounderIdePair({
  accessToken,
  onPaired,
}: Props & { onPaired?: (nodeId: string) => void }) {
  const [data, setData] = useState<PairingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const firedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    createFounderNodePairingCode(accessToken, 'desktop')
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  // Poll node status — fire onPaired the first time a node appears for this user.
  useEffect(() => {
    if (!accessToken || !data?.code) return;
    let stopped = false;

    const tick = async () => {
      if (stopped) return;
      try {
        const status = await fetchFounderNodeStatus(accessToken);
        const firstOnline = status.nodes?.find((node) => node.status === 'online');
        if (firstOnline && !firedRef.current) {
          firedRef.current = true;
          onPaired?.(firstOnline.nodeId);
          return;
        }
      } catch {
        // network blip — keep polling
      }
      timer = window.setTimeout(tick, 4000);
    };

    let timer = window.setTimeout(tick, 4000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [accessToken, data?.code, onPaired]);

  if (loading) return <p className="text-sm text-zinc-500">Generating pairing code…</p>;
  if (err) return <p className="text-sm text-red-400">Failed: {err}</p>;
  if (!data) return null;

  return (
    <div className="space-y-3">
      {data.code && (
        <div>
          <p className="text-xs uppercase text-zinc-500">Your pairing code</p>
          <code className="mt-1 block rounded bg-zinc-900 px-3 py-2 font-mono text-lg text-emerald-300">
            {data.code}
          </code>
        </div>
      )}
      <ol className="list-decimal space-y-1 pl-5 text-sm text-zinc-300">
        <li>Open Founder IDE on your laptop.</li>
        <li>Go to <strong className="text-white">Settings → Founder Node</strong>.</li>
        <li>Click <strong className="text-white">Pair</strong> and paste the code above.</li>
        <li>Keep the IDE running — pairing completes in a few seconds.</li>
      </ol>
      <p className="text-xs text-zinc-500">
        Waiting for your Founder IDE to call home… this page will switch automatically.
      </p>
      {data.expiresAt && (
        <p className="text-xs text-zinc-500">Expires at {new Date(data.expiresAt).toLocaleString()}</p>
      )}
    </div>
  );
}
