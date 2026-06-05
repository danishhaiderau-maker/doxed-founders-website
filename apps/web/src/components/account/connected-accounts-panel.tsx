'use client';

import Link from 'next/link';
import { signIn, signOut } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { getIntegrationConnectGuide } from '@dcf/utils';
import { IntegrationConnectGuidePanel } from '@/components/integration-connect-guide-panel';
import {
  connectGitHubRepo,
  connectIntegration,
  disconnectIntegration,
  fetchBuilderSettings,
  fetchCopilotMemory,
  fetchFounderOsDashboard,
  fetchIntegrationProviders,
  fetchXConnectionStatus,
  FounderOsDashboard,
  IntegrationProviderConfig,
} from '@/lib/api';

type Props = {
  accessToken: string;
};

export function ConnectedAccountsPanel({ accessToken }: Props) {
  const searchParams = useSearchParams();
  const connectFromUrl = searchParams.get('connect')?.trim().toLowerCase() ?? null;
  const [data, setData] = useState<FounderOsDashboard | null>(null);
  const [providers, setProviders] = useState<IntegrationProviderConfig[]>([]);
  const [repoInput, setRepoInput] = useState('');
  const [linkedRepo, setLinkedRepo] = useState<string | null>(null);
  const [connectProvider, setConnectProvider] = useState<IntegrationProviderConfig | null>(null);
  const [connectFields, setConnectFields] = useState<Record<string, string>>({});
  const [guideProvider, setGuideProvider] = useState<{ key: string; label: string } | null>(null);
  const [expandedGuide, setExpandedGuide] = useState<string | null>(null);
  const [xStatus, setXStatus] = useState<{
    canPostInstantly: boolean;
    tokenExpired?: boolean;
    twitterHandle: string | null;
    message: string;
  } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    const [dashResult, memory, prov, x] = await Promise.all([
      fetchFounderOsDashboard(accessToken)
        .then((value) => ({ ok: true as const, value }))
        .catch((e: unknown) => ({ ok: false as const, error: e })),
      fetchCopilotMemory(accessToken).catch(() => null),
      fetchIntegrationProviders(),
      fetchXConnectionStatus(accessToken).catch(() => null),
    ]);

    setProviders(prov);
    setXStatus(x);
    const repo = memory?.repoFullName ?? null;
    setLinkedRepo(repo);
    if (repo) setRepoInput(repo);

    if (dashResult.ok) {
      setData(dashResult.value);
      return;
    }

    const builder = await fetchBuilderSettings(accessToken).catch(() => null);
    if (builder) {
      const credentialKeys = new Set(
        builder.providers.filter((p) => p.connected && p.credentialProvider).map((p) => p.credentialProvider!),
      );
      setData({
        founderCredits: 0,
        communityRewardPool: 0,
        primaryProject: null,
        connectedApps: prov.map((p) => ({
          provider: p.key,
          label: p.label,
          connected:
            p.key === 'github'
              ? Boolean(repo)
              : p.key === 'cursor'
                ? credentialKeys.has('cursor')
                : credentialKeys.has(p.key),
          reputationBoost: p.reputationBoost,
          billTip: p.billTip,
          accountName: null,
          webhookUrl: null,
        })),
        pendingSuggestions: [],
        openBounties: [],
      });
    }

    const message =
      dashResult.error instanceof Error
        ? dashResult.error.message
        : 'Failed to load connected accounts';
    setErr(message);
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!connectFromUrl || providers.length === 0) return;
    setExpandedGuide(connectFromUrl);
    const el = document.getElementById(`connect-${connectFromUrl}`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [connectFromUrl, providers.length]);

  async function handleConnectGitHub() {
    if (!repoInput.trim()) {
      setErr('Enter GitHub owner/repo (e.g. you/your-repo)');
      return;
    }
    setBusy('github');
    setErr(null);
    try {
      await connectGitHubRepo(repoInput.trim(), accessToken);
      setMsg('GitHub connected');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'GitHub connect failed');
    } finally {
      setBusy(null);
    }
  }

  async function handleConnectProvider() {
    if (!connectProvider) return;
    setBusy(connectProvider.key);
    setErr(null);
    try {
      const payload: { provider: string; token?: string; projectName?: string } = {
        provider: connectProvider.key,
      };
      if (connectProvider.connectType === 'token') {
        payload.token = connectFields.token?.trim();
        payload.projectName = connectFields.projectName?.trim() || undefined;
      }
      const result = await connectIntegration(payload, accessToken);
      setMsg(`${result.accountName} connected`);
      setConnectProvider(null);
      setConnectFields({});
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Connect failed');
    } finally {
      setBusy(null);
    }
  }

  async function handleDisconnect(provider: string) {
    setBusy(`disconnect-${provider}`);
    setErr(null);
    try {
      await disconnectIntegration(provider, accessToken);
      setMsg('Disconnected');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Disconnect failed');
    } finally {
      setBusy(null);
    }
  }

  async function handleToggle(provider: IntegrationProviderConfig) {
    const app = apps.find((a) => a.provider === provider.key);
    if (app?.connected) {
      await handleDisconnect(provider.key);
      return;
    }
    setConnectProvider(provider);
    setConnectFields({});
  }

  const apps = data?.connectedApps ?? [];
  const githubConnected = Boolean(apps.find((a) => a.provider === 'github')?.connected || linkedRepo);
  const xConnected = Boolean(apps.find((a) => a.provider === 'x')?.connected || xStatus?.twitterHandle);

  return (
    <section className="space-y-6">
      <div>
        <p className="text-sm text-zinc-400">
          Connect OAuth, GitHub, deploy stack, and builder AI from one place. Each integration includes setup
          instructions below.
        </p>
        <Link
          href="/settings/builder"
          className="mt-3 inline-flex rounded-lg border border-violet-500/40 px-4 py-2 text-sm text-violet-200 hover:bg-violet-500/10"
        >
          Builder settings (AI · GitHub PAT · Cursor) →
        </Link>
      </div>

      {xStatus?.tokenExpired && (
        <div className="rounded-xl border border-red-500/40 bg-red-950/25 px-4 py-3 text-sm text-red-100">
          {xStatus.message}{' '}
          <button
            type="button"
            onClick={() => signIn('twitter', { callbackUrl: '/account?tab=connected' })}
            className="font-semibold underline hover:text-white"
          >
            Reconnect X
          </button>{' '}
          to restore 1-click Proof of Conviction posting.
        </div>
      )}

      {msg && (
        <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-2 text-sm text-emerald-200">
          {msg}
        </p>
      )}
      {err && (
        <div className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-2 text-sm text-red-300">
          <p>{err}</p>
          {err.includes('session expired') && (
            <button
              type="button"
              onClick={() => signOut({ callbackUrl: '/login?callbackUrl=/account?tab=connected' })}
              className="mt-2 font-semibold text-red-100 underline hover:text-white"
            >
              Sign out and sign in again
            </button>
          )}
        </div>
      )}

      <ul className="space-y-3">
        {apps.map((app) => {
          const provider = providers.find((p) => p.key === app.provider);
          const guide = getIntegrationConnectGuide(app.provider);
          const isExpanded = expandedGuide === app.provider;

          return (
            <li key={app.provider} id={`connect-${app.provider}`} className="rounded-xl border border-zinc-800 bg-zinc-950/50 scroll-mt-24">
              <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-white">{app.label}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                        app.connected ? 'bg-emerald-500/20 text-emerald-300' : 'bg-zinc-800 text-zinc-500'
                      }`}
                    >
                      {app.connected ? app.accountName ?? 'Connected' : 'Not connected'}
                    </span>
                  </div>
                  {provider?.billTip && <p className="mt-1 text-xs text-zinc-500">{provider.billTip}</p>}
                  {app.provider === 'x' && xStatus?.twitterHandle && (
                    <p className="mt-1 text-xs text-zinc-400">@{xStatus.twitterHandle.replace(/^@/, '')}</p>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  {guide && (
                    <button
                      type="button"
                      onClick={() => setExpandedGuide(isExpanded ? null : app.provider)}
                      className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-500"
                    >
                      {isExpanded ? 'Hide guide' : 'How to connect'}
                    </button>
                  )}
                  {app.provider === 'github' && (
                    <button
                      type="button"
                      disabled={busy === 'github'}
                      onClick={handleConnectGitHub}
                      className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                    >
                      {githubConnected ? 'Update repo' : 'Connect'}
                    </button>
                  )}
                  {app.provider === 'x' && (
                    <>
                      {!xConnected || xStatus?.tokenExpired ? (
                        <button
                          type="button"
                          onClick={() => signIn('twitter', { callbackUrl: '/account?tab=connected' })}
                          className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-medium text-white"
                        >
                          {xStatus?.tokenExpired ? 'Reconnect X' : 'Connect X'}
                        </button>
                      ) : (
                        <span className="rounded-lg border border-emerald-500/30 px-3 py-1.5 text-xs text-emerald-300">
                          OAuth linked
                        </span>
                      )}
                    </>
                  )}
                  {provider?.connectType === 'token' && (
                    <>
                      {app.connected ? (
                        <button
                          type="button"
                          disabled={busy === `disconnect-${app.provider}`}
                          onClick={() => handleDisconnect(app.provider)}
                          className="rounded-lg border border-red-500/40 px-3 py-1.5 text-xs text-red-200 hover:bg-red-950/30"
                        >
                          Disconnect
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setConnectProvider(provider);
                            setConnectFields({});
                          }}
                          className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white"
                        >
                          Connect
                        </button>
                      )}
                    </>
                  )}
                  {provider?.connectType === 'toggle' && (
                    <button
                      type="button"
                      disabled={busy === app.provider}
                      onClick={() => handleToggle(provider)}
                      className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                        app.connected
                          ? 'border border-zinc-600 text-zinc-300 hover:border-red-500/40 hover:text-red-200'
                          : 'bg-indigo-600 text-white'
                      }`}
                    >
                      {app.connected ? 'Disable' : 'Enable'}
                    </button>
                  )}
                  {app.provider === 'cursor' && (
                    <Link
                      href="/settings/builder"
                      className="rounded-lg border border-violet-500/40 px-3 py-1.5 text-xs text-violet-200"
                    >
                      API key →
                    </Link>
                  )}
                </div>
              </div>

              {app.provider === 'github' && (
                <div className="border-t border-zinc-800 px-4 py-3">
                  <input
                    value={repoInput}
                    onChange={(e) => setRepoInput(e.target.value)}
                    placeholder="owner/repo"
                    className="w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
                  />
                  {linkedRepo && (
                    <p className="mt-2 text-xs text-emerald-400">
                      Linked:{' '}
                      <a
                        href={`https://github.com/${linkedRepo}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline"
                      >
                        {linkedRepo}
                      </a>
                    </p>
                  )}
                </div>
              )}

              {isExpanded && guide && (
                <div className="border-t border-zinc-800 px-4 py-3 text-xs text-zinc-400">
                  <p className="font-medium text-zinc-200">{guide.summary}</p>
                  <p className="mt-2">
                    <span className="text-emerald-400">Does:</span> {guide.whatItDoes}
                  </p>
                  <p className="mt-1">
                    <span className="text-amber-400">Does not:</span> {guide.whatItDoesNot}
                  </p>
                  <ol className="mt-3 list-decimal space-y-2 pl-4">
                    {guide.steps.map((step) => (
                      <li key={step.title}>
                        <span className="font-medium text-zinc-300">{step.title}</span>
                        <p className="mt-0.5">{step.body}</p>
                        {step.link && (
                          <a href={step.link.href} className="mt-1 inline-block text-violet-300 underline">
                            {step.link.label}
                          </a>
                        )}
                      </li>
                    ))}
                  </ol>
                  {guide.note && <p className="mt-2 text-amber-200/80">{guide.note}</p>}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {connectProvider && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-4 sm:items-center">
          <div className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-zinc-700 bg-zinc-900 p-5">
            <p className="font-semibold text-white">Connect {connectProvider.label}</p>
            <p className="mt-1 text-xs text-zinc-500">{connectProvider.billTip}</p>
            {connectProvider.fields.map((f) => (
              <input
                key={f.key}
                type={f.secret ? 'password' : 'text'}
                value={connectFields[f.key] ?? ''}
                onChange={(e) => setConnectFields({ ...connectFields, [f.key]: e.target.value })}
                placeholder={f.placeholder}
                className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              />
            ))}
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={!!busy}
                onClick={handleConnectProvider}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                Connect
              </button>
              <button
                type="button"
                onClick={() => setConnectProvider(null)}
                className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-400"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {guideProvider && getIntegrationConnectGuide(guideProvider.key) && (
        <IntegrationConnectGuidePanel
          providerLabel={guideProvider.label}
          guide={getIntegrationConnectGuide(guideProvider.key)!}
          onClose={() => setGuideProvider(null)}
        />
      )}
    </section>
  );
}
