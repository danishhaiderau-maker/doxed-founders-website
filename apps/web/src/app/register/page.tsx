import { getEnabledOAuthProviders } from '@/lib/auth-options';
import RegisterPageClient from './page.client';

export default function RegisterPage() {
  const oauth = getEnabledOAuthProviders();
  return <RegisterPageClient oauthEnabled={oauth} />;
}
