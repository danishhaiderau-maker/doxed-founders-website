import { Suspense } from 'react';
import { PlatformOfflineNotice } from '@/components/platform-offline-notice';
import { isBackendApiRetired } from '@/lib/backend-api-retired';
import { getEnabledOAuthProviders } from '@/lib/auth-options';
import RegisterPageClient from './page.client';

export default function RegisterPage() {
  if (isBackendApiRetired()) {
    return <PlatformOfflineNotice feature="register" />;
  }

  const oauth = getEnabledOAuthProviders();
  const nextAuthUrl = process.env.NEXTAUTH_URL ?? 'http://localhost:3000';
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center text-zinc-400">Loading…</div>}>
      <RegisterPageClient oauthEnabled={oauth} nextAuthUrl={nextAuthUrl} />
    </Suspense>
  );
}
