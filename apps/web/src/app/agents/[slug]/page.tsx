import AgentDetailClient from './page.client';

export default async function AgentDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <AgentDetailClient slug={slug} />;
}
