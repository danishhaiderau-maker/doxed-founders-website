import { getEnabledOAuthProviders } from '@/lib/auth-options';
import RegisterPageClient from './page.client';

export default function RegisterPage() {
  const oauth = getEnabledOAuthProviders();
  const nextAuthUrl = process.env.NEXTAUTH_URL ?? 'http://localhost:3000';
  return <RegisterPageClient oauthEnabled={oauth} nextAuthUrl={nextAuthUrl} />;
}
