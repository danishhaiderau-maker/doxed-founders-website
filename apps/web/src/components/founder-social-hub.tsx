'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  buildFeedShareMessage,
  buildXUpdateTweet,
  formatRelativeTime,
} from '@dcf/utils';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import {
  AI_STACK_HREF,
  shortProviderName,
  type ProviderRow,
} from '@/lib/copilot-ai-stack';
import {
  BuildRoomData,
  createBuildPost,
  fetchBuilderSettings,
  fetchBuilderWorkerStatus,
  fetchCopilotSocialDraft,
  syncGitHubCommits,
  ProjectRoom,
} from '@/lib/api';

type DraftAgent = { key: string; label: string; kind: 'brain' | 'code' };

type FounderSocialHubProps = {
  accessToken: string;
  room: ProjectRoom | null;
  buildRoom: BuildRoomData | null;
  onRefresh: () => void;
  onMessage?: (msg: string) => void;
};

function listDraftAgents(
  providers: ProviderRow[],
  connections: { cursor: boolean; openHands: boolean },
): DraftAgent[] {
  const out: DraftAgent[] = [];
  for (const p of providers) {
    if (
      p.connected &&
      p.key !== 'RULE_BASED' &&
      p.key !== 'CURSOR' &&
      p.key !== 'OPENHANDS' &&
      (p.connectMode === 'api_key' || p.connectMode === 'founder_node')
    ) {
      out.push({ key: p.key, label: shortProviderName(p), kind: 'brain' });
    }
  }
  if (connections.cursor) out.push({ key: 'CURSOR', label: 'Cursor', kind: 'code' });
  if (connections.openHands) out.push({ key: 'OPENHANDS', label: 'OpenHands', kind: 'code' });
  return out;
}

function agentButtonClass(kind: DraftAgent['kind'], active: boolean) {
  const base =
    'rounded-lg border px-3 py-1.5 text-[11px] font-semibold transition disabled:opacity-50';
  if (active) {
    return `${base} border-violet-400 bg-violet-600 text-white`;
  }
  return kind === 'code'
    ? `${base} border-emerald-500/40 bg-emerald-950/30 text-emerald-100 hover:border-emerald-400/60`
    : `${base} border-sky-500/40 bg-sky-950/30 text-sky-100 hover:border-sky-400/60`;
}

