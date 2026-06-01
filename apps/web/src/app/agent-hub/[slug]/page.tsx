import AgentHubDashboardClient from './page.client';

type Props = { params: Promise<{ slug: string }> };

export default async function AgentHubDashboardPage({ params }: Props) {
  const { slug } = await params;
  return <AgentHubDashboardClient slug={slug} />;
}
