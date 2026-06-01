import AgentMyDashboardClient from './page.client';

type Props = { params: Promise<{ slug: string }> };

export default async function AgentMyDashboardPage({ params }: Props) {
  const { slug } = await params;
  return <AgentMyDashboardClient slug={slug} />;
}