export function FounderSocialHub({
  accessToken,
  room,
  buildRoom,
  onRefresh,
  onMessage,
}: FounderSocialHubProps) {
  const origin = useShareOrigin();
  const [headline, setHeadline] = useState('');
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [draftingKey, setDraftingKey] = useState<string | null>(null);
  const [lastDraftProvider, setLastDraftProvider] = useState<string | null>(null);
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [connections, setConnections] = useState({ cursor: false, openHands: false });

  const loadAiStack = useCallback(async () => {
    try {
      const [settings, worker] = await Promise.all([
        fetchBuilderSettings(accessToken),
        fetchBuilderWorkerStatus(accessToken).catch(() => null),
      ]);
      setProviders(settings.providers as ProviderRow[]);
      setConnections({
        cursor: worker?.connections.cursor ?? false,
        openHands: worker?.connections.openHands ?? false,
      });
    } catch {
      setProviders([]);
    }
  }, [accessToken]);

  useEffect(() => {
    void loadAiStack();
  }, [loadAiStack]);

  const draftAgents = useMemo(
    () => listDraftAgents(providers, connections),
    [providers, connections],
  );

  const achievements = useMemo(() => {
    const items: { id: string; title: string; detail: string; when: string }[] = [];
    const commitCount = buildRoom?.stats.commits ?? 0;
    if (commitCount > 0 && buildRoom?.commits[0]) {
      const latest = buildRoom.commits[0];
      items.push({
        id: 'commits',
        title: `${commitCount} commit${commitCount === 1 ? '' : 's'} pushed`,
        detail: latest.message.split('\n')[0].slice(0, 120),
        when: formatRelativeTime(latest.date),
      });
    }
    const deploy = buildRoom?.deployments[0];
    if (deploy) {
      items.push({
        id: `deploy-${deploy.id}`,
        title: 'Deployment shipped',
        detail: deploy.headline,
        when: formatRelativeTime(deploy.createdAt),
      });
    }
    const post = room?.buildPosts?.[0];
    if (post) {
      items.push({
        id: `post-${post.id}`,
        title: post.headline,
        detail: post.body.slice(0, 120),
        when: formatRelativeTime(post.publishedAt),
      });
    }
    return items.slice(0, 5);
  }, [buildRoom, room]);

  async function publishUpdate() {
    if (!headline.trim() || !body.trim()) return;
    setBusy(true);
    try {
      await createBuildPost({ headline: headline.trim(), body: body.trim() }, accessToken);
      setHeadline('');
      setBody('');
      setLastDraftProvider(null);
      onMessage?.('Update published to feed');
      onRefresh();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Publish failed');
    } finally {
      setBusy(false);
    }
  }

  async function draftWithAgent(agent: DraftAgent) {
    setDraftingKey(agent.key);
    setErr(null);
    try {
      await syncGitHubCommits(accessToken).catch(() => undefined);
      const result = await fetchCopilotSocialDraft(agent.key, accessToken);
      setHeadline(result.headline);
      setBody(result.body);
      setLastDraftProvider(agent.label);
      onMessage?.(
        result.fallback
          ? `${agent.label}: drafted from your last 24h of GitHub commits (connect DeepSeek in AI Stack for richer AI copy)`
          : `${agent.label} drafted from live GitHub + memory — edit and publish`,
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Draft failed';
      setErr(msg);
      onMessage?.(msg);
    } finally {
      setDraftingKey(null);
    }
  }

  const shareUrl = room ? `${origin}/project/${room.slug}` : origin;
  const listingLabel = room?.ticker
    ? `$${room.ticker.replace(/^\$/, '')}`
    : room?.name;
  const xTweetText = buildXUpdateTweet({
    headline: headline.trim() || 'Founder update',
    traderSummary: body.trim().split('\n')[0] ?? '',
    projectName: listingLabel,
  });
  const canPublish = Boolean(headline.trim() && body.trim());

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header>
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">
          Social Hub
        </p>
        <h1 className="mt-1 text-2xl font-bold text-white">Build in public</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Scan today&apos;s commits and goals — any connected AI turns code into plain-English hype for
          feed, X, and community.
        </p>
      </header>

      <section className="rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-5">
        <h2 className="text-sm font-semibold text-white">Founder update</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Syncs GitHub, memory, and deploy checks for the last 24 hours — then DeepSeek or Cursor writes
          a trader-friendly post (admin platform footer appended when configured).
        </p>

        {draftAgents.length > 0 ? (
          <div className="mt-4">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
              Draft with your connected AI
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {draftAgents.map((agent) => (
                <button
                  key={agent.key}
                  type="button"
                  disabled={Boolean(draftingKey) || busy}
                  onClick={() => void draftWithAgent(agent)}
                  className={agentButtonClass(agent.kind, draftingKey === agent.key)}
                  title={
                    agent.kind === 'code'
                      ? 'Reads repo/commits — explains in simple language'
                      : 'Marketing-style build-in-public draft'
                  }
                >
                  {draftingKey === agent.key ? `${agent.label}…` : agent.label}
                </button>
              ))}
            </div>
            {lastDraftProvider && (
              <p className="mt-2 text-[10px] text-violet-300/90">
                Last draft by <span className="font-semibold">{lastDraftProvider}</span> — edit before
                posting
              </p>
            )}
          </div>
        ) : (
          <p className="mt-3 text-xs text-amber-200/90">
            Connect at least one LLM (or Cursor / OpenHands) in{' '}
            <Link href={AI_STACK_HREF} className="underline">
              AI Stack
            </Link>{' '}
            to auto-draft posts.
          </p>
        )}

        {err && <p className="mt-2 text-xs text-red-300">{err}</p>}

        <input
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          placeholder="Headline — what did you ship?"
          className="mt-4 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-white outline-none focus:border-violet-500/50"
        />
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Details — why it's huge, who benefits, what's next…"
          rows={5}
          className="mt-2 w-full resize-none rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-white outline-none focus:border-violet-500/50"
        />
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || !canPublish}
            onClick={() => void publishUpdate()}
            className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
          >
            Share to Feed
          </button>
          <ShareOnXButton
            text={xTweetText}
            url={shareUrl}
            label="Share to X"
            className={!canPublish ? 'pointer-events-none opacity-40' : ''}
          />
          {room && (
            <a
              href={`/project/${room.slug}?channel=ANNOUNCEMENTS`}
              className="rounded-lg border border-zinc-700 px-4 py-2 text-xs text-zinc-300 hover:border-violet-500/50 hover:text-white"
            >
              Share to Community
            </a>
          )}
        </div>
        {!canPublish && draftAgents.length > 0 && (
          <p className="mt-2 text-[10px] text-zinc-600">
            Tip: tap a connected AI above to fill headline and body, then publish or share to X.
          </p>
        )}
      </section>

      {achievements.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-white">Auto-generated achievements</h2>
          <p className="mt-0.5 text-xs text-zinc-500">From GitHub and recent activity — approve to publish</p>
          <ul className="mt-4 space-y-3">
            {achievements.map((a) => {
              const shareText = buildFeedShareMessage({ headline: a.title, detail: a.detail });
              return (
                <li
                  key={a.id}
                  className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4"
                >
                  <p className="text-[10px] uppercase tracking-wider text-zinc-600">{a.when}</p>
                  <p className="mt-1 font-medium text-white">{a.title}</p>
                  <p className="mt-1 text-xs text-zinc-500 line-clamp-2">{a.detail}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <ShareOnXButton text={shareText} url={shareUrl} label="Share to X" />
                    <button
                      type="button"
                      onClick={() => {
                        setHeadline(a.title);
                        setBody(a.detail);
                      }}
                      className="rounded-lg border border-zinc-700 px-3 py-1.5 text-[11px] text-zinc-300 hover:text-white"
                    >
                      Use as draft
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
