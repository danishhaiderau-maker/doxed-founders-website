import { redirect } from 'next/navigation';

type Props = {
  searchParams: Promise<{ tab?: string }>;
};

export default async function IntegrationsAliasPage({ searchParams }: Props) {
  const { tab } = await searchParams;
  const allowed = new Set(['downloads', 'ai', 'infra', 'security', 'founder-node']);
  const nextTab = tab && allowed.has(tab) ? tab : undefined;
  redirect(nextTab ? `/settings/builder?tab=${nextTab}` : '/settings/builder');
}
