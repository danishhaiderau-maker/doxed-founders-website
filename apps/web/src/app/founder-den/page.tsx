import { Suspense } from 'react';
import FounderDenPageClient from './page.client';

export default function FounderDenPage() {
  return (
    <Suspense fallback={<main className="min-h-screen bg-[#050508]" />}>
      <FounderDenPageClient />
    </Suspense>
  );
}
