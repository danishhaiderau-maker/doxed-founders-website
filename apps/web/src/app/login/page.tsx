import { Suspense } from 'react';
import { getEnabledOAuthProviders } from '@/lib/auth-options';
import LoginPageClient from './page.client';

export default function LoginPage() {
  const oauth = getEnabledOAuthProviders();
  const nextAuthUrl = process.env.NEXTAUTH_URL ?? 'http://localhost:3000';
  return (
    <Suspense fallback={<main className="min-h-screen" />}>
      <LoginPageClient oauthEnabled={oauth} nextAuthUrl={nextAuthUrl} />
    </Suspense>
  );
}
