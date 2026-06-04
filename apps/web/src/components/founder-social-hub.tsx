'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  buildFounderUpdateXText,
  formatRelativeTime,
  type FounderUpdateParsed,
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

type AchievementInsight = {
  aiSummary: string;
  impactLevel: string;
  launchReadinessDelta: number;
  headline: string;
};

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

function insightFromDraft(result: {
  headline: string;
  traderSummary?: string;
  whatShipped?: string;
  impactLevel?: string;
  launchReadinessDelta?: number;
}): AchievementInsight {
  const aiSummary =
    result.whatShipped?.trim() ||
    result.traderSummary?.split('\n')[0]?.trim() ||
    result.headline;
  return {
    headline: result.headline,
    aiSummary,
    impactLevel: result.impactLevel ?? 'MEDIUM',
    launchReadinessDelta: result.launchReadinessDelta ?? 5,
  };
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
  const [tweetVersion, setTweetVersion] = useState('');
  const [parsedUpdate, setParsedUpdate] = useState<Partial<FounderUpdateParsed> | null>(null);
  const [audience, setAudience] = useState<'trader' | 'developer'>('trader');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [draftingKey, setDraftingKey] = useState<string | null>(null);
  const [explainingId, setExplainingId] = useState<string | null>(null);
  const [lastDraftProvider, setLastDraftProvider] = useState<string | null>(null);
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [connections, setConnections] = useState({ cursor: false, openHands: false });
  const [achievementInsights, setAchievementInsights] = useState<Record<string, AchievementInsight>>(
    {},
  );

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
    const items: {
      id: string;
      title: string;
      detail: string;
      when: string;
      kind: string;
    }[] = [];
    const commitCount = buildRoom?.stats.commits ?? 0;
    if (commitCount > 0 && buildRoom?.commits[0]) {
      const latest = buildRoom.commits[0];
      items.push({
        id: 'commits',
        kind: 'commits',
        title: `${commitCount} commit${commitCount === 1 ? '' : 's'} pushed`,
        detail: latest.message.split('\n')[0].slice(0, 120),
        when: formatRelativeTime(latest.date),
      });
    }
    const deploy = buildRoom?.deployments[0];
    if (deploy) {
      items.push({
        id: `deploy-${deploy.id}`,
        kind: 'deploy',
        title: 'Deployment shipped',
        detail: deploy.headline,
        when: formatRelativeTime(deploy.createdAt),
      });
    }
    const post = room?.buildPosts?.[0];
    if (post) {
      items.push({
        id: `post-${post.id}`,
        kind: 'post',
        title: post.headline,
        detail: post.body.slice(0, 120),
        when: formatRelativeTime(post.publishedAt),
      });
    }
    return items.slice(0, 5);
  }, [buildRoom, room]);

  function applyDraftResult(
    result: Awaited<ReturnType<typeof fetchCopilotSocialDraft>>,
    mode: 'trader' | 'developer',
  ) {
    setHeadline(result.headline);
    setBody(result.displayBody ?? result.body);
    setTweetVersion(result.tweetVersion ?? result.xHook);
    setParsedUpdate({
      headline: result.headline,
      whatShipped: result.whatShipped,
      whyItMatters: result.whyItMatters,
      whatUsersNotice: result.whatUsersNotice,
      whatsNext: result.whatsNext,
      developerSummary: result.developerSummary,
      traderSummary: result.traderSummary,
      tweetVersion: result.tweetVersion ?? result.xHook,
      feedVersion: result.feedVersion ?? result.body,
      impactLevel: result.impactLevel,
      launchReadinessDelta: result.launchReadinessDelta,
    });
    setAudience(mode);
  }

  function switchAudience(mode: 'trader' | 'developer') {
    setAudience(mode);
    if (!parsedUpdate) return;
    if (mode === 'developer' && parsedUpdate.developerSummary?.trim()) {
      setBody(parsedUpdate.developerSummary.trim());
    } else if (parsedUpdate.traderSummary?.trim()) {
      setBody(parsedUpdate.traderSummary.trim());
    } else if (parsedUpdate.feedVersion?.trim()) {
      setBody(parsedUpdate.feedVersion.trim());
    }
  }

  async function publishUpdate() {
    if (!headline.trim() || !body.trim()) return;
    setBusy(true);
    try {
      await createBuildPost({ headline: headline.trim(), body: body.trim() }, accessToken);
      setHeadline('');
      setBody('');
      setTweetVersion('');
      setParsedUpdate(null);
      setLastDraftProvider(null);
      onMessage?.('Update published to feed');
      onRefresh();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Publish failed');
    } finally {
      setBusy(false);
    }
  }

  async function draftWithAgent(agent: DraftAgent, opts?: {
    audience?: 'trader' | 'developer';
    achievement?: { id: string; title: string; detail: string; kind: string };
  }) {
    const mode = opts?.audience ?? audience;
    if (opts?.achievement) setExplainingId(opts.achievement.id);
    else setDraftingKey(agent.key);
    setErr(null);
    try {
      await syncGitHubCommits(accessToken).catch(() => undefined);
      const result = await fetchCopilotSocialDraft(agent.key, accessToken, {
        audience: mode,
        achievement: opts?.achievement
          ? {
              title: opts.achievement.title,
              detail: opts.achievement.detail,
              kind: opts.achievement.kind,
            }
          : undefined,
      });
      applyDraftResult(result, mode);
      setLastDraftProvider(agent.label);
      if (opts?.achievement) {
        setAchievementInsights((prev) => ({
          ...prev,
          [opts.achievement!.id]: insightFromDraft(result),
        }));
      }
      onMessage?.(
        result.fallback
          ? `${agent.label}: drafted from project context (connect DeepSeek in AI Stack for richer AI)`
          : opts?.achievement
            ? `${agent.label} explained this milestone in trader language`
            : `${agent.label} generated a founder update from roadmap + GitHub + memory`,
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Draft failed';
      setErr(msg);
      onMessage?.(msg);
    } finally {
      setDraftingKey(null);
      setExplainingId(null);
    }
  }

  const shareUrl = room ? `${origin}/project/${room.slug}` : origin;
  const listingLabel = room?.ticker
    ? `$${room.ticker.replace(/^\$/, '')}`
    : room?.name;

  const xTweetText = parsedUpdate
    ? buildFounderUpdateXText({
        parsed: {
          headline: (headline.trim() || parsedUpdate.headline) ?? 'Founder update',
          whatShipped: parsedUpdate.whatShipped ?? '',
          whyItMatters: parsedUpdate.whyItMatters ?? '',
          whatUsersNotice: parsedUpdate.whatUsersNotice ?? '',
          whatsNext: parsedUpdate.whatsNext ?? '',
          developerSummary: parsedUpdate.developerSummary ?? body,
          traderSummary: parsedUpdate.traderSummary ?? body,
          tweetVersion: (tweetVersion || parsedUpdate.tweetVersion) ?? headline,
          feedVersion: parsedUpdate.feedVersion ?? body,
          impactLevel: parsedUpdate.impactLevel ?? 'MEDIUM',
          launchReadinessDelta: parsedUpdate.launchReadinessDelta ?? 5,
        },
        projectName: listingLabel,
      })
    : headline.trim()
      ? `${listingLabel ? `${listingLabel}: ` : ''}${headline.trim()}`
      : '';

  const canPublish = Boolean(headline.trim() && body.trim());
  const defaultAgent = draftAgents[0];

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header>
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">
          Social Hub
        </p>
        <h1 className="mt-1 text-2xl font-bold text-white">Build in public</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Project context (roadmap, goals, commits, deploys, community) → AI explains what shipped and
          why traders should care — one engine for feed and X.
        </p>
      </header>

      <section className="rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-5">
        <h2 className="text-sm font-semibold text-white">Founder update</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Syncs GitHub, memory, roadmap, tasks, deploys, and community — then DeepSeek (trader voice)
          or Cursor (technical voice) writes impact, not commit counts.
        </p>

        {draftAgents.length > 0 ? (
          <div className="mt-4">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
              Generate founder update
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {draftAgents.map((agent) => (
                <button
                  key={agent.key}
                  type="button"
                  disabled={Boolean(draftingKey) || Boolean(explainingId) || busy}
                  onClick={() => void draftWithAgent(agent)}
                  className={agentButtonClass(agent.kind, draftingKey === agent.key)}
                  title={
                    agent.kind === 'code'
                      ? 'Reads repo, PRs, and roadmap — technical + trader summaries'
                      : 'Doxxed Crypto founder update writer for traders and scouts'
                  }
                >
                  {draftingKey === agent.key ? `${agent.label}…` : agent.label}
                </button>
              ))}
            </div>
            {parsedUpdate && (
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => switchAudience('trader')}
                  className={`rounded-lg border px-3 py-1 text-[10px] font-semibold ${
                    audience === 'trader'
                      ? 'border-violet-400 bg-violet-600/80 text-white'
                      : 'border-zinc-700 text-zinc-400 hover:text-white'
                  }`}
                >
                  Trader version
                </button>
                <button
                  type="button"
                  onClick={() => switchAudience('developer')}
                  className={`rounded-lg border px-3 py-1 text-[10px] font-semibold ${
                    audience === 'developer'
                      ? 'border-emerald-500/50 bg-emerald-950/50 text-emerald-100'
                      : 'border-zinc-700 text-zinc-400 hover:text-white'
                  }`}
                >
                  Developer version
                </button>
              </div>
            )}
            {lastDraftProvider && (
              <p className="mt-2 text-[10px] text-violet-300/90">
                Last draft by <span className="font-semibold">{lastDraftProvider}</span> — edit before
                posting
                {parsedUpdate?.launchReadinessDelta != null && (
                  <>
                    {' '}
                    · Launch readiness{' '}
                    <span className="text-emerald-300">
                      +{parsedUpdate.launchReadinessDelta}%
                    </span>
                    {parsedUpdate.impactLevel ? ` · Impact ${parsedUpdate.impactLevel}` : ''}
                  </>
                )}
              </p>
            )}
          </div>
        ) : (
          <p className="mt-3 text-xs text-amber-200/90">
            Connect at least one LLM (or Cursor / OpenHands) in{' '}
            <Link href={AI_STACK_HREF} className="underline">
              AI Stack
            </Link>{' '}
            to generate updates.
          </p>
        )}

        {err && <p className="mt-2 text-xs text-red-300">{err}</p>}

        <input
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          placeholder="Headline — what product moved forward?"
          className="mt-4 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-white outline-none focus:border-violet-500/50"
        />
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Why it matters, what users notice, what's next…"
          rows={6}
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
            Tap a connected AI to generate a founder update, toggle trader vs developer view, then
            publish or share to X.
          </p>
        )}
      </section>

      {achievements.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-white">Auto-generated achievements</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            From GitHub and recent activity — explain with AI before sharing
          </p>
          <ul className="mt-4 space-y-3">
            {achievements.map((a) => {
              const insight = achievementInsights[a.id];
              const explaining = explainingId === a.id;
              return (
                <li
                  key={a.id}
                  className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4"
                >
                  <p className="text-[10px] uppercase tracking-wider text-zinc-600">{a.when}</p>
                  <p className="mt-1 font-medium text-white">{a.title}</p>
                  <p className="mt-1 text-xs text-zinc-500 line-clamp-2">{a.detail}</p>
                  {insight && (
                    <div className="mt-3 rounded-lg border border-violet-500/20 bg-violet-950/20 p-3 text-xs text-zinc-300">
                      <p className="text-[10px] font-semibold uppercase tracking-wide text-violet-300/90">
                        AI summary
                      </p>
                      <p className="mt-1">{insight.aiSummary}</p>
                      <p className="mt-2 text-[10px] text-zinc-500">
                        Impact: {insight.impactLevel} · Launch readiness: +
                        {insight.launchReadinessDelta}%
                      </p>
                    </div>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {defaultAgent && (
                      <button
                        type="button"
                        disabled={Boolean(draftingKey) || explaining}
                        onClick={() =>
                          void draftWithAgent(defaultAgent, {
                            achievement: {
                              id: a.id,
                              title: a.title,
                              detail: a.detail,
                              kind: a.kind,
                            },
                          })
                        }
                        className="rounded-lg border border-violet-500/40 bg-violet-950/30 px-3 py-1.5 text-[11px] font-semibold text-violet-100 hover:border-violet-400/60 disabled:opacity-50"
                      >
                        {explaining ? 'Explaining…' : 'Explain with AI'}
                      </button>
                    )}
                    <ShareOnXButton
                      text={
                        insight
                          ? buildFounderUpdateXText({
                              parsed: {
                                headline: insight.headline,
                                whatShipped: insight.aiSummary,
                                whyItMatters: '',
                                whatUsersNotice: '',
                                whatsNext: '',
                                developerSummary: insight.aiSummary,
                                traderSummary: insight.aiSummary,
                                tweetVersion: insight.aiSummary.slice(0, 200),
                                feedVersion: insight.aiSummary,
                                impactLevel:
                                  insight.impactLevel === 'HIGH' ||
                                  insight.impactLevel === 'LOW'
                                    ? insight.impactLevel
                                    : 'MEDIUM',
                                launchReadinessDelta: insight.launchReadinessDelta,
                              },
                              projectName: listingLabel,
                            })
                          : `${a.title}\n${a.detail}`
                      }
                      url={shareUrl}
                      label="Share to X"
                      className={!insight ? 'opacity-60' : ''}
                    />
                  </div>
                  {!defaultAgent && (
                    <p className="mt-2 text-[10px] text-zinc-600">
                      Connect AI in AI Stack to explain achievements.
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
