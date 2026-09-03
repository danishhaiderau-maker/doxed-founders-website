#!/usr/bin/env node
/**
 * Deploy one exact Git commit to the Railway API control plane without
 * restarting the isolated relay-executor service.
 *
 * The command is deliberately split into receipt-bound phases so CI can keep
 * the pre-deploy executor deployment id and prove it did not change.
 */
import { PrismaClient } from '@prisma/client';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const GQL = 'https://backboard.railway.com/graphql/v2';
const API_SERVICE = 'doxed-founders-website';
const EXECUTOR_SERVICE = 'relay-executor';
const API_URL = 'https://doxed-founders-website-production.up.railway.app';
const TERMINAL_FAILURES = new Set(['FAILED', 'CRASHED', 'REMOVED']);

export function pausedAndDisarmed(instance) {
  const dashboard = instance?.dashboardState ?? {};
  return instance?.status === 'PAUSED'
    && dashboard.relayExecutionMode === 'PAUSED'
    && dashboard.relayArmedAt == null
    && dashboard.realTradingConfirmedAt == null;
}

export function assertExactSha(value) {
  const sha = String(value ?? '').trim().toLowerCase();
  if (!/^[0-9a-f]{40}$/.test(sha)) {
    throw new Error('EXPECTED_COMMIT_SHA must be an exact 40-character Git SHA');
  }
  return sha;
}

async function gql(token, query, variables = {}) {
  const response = await fetch(GQL, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ query, variables }),
    signal: AbortSignal.timeout(20_000),
  });
  if (!response.ok) throw new Error(`Railway GraphQL HTTP ${response.status}`);
  const body = await response.json();
  if (body.errors?.length) throw new Error(body.errors.map((error) => error.message).join('; '));
  return body.data;
}

async function loadTopology(token) {
  const data = await gql(token, `query {
    projects { edges { node {
      id name
      environments { edges { node { id name } } }
      services { edges { node { id name serviceInstances { edges { node {
        environmentId latestDeployment { id status createdAt meta }
      } } } } } }
    } } }
  }`);
  const projects = data.projects?.edges?.map((edge) => edge.node) ?? [];
  const matches = projects.filter((project) => {
    const names = new Set(project.services?.edges?.map((edge) => edge.node.name) ?? []);
    return names.has(API_SERVICE) && names.has(EXECUTOR_SERVICE);
  });
  if (matches.length !== 1) {
    throw new Error(`Expected one Railway project containing isolated API and executor services; found ${matches.length}`);
  }
  const project = matches[0];
  const environment = project.environments?.edges?.find((edge) => edge.node.name === 'production')?.node;
  if (!environment) throw new Error('Railway production environment missing');
  const service = (name) => project.services.edges.find((edge) => edge.node.name === name)?.node;
  const instance = (serviceNode) => serviceNode?.serviceInstances?.edges?.find(
    (edge) => edge.node.environmentId === environment.id,
  )?.node;
  const api = service(API_SERVICE);
  const executor = service(EXECUTOR_SERVICE);
  const apiInstance = instance(api);
  const executorInstance = instance(executor);
  if (!api || !executor || !apiInstance?.latestDeployment || !executorInstance?.latestDeployment) {
    throw new Error('Railway API/executor production deployment topology is incomplete');
  }
  return { project, environment, api, executor, apiInstance, executorInstance };
}

