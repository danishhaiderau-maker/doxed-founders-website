'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { fetchPrivacyDataClasses, type PrivacyDataClassesResponse } from '@/lib/api';

export function PrivacyDataClassOverview() {
  const [data, setData] = useState<PrivacyDataClassesResponse | null>(null);

  useEffect(() => {
    fetchPrivacyDataClasses()
      .then(setData)
      .catch(() => setData(null));
  }, []);

  if (!data) return null;

  return (
    <section className="mt-8 rounded-xl border border-zinc-700/60 bg-zinc-950/50 p-5">
      <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-400">
        Platform privacy (P0)
      </p>
      <h2 className="mt-1 text-lg font-bold text-white">Public vs private data</h2>
      <p className="mt-2 max-w-3xl text-sm text-zinc-400">
        {data.hybridModel.publicLayer}. {data.hybridModel.privateLayer}.{' '}
        {data.hybridModel.teeLayer}.
      </p>
      <ul className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {data.classes.map((c) => (
          <li
            key={c.id}
            className="rounded-lg border border-zinc-800/80 bg-black/30 px-3 py-2.5 text-xs"
          >
            <p className="font-semibold text-zinc-200">{c.label}</p>
            <p className="mt-1 text-zinc-500">{c.storage}</p>
          </li>
        ))}
      </ul>
      {data.audit.compliant && (
        <p className="mt-3 text-xs text-emerald-400/90">
          Static data-class audit: compliant ({data.audit.modelCount} models,{' '}
          {data.audit.routeCount} route patterns).
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-3 text-xs font-semibold">
        <Link href="/founder-den?tab=analytics" className="text-violet-300 hover:text-violet-200">
          Your boundaries (signed in) →
        </Link>
        <Link href="/settings/builder#founder-attestation" className="text-zinc-400 hover:text-white">
          Attestation dashboard →
        </Link>
      </div>
    </section>
  );
}
