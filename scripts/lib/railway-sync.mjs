const RAILWAY_GQL = 'https://backboard.railway.com/graphql/v2';

export async function railwayGql(token, query, variables = {}) {
  const res = await fetch(RAILWAY_GQL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors?.length) throw new Error(json.errors.map((e) => e.message).join('; '));
  return json.data;
}

export async function syncRailwayServiceVars(token, vars) {
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
  if (!target) throw new Error('doxed-founders-website Railway service not found');

  const env =
    target.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
    target.environments?.edges?.[0]?.node;
  const service =
    target.services?.edges?.find((e) => e.node.name === 'doxed-founders-website')?.node;
  if (!env || !service) throw new Error('Missing Railway env/service');

  await railwayGql(
    token,
    `mutation($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }`,
    {
      input: {
        projectId: target.id,
        environmentId: env.id,
        serviceId: service.id,
        variables: vars,
        replace: false,
      },
    },
  );

  await railwayGql(
    token,
    `mutation($serviceId: String!, $environmentId: String!) {
      serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
    }`,
    { serviceId: service.id, environmentId: env.id },
  );

  return { project: target.name, service: service.name };
}
