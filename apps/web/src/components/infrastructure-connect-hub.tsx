'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  checkPlatformConnectionHealth,
  connectGitHubRepo,
  connectIntegration,
  fetchFounderPlatformConnectionsHub,
  fetchFounderPublishPlan,
  fetchGitHubOAuthStart,
  updatePlatformConnectionToggles,
  type PlatformConnectionsHub,
  type FounderPublishPlanResponse,
} from '@/lib/api';
import { FounderCloudPanel } from '@/components/founder-cloud-panel';

type Props = {
  accessToken: string;
  onMessage?: (msg: string) => void;
};

export function InfrastructureConnectHub({ accessToken, onMessage }: Props) {
  const [hub, setHub] = useState<PlatformConnectionsHub | null>(null);
  const [publishPlan, setPublishPlan] = useState<FounderPublishPlanResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [projectNames, setProjectNames] = useState<Record<string, string>>({});
  const [githubRepo, setGithubRepo] = useState('');

  const load = useCallback(async () => {
    try {
      const [hubRes, planRes] = await Promise.all([
        fetchFounderPlatformConnectionsHub(accessToken),
        fetchFounderPublishPlan(accessToken).catch(() => null),
      ]);
      setHub(hubRes);
      setPublishPlan(planRes);
      setErr(null);
    } catch (e) {
      setHub(null);
      setErr(e instanceof Error ? e.message : 'Could not load connect hub');
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  async function connectTokenProvider(key: string) {
    const token = tokens[key]?.trim();
    if (!token) {
      setErr('API token required');
      return;
    }
    setBusy(`connect-${key}`);
    setErr(null);
    try {
      const result = await connectIntegration(
        { provider: key, token, projectName: projectNames[key]?.trim() || undefined },
        accessToken,
      );
      onMessage?.(`${key} connected${result.accountName ? `: ${result.accountName}` : ''}`);
      setTokens((prev) => ({ ...prev, [key]: '' }));
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Connect failed');
    } finally {
      setBusy(null);
    }
  }

  async function connectGithub() {
    if (!githubRepo.trim()) {
      setErr('Enter owner/repo');
      return;
    }
    setBusy('connect-github');
    setErr(null);
    try {
      await connectGitHubRepo(githubRepo.trim(), accessToken);
      onMessage?.('GitHub repo connected');
      setGithubRepo('');
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'GitHub connect failed');
    } finally {
      setBusy(null);
    }
  }

  async function toggle(
    provider: string,
    field: 'publish' | 'syncBack' | 'aiContext',
    value: boolean,
  ) {
    setBusy(`toggle-${provider}-${field}`);
    try {
      await updatePlatformConnectionToggles({ [field]: value }, accessToken, provider);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save toggle');
    } finally {
      setBusy(null);
    }
  }

  async function healthCheck(provider: string) {
    setBusy(`health-${provider}`);
    try {
      const result = await checkPlatformConnectionHealth(accessToken, provider);
      onMessage?.(`${provider}: ${result.detail}`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Health check failed');
    } finally {
      setBusy(null);
    }
  }

  if (!hub) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-sm text-zinc-500">
        {err ?? 'Loading infrastructure connect hub…'}
      </div>
    );
  }

  return (
    <section className="space-y-4 rounded-xl border border-cyan-500/20 bg-gradient-to-br from-cyan-950/15 to-zinc-950/80 p-4">
      <header>
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-400">
          Infrastructure connect hub
        </p>
        <h3 className="mt-1 text-base font-semibold text-white">Memory · Compute · Publish</h3>
        <p className="mt-1 text-xs text-zinc-500">
          Connect hosts and set per-destination toggles. Recommendations only — nothing is enforced.
        </p>
      </header>

      {err && <p className="text-sm text-red-300">{err}</p>}

      <div className="space-y-3">
        {hub.providers.map((p) => (
          <div
            key={p.key}
            className={`rounded-lg border p-3 ${
              p.connected ? 'border-emerald-600/30 bg-emerald-950/10' : 'border-zinc-800 bg-black/20'
            }`}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-medium text-white">{p.label}</p>
                <p className="text-[11px] text-zinc-500">{p.description}</p>
                {p.connected && p.accountName && (
                  <p className="mt-1 text-xs text-emerald-300">{p.accountName}</p>
                )}
                {p.health.detail && (
                  <p className={`mt-1 text-[10px] ${p.health.ok ? 'text-emerald-400/80' : 'text-zinc-500'}`}>
                    Health: {p.health.detail}
                  </p>
                )}
              </div>
              <button
                type="button"
                disabled={Boolean(busy)}
                onClick={() => void healthCheck(p.key)}
                className="rounded border border-zinc-700 px-2 py-1 text-[10px] text-zinc-400 hover:text-white disabled:opacity-50"
              >
                {busy === `health-${p.key}` ? 'Checking…' : 'Check health'}
              </button>
            </div>

            {!p.connected && p.key === 'github' && (
              <div className="mt-3 flex flex-wrap gap-2">
                <input
                  value={githubRepo}
                  onChange={(e) => setGithubRepo(e.target.value)}
                  placeholder="owner/repo"
                  className="min-w-[10rem] flex-1 rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs"
                />
                <button
                  type="button"
                  disabled={busy === 'connect-github'}
                  onClick={() => void connectGithub()}
                  className="rounded bg-zinc-100 px-3 py-1.5 text-xs font-medium text-black disabled:opacity-50"
                >
                  Connect repo
                </button>
                <button
                  type="button"
                  disabled={Boolean(busy)}
                  onClick={async () => {
                    setBusy('github-oauth');
                    try {
                      const { url } = await fetchGitHubOAuthStart(accessToken);
                      window.location.href = url;
                    } catch (e) {
                      setErr(e instanceof Error ? e.message : 'OAuth unavailable');
                      setBusy(null);
                    }
                  }}
                  className="rounded border border-zinc-600 px-3 py-1.5 text-xs text-zinc-300"
                >
                  OAuth
                </button>
              </div>
            )}

            {!p.connected && p.key === 'founder_node' && (
              <p className="mt-3 text-xs text-zinc-400">
                Install and pair Founder Node →{' '}
                <Link href="/founder-node" className="text-cyan-400 underline">
                  setup guide
                </Link>
              </p>
            )}

            {!p.connected && p.connectType === 'token' && (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <input
                  type="password"
                  value={tokens[p.key] ?? ''}
                  onChange={(e) => setTokens((prev) => ({ ...prev, [p.key]: e.target.value }))}
                  placeholder="API token"
                  className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs"
                />
                <input
                  value={projectNames[p.key] ?? ''}
                  onChange={(e) => setProjectNames((prev) => ({ ...prev, [p.key]: e.target.value }))}
                  placeholder="Project / service name (optional)"
                  className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs"
                />
                <button
                  type="button"
                  disabled={busy === `connect-${p.key}`}
                  onClick={() => void connectTokenProvider(p.key)}
                  className="sm:col-span-2 rounded bg-cyan-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                >
                  {busy === `connect-${p.key}` ? 'Connecting…' : `Connect ${p.label}`}
                </button>
              </div>
            )}

            {p.connected && (
              <div className="mt-3 flex flex-wrap gap-3 border-t border-zinc-800/80 pt-3">
                {(
                  [
                    ['publish', 'Publish outbound'],
                    ['syncBack', 'Sync back'],
                    ['aiContext', 'AI context'],
                  ] as const
                ).map(([field, label]) => (
                  <label key={field} className="flex cursor-pointer items-center gap-2 text-[11px] text-zinc-400">
                    <input
                      type="checkbox"
                      checked={p.toggles[field]}
                      disabled={Boolean(busy)}
                      onChange={(e) => void toggle(p.key, field, e.target.checked)}
                      className="rounded border-zinc-600"
                    />
                    {label}
                  </label>
                ))}
              </div>
            )}

            {p.webhookUrl && p.connected && (
              <p className="mt-2 font-mono text-[10px] text-zinc-600">Webhook: {p.webhookUrl}</p>
            )}
          </div>
        ))}
      </div>

      {publishPlan ? (
        <div className="mt-6 rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Publish pipeline</p>
          <p className="mt-2 text-sm text-zinc-400">
            Social: feed {publishPlan.social.buildFeed ? '✓' : '○'} · X {publishPlan.social.x ? '✓' : '○'} ·
            community {publishPlan.social.community ? '✓' : '○'}
          </p>
          {publishPlan.hostRedeployProviders.length > 0 ? (
            <p className="mt-1 text-xs text-violet-300">
              Host redeploy toggles: {publishPlan.hostRedeployProviders.join(', ')}
            </p>
          ) : null}
          {publishPlan.notes.map((note) => (
            <p key={note} className="mt-1 text-[11px] text-zinc-600">
              {note}
            </p>
          ))}
        </div>
      ) : null}

      <div className="mt-6">
        <FounderCloudPanel accessToken={accessToken} showImport />
      </div>
    </section>
  );
}
