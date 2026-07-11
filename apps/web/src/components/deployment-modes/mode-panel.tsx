'use client';

import { useCallback, useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { cn, DEPLOYMENT_MODES, getDeploymentModeMeta, type DeploymentModeId } from '@dcf/utils';
import {
  fetchDeploymentMode,
  patchDeploymentMode,
  publishDeployment,
  type DeploymentModeState,
} from '@/lib/api';
import { PublishProgress } from './publish-progress';

/**
 * Full deployment-mode panel (doc §3). Three sections:
 *   1. "What's running right now" — runtime status block.
 *   2. "Switch mode" — radio to flip between Private / Hybrid / Public.
 *   3. "Publish to public cloud" — Hybrid → Public promotion trigger + progress.
 *
 * Renders as an overlay modal. The dashboard header badge opens it.
 */
export function DeploymentModePanel({
  slug,
  open,
  onClose,
}: {
  slug: string;
  open: boolean;
  onClose: () => void;
}) {
  const { data: session } = useSession();
  const [state, setState] = useState<DeploymentModeState | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await fetchDeploymentMode(slug, session?.accessToken);
      setState(next);
      if (next.latestPublishJob && (next.latestPublishJob.status === 'PENDING' || next.latestPublishJob.status === 'RUNNING')) {
        setActiveJobId(next.latestPublishJob.id);
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Could not load deployment mode');
    }
  }, [slug, session?.accessToken]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  async function handleSwitchMode(mode: DeploymentModeId) {
    if (!session?.accessToken) return;
    setLoading(true);
    setMsg(null);
    try {
      const next = await patchDeploymentMode(slug, { mode }, session.accessToken);
      setState(next);
      setMsg(`Switched to ${mode} mode`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Could not switch mode');
    } finally {
      setLoading(false);
    }
  }

  async function handlePublish() {
    if (!session?.accessToken || !state) return;
    setPublishing(true);
    setMsg(null);
    try {
      const job = await publishDeployment(slug, {}, session.accessToken);
      if (job) setActiveJobId(job.id);
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Publish failed to start');
    } finally {
      setPublishing(false);
    }
  }

  if (!open) return null;
  const mode = state?.mode ?? 'HYBRID';
  const meta = getDeploymentModeMeta(mode);
  const config = state?.config ?? null;
  const published = state?.latestPublishJob?.status === 'COMPLETED';
  const inFlight = activeJobId !== null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 sm:p-8">
      <div className="w-full max-w-3xl rounded-2xl border border-zinc-800 bg-zinc-950 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-zinc-800 px-6 py-4">
          <div>
            <p className="text-xs uppercase tracking-widest text-zinc-500">Deployment Mode</p>
            <h2 className="text-lg font-semibold text-white">
              {slug}{' '}
              <span className="ml-2 text-zinc-400">
                {meta.emoji} {meta.label}
                {!published && mode === 'HYBRID' && (
                  <span className="text-violet-300/80"> · not yet published</span>
                )}
              </span>
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg border border-zinc-700 px-3 py-1 text-sm text-zinc-300 hover:bg-zinc-800"
          >
            ×
          </button>
        </div>

        <div className="space-y-6 px-6 py-5">
          {msg && (
            <p className="rounded-lg border border-zinc-700 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-200">
              {msg}
            </p>
          )}

          {inFlight ? (
            <PublishProgress
              slug={slug}
              token={session?.accessToken}
              jobId={activeJobId ?? undefined}
              onCompleted={() => {
                setActiveJobId(null);
                void load();
              }}
              onFailed={() => {
                setActiveJobId(null);
                void load();
              }}
            />
          ) : null}

          {/* Section 1 — What's running right now */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
              What&apos;s running right now
            </h3>
            <div className="mt-3 overflow-hidden rounded-xl border border-zinc-800">
              <RuntimeRow
                label="Git"
                value={
                  config
                    ? `${config.gitBackend} @ ${config.gitUrl ?? 'not initialized'}`
                    : 'Not initialized'
                }
                status={config?.forgejoOnline ? 'online' : 'idle'}
              />
              <RuntimeRow
                label="Database"
                value={
                  config
                    ? `${config.dbProvider === 'sqlite' ? 'SQLite' : 'Postgres'} (${truncateForDisplay(config.dbUrl)})`
                    : 'Not initialized'
                }
                status={config?.sqlitePresent ? 'online' : 'idle'}
              />
              <RuntimeRow
                label="Hosting"
                value={
                  config?.hostingUrl
                    ? config.hostingUrl
                    : `${config?.hostingType ?? 'tunnel-on-demand'} (off)`
                }
                status={config?.tunnelActive ? 'online' : 'idle'}
              />
              <RuntimeRow
                label="Phone"
                value={config ? capitalize(config.phoneRoute) + ' direct' : '—'}
                status={config?.tailscaleReady ? 'online' : 'idle'}
              />
              <RuntimeRow
                label="AI"
                value="Founder OS Gateway"
                status="online"
              />
            </div>
          </section>

          {/* Section 2 — Switch mode */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
              Switch mode
            </h3>
            <div className="mt-3 space-y-2">
              {DEPLOYMENT_MODES.map((m) => (
                <label
                  key={m.id}
                  className={cn(
                    'flex cursor-pointer items-center gap-3 rounded-lg border px-4 py-3 text-sm transition',
                    mode === m.id
                      ? 'border-violet-500/50 bg-violet-950/20'
                      : 'border-zinc-800 hover:border-zinc-600',
                  )}
                >
                  <input
                    type="radio"
                    name="deployment-mode"
                    checked={mode === m.id}
                    disabled={loading || publishing}
                    onChange={() => handleSwitchMode(m.id)}
                    className="h-4 w-4 accent-violet-500"
                  />
                  <span className="text-base" aria-hidden>
                    {m.emoji}
                  </span>
                  <span className="font-medium text-white">{m.label}</span>
                  <span className="text-zinc-400">— {modeCostLine(m.id)}</span>
                  {m.recommended && (
                    <span className="ml-auto text-[11px] text-violet-300/80">Recommended</span>
                  )}
                </label>
              ))}
            </div>
          </section>

          {/* Section 3 — Publish to public cloud */}
          {mode !== 'PUBLIC' && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
                Publish to public cloud
              </h3>
              <div className="mt-3 rounded-xl border border-zinc-800 bg-zinc-900/30 p-4">
                <ul className="space-y-1.5 text-sm text-zinc-300">
                  <li className="flex gap-2"><span className="text-emerald-400">→</span> Mirrors your Forgejo repo to GitHub</li>
                  <li className="flex gap-2"><span className="text-emerald-400">→</span> Migrates SQLite → Neon Postgres</li>
                  <li className="flex gap-2"><span className="text-emerald-400">→</span> Deploys to Vercel</li>
                  <li className="flex gap-2"><span className="text-emerald-400">→</span> Assigns your domain</li>
                </ul>
                <p className="mt-3 text-xs text-zinc-400">
                  Your code, history, and data all move with you. Nothing is rewritten.
                </p>

                {config?.publishPlan && (
                  <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2 text-xs text-zinc-400">
                    <span className="text-zinc-500">Target:</span>{' '}
                    {config.publishPlan.targetGithubRepo} →{' '}
                    {config.publishPlan.targetDomain}
                  </div>
                )}

                <button
                  type="button"
                  disabled={publishing || inFlight}
                  onClick={handlePublish}
                  className="mt-4 w-full rounded-lg border border-violet-500/60 bg-violet-600/20 px-4 py-2.5 text-sm font-semibold text-violet-100 transition hover:bg-violet-600/30 disabled:opacity-50"
                >
                  {publishing ? 'Starting publish…' : `🚀 Publish ${slug}`}
                </button>
              </div>
            </section>
          )}

          {mode === 'PUBLIC' && published && state?.latestPublishJob?.liveUrl && (
            <section>
              <a
                href={state.latestPublishJob.liveUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block rounded-lg border border-emerald-500/50 px-4 py-2 text-sm text-emerald-200 hover:bg-emerald-900/30"
              >
                Live at {state.latestPublishJob.liveUrl} ↗
              </a>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function RuntimeRow({
  label,
  value,
  status,
}: {
  label: string;
  value: string;
  status: 'online' | 'idle' | 'offline';
}) {
  const dot =
    status === 'online'
      ? 'bg-emerald-400'
      : status === 'idle'
        ? 'bg-zinc-600'
        : 'bg-red-400';
  const statusLabel = status === 'online' ? 'online' : status === 'idle' ? 'on demand' : 'offline';
  return (
    <div className="flex items-center justify-between border-t border-zinc-800 px-4 py-3 text-sm first:border-t-0">
      <div className="flex items-center gap-3">
        <span className="w-16 text-xs uppercase text-zinc-500">{label}</span>
        <span className="text-zinc-200">{value}</span>
      </div>
      <span className="flex items-center gap-1.5 text-xs text-zinc-400">
        <span className={cn('inline-block h-2 w-2 rounded-full', dot)} />
        {statusLabel}
      </span>
    </div>
  );
}

function modeCostLine(mode: DeploymentModeId): string {
  if (mode === 'PRIVATE') return 'everything on laptop, $0/mo';
  if (mode === 'PUBLIC') return 'flip to full cloud now';
  return 'private now, publish when ready';
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function truncateForDisplay(s: string | null): string {
  if (!s) return 'dev.db';
  if (s.startsWith('file:')) {
    const parts = s.split('/');
    return parts[parts.length - 1] || 'dev.db';
  }
  // Don't render raw connection strings in the UI.
  return 'connected';
}
