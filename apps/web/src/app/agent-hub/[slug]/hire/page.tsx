import { Suspense } from 'react';
import AgentHireClient from './page.client';

type Props = { params: Promise<{ slug: string }> };

export default async function AgentHirePage({ params }: Props) {
  const { slug } = await params;
  return (
    <Suspense fallback={<p className="p-8 text-zinc-500">Loading hire wizard…</p>}>
      <AgentHireClient slug={slug} />
    </Suspense>
  );
}