async function provePausedTarget(targetInstanceId) {
  if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is required');
  if (!targetInstanceId) throw new Error('TARGET_INSTANCE_ID is required');
  const prisma = new PrismaClient();
  try {
    const read = () => prisma.tradingAgentInstance.findUnique({
      where: { id: targetInstanceId },
      select: {
        id: true,
        status: true,
        exchangeProvider: true,
        dashboardState: true,
        updatedAt: true,
        agent: { select: { slug: true } },
      },
    });
    const first = await read();
    if (!first || first.agent.slug !== 'conservative-btc' || first.exchangeProvider !== 'bitfinex') {
      throw new Error('Target is not the existing conservative-btc Bitfinex relay instance');
    }
    if (!pausedAndDisarmed(first)) {
      throw new Error('Target relay is not durably PAUSED with PAUSED execution mode and null arm timestamps');
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    const second = await read();
    if (!second || second.updatedAt.toISOString() !== first.updatedAt.toISOString() || !pausedAndDisarmed(second)) {
      throw new Error('Target paused/disarmed state changed during the stable-read proof');
    }
    console.log(JSON.stringify({
      ok: true,
      targetInstanceId: first.id,
      status: first.status,
      relayExecutionMode: first.dashboardState?.relayExecutionMode ?? null,
      relayArmedAt: first.dashboardState?.relayArmedAt ?? null,
      realTradingConfirmedAt: first.dashboardState?.realTradingConfirmedAt ?? null,
      stableUpdatedAt: first.updatedAt.toISOString(),
    }));
  } finally {
    await prisma.$disconnect();
  }
}

async function snapshot(token) {
  const topology = await loadTopology(token);
  console.log(JSON.stringify({
    apiServiceId: topology.api.id,
    apiDeploymentId: topology.apiInstance.latestDeployment.id,
    apiStatus: topology.apiInstance.latestDeployment.status,
    executorServiceId: topology.executor.id,
    executorDeploymentId: topology.executorInstance.latestDeployment.id,
    executorStatus: topology.executorInstance.latestDeployment.status,
  }));
}

async function deploy(token, expectedSha) {
  const topology = await loadTopology(token);
  const data = await gql(token, `mutation($serviceId: String!, $environmentId: String!, $commitSha: String!) {
    serviceInstanceDeployV2(serviceId: $serviceId, environmentId: $environmentId, commitSha: $commitSha)
  }`, {
    serviceId: topology.api.id,
    environmentId: topology.environment.id,
    commitSha: expectedSha,
  });
  const deploymentId = String(data.serviceInstanceDeployV2 ?? '').trim();
  if (!deploymentId) throw new Error('Railway did not return an API deployment id');
  console.log(JSON.stringify({ ok: true, deploymentId, service: API_SERVICE, commitSha: expectedSha }));
}

async function verify(token, expectedSha, expectedApiDeploymentId, expectedExecutorDeploymentId) {
  const deadline = Date.now() + Number(process.env.DEPLOY_TIMEOUT_MS ?? 12 * 60_000);
  let observed;
  while (Date.now() < deadline) {
    const topology = await loadTopology(token);
    const apiDeployment = topology.apiInstance.latestDeployment;
    const executorDeployment = topology.executorInstance.latestDeployment;
    observed = {
      apiDeploymentId: apiDeployment.id,
      apiStatus: apiDeployment.status,
      apiCommitSha: String(apiDeployment.meta?.commitSha ?? '').toLowerCase(),
      executorDeploymentId: executorDeployment.id,
      executorStatus: executorDeployment.status,
    };
    if (executorDeployment.id !== expectedExecutorDeploymentId) {
      throw new Error('Isolated relay-executor deployment changed during API-only rollout');
    }
    if (apiDeployment.id !== expectedApiDeploymentId) {
      throw new Error('Latest API deployment does not match the exact deployment returned by the commit-bound mutation');
    }
    if (TERMINAL_FAILURES.has(apiDeployment.status)) {
      throw new Error(`API deployment reached terminal failure ${apiDeployment.status}`);
    }
    if (apiDeployment.status === 'SUCCESS'
      && (!observed.apiCommitSha || observed.apiCommitSha === expectedSha)) break;
    await new Promise((resolve) => setTimeout(resolve, 5_000));
  }
  if (observed?.apiStatus !== 'SUCCESS'
      || observed.apiDeploymentId !== expectedApiDeploymentId
      || (observed.apiCommitSha && observed.apiCommitSha !== expectedSha)) {
    throw new Error(`Exact API revision did not become current: ${JSON.stringify(observed)}`);
  }
  const health = await fetch(`${API_URL}/api/health/live`, { signal: AbortSignal.timeout(15_000) });
  if (!health.ok) throw new Error(`API liveness failed HTTP ${health.status}`);
  const route = await fetch(`${API_URL}/api/trading-agents/conservative-btc/credentials/refresh-paused`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{}',
    signal: AbortSignal.timeout(15_000),
  });
  if (route.status !== 401) {
    throw new Error(`Paused credential route availability proof expected unauthenticated HTTP 401, got ${route.status}`);
  }
  console.log(JSON.stringify({ ok: true, ...observed, apiHealth: health.status, pausedCredentialRoute: route.status }));
}

async function main() {
  const command = process.argv[2];
  if (command === 'prove-paused') return provePausedTarget(process.env.TARGET_INSTANCE_ID?.trim());
  const token = process.env.RAILWAY_TOKEN?.trim();
  if (!token) throw new Error('RAILWAY_TOKEN is required');
  if (command === 'snapshot') return snapshot(token);
  const expectedSha = assertExactSha(process.env.EXPECTED_COMMIT_SHA);
  if (command === 'deploy') return deploy(token, expectedSha);
  if (command === 'verify') {
    const apiId = process.env.EXPECTED_API_DEPLOYMENT_ID?.trim();
    const executorId = process.env.EXPECTED_EXECUTOR_DEPLOYMENT_ID?.trim();
    if (!apiId) throw new Error('EXPECTED_API_DEPLOYMENT_ID is required');
    if (!executorId) throw new Error('EXPECTED_EXECUTOR_DEPLOYMENT_ID is required');
    return verify(token, expectedSha, apiId, executorId);
  }
  throw new Error('Usage: deploy-paused-credential-api.mjs prove-paused|snapshot|deploy|verify');
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(`API-only credential rollout failed: ${error.message}`);
    process.exitCode = 1;
  });
}
