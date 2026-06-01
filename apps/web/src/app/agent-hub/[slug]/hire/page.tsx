import AgentHireClient from './page.client';

type Props = { params: Promise<{ slug: string }> };

export default async function AgentHirePage({ params }: Props) {
  const { slug } = await params;
  return <AgentHireClient slug={slug} />;
}
