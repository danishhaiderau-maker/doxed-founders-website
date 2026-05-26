import { Suspense } from 'react';
import { getEnabledOAuthProviders } from '@/lib/auth-options';
import LoginPageClient from './page.client';

export default function LoginPage() {
  const oauth = getEnabledOAuthProviders();
  return (
    <Suspense fallback={<main className="min-h-screen" />}>
      <LoginPageClient oauthEnabled={oauth} />
    </Suspense>
  );
}
