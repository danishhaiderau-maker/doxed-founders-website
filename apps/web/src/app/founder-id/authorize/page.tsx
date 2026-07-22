import { Suspense } from 'react';
import FounderIdAuthorizeClient from './page.client';

export default function FounderIdAuthorizePage() {
  return (
    <Suspense fallback={<main className="min-h-screen bg-[#08090b]" />}>
      <FounderIdAuthorizeClient />
    </Suspense>
  );
}
