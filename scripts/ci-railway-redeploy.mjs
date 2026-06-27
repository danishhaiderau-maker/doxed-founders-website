/**
 * CI-friendly Railway redeploy — uses RAILWAY_TOKEN from env (GitHub Secrets).
 * No vault dependency. Triggers a production redeploy of the doxed-founders-website service.
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
  const target = projects.find((p) =>
    p.services?.edges?.some((s) => s.node.name === 'doxed-founders-website'),
  );
  if (!target) {
    console.warn('doxed-founders-website Railway service not found — skipping');
    return;
  }

  const env =
    target.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
    target.environments?.edges?.[0]?.node;
  const service =
    target.services?.edges?.find((e) => e.node.name === 'doxed-founders-website')?.node;
  if (!env || !service) {
    console.warn('Missing Railway env/service — skipping');
    return;
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

main().catch((err) => {
  console.error('Railway redeploy failed:', err.message);
  process.exit(1);
});
