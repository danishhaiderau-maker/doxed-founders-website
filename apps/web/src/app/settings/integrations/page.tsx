import { redirect } from 'next/navigation';

type Props = {
  searchParams: Promise<{ tab?: string }>;
};

/**
 * Legacy /settings/integrations alias. The old target /settings/builder was
 * retired — pairing, downloads, AI providers, and infrastructure credentials
 * all live inside the Founder IDE app now. Security lives at
 * /account?tab=security. Collapse every deep link to /founder-ide so existing
 * bookmarks do not 404.
 */
export default async function IntegrationsAliasPage({ searchParams }: Props) {
  const { tab } = await searchParams;
  if (tab === 'security') {
    redirect('/account?tab=security');
  }
  redirect('/founder-ide');
}
