/**
 * CI-friendly Railway redeploy — uses RAILWAY_TOKEN from env (GitHub Secrets).
 * No vault dependency. Triggers both production API and isolated relay worker.
 *
 * Usage: RAILWAY_TOKEN=... node scripts/ci-railway-redeploy.mjs
 */
const RAILWAY_GQL = 'https://backboard.railway.com/graphql/v2';

async function railwayGql(token, query, variables = {}) {
  const res = await fetch(RAILWAY_GQL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors?.length) throw new Error(json.errors.map((e) => e.message).join('; '));
  return json.data;
}

async function main() {
  const token = process.env.RAILWAY_TOKEN?.trim();
  if (!token) {
    if (process.env.CI) throw new Error('RAILWAY_TOKEN not set in CI');
    console.warn('RAILWAY_TOKEN not set — skipping Railway redeploy');
    return;
  }

  console.log('=== CI Railway redeploy ===');

  const data = await railwayGql(
    token,
    `query {
      projects { edges { node {
        id name
        environments { edges { node { id name } } }
        services { edges { node { id name } } }
      } } }
    }`,
  );

  const projects = data.projects?.edges?.map((e) => e.node) ?? [];
  for (const serviceName of ['doxed-founders-website', 'relay-executor']) {
    const target = projects.find((project) =>
      project.services?.edges?.some((edge) => edge.node.name === serviceName),
    );
    const env =
      target?.environments?.edges?.find((edge) => edge.node.name === 'production')?.node ??
      target?.environments?.edges?.[0]?.node;
    const service =
      target?.services?.edges?.find((edge) => edge.node.name === serviceName)?.node;
    if (!target || !env || !service) {
      throw new Error(`Required Railway service ${serviceName} or production environment not found`);
    }
    await railwayGql(
      token,
      `mutation($serviceId: String!, $environmentId: String!) {
        serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
      }`,
      { serviceId: service.id, environmentId: env.id },
    );
    console.log(`Railway redeploy triggered on ${target.name} / ${service.name}`);
  }
}

main().catch((err) => {
  console.error('Railway redeploy failed:', err.message);
  process.exit(1);
});
