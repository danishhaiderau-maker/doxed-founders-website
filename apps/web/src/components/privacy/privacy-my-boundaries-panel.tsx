'use client';

import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { fetchPrivacyMyBoundaries, type PrivacyMyBoundariesResponse } from '@/lib/api';

export function PrivacyMyBoundariesPanel() {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [data, setData] = useState<PrivacyMyBoundariesResponse | null>(null);

  useEffect(() => {
    if (!token) return;
    fetchPrivacyMyBoundaries(token)
      .then(setData)
      .catch(() => setData(null));
  }, [token]);

  if (!token || !data) return null;

  return (
    <div className="rounded-lg border border-emerald-500/20 bg-emerald-950/10 p-4">
      <p className="text-sm font-medium text-white">Your data boundaries (P0)</p>
      <ul className="mt-3 space-y-2 text-xs text-zinc-400">
        <li>
          <span className="text-zinc-300">Memory:</span> {data.yourData.memory.mode} —{' '}
          {data.yourData.memory.note}
        </li>
        <li>
          <span className="text-zinc-300">Secrets:</span> {data.yourData.secrets.modeLabel} —{' '}
          {data.yourData.secrets.credentialCount} credential(s), unwrap audited
        </li>
        {data.yourData.founderNode && (
          <li>
            <span className="text-zinc-300">Founder Node:</span> {data.yourData.founderNode.label} (
            {data.yourData.founderNode.status})
          </li>
        )}
        <li>
          <span className="text-zinc-300">Public listings:</span> {data.publicProduct.note}
        </li>
      </ul>
    </div>
  );
}
