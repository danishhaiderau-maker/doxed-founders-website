'use client';

import { useMemo, useState } from 'react';
import {
  buildFeedShareMessage,
  buildXUpdateTweet,
  formatRelativeTime,
} from '@dcf/utils';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import { BuildRoomData, createBuildPost, ProjectRoom } from '@/lib/api';

type FounderSocialHubProps = {
  accessToken: string;
  room: ProjectRoom | null;
  buildRoom: BuildRoomData | null;
  onRefresh: () => void;
  onMessage?: (msg: string) => void;
};

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
      onMessage?.('Update published to feed');
      onRefresh();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Publish failed');
    } finally {
      setBusy(false);
    }
  }

  const shareUrl = room ? `${origin}/project/${room.slug}` : origin;

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header>
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">
          Social Hub
        </p>
        <h1 className="mt-1 text-2xl font-bold text-white">Build in public</h1>
        <p className="mt-1 text-sm text-zinc-500">
          One place to publish updates — feed, X, and community.
        </p>
      </header>

      <section className="rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-5">
        <h2 className="text-sm font-semibold text-white">Founder update</h2>
        <input
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          placeholder="Headline — what did you ship?"
          className="mt-3 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-white outline-none focus:border-violet-500/50"
        />
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Details — features, metrics, what's next…"
          rows={5}
          className="mt-2 w-full resize-none rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-white outline-none focus:border-violet-500/50"
        />
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || !headline.trim() || !body.trim()}
            onClick={() => void publishUpdate()}
            className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
          >
            Share to Feed
          </button>
          {headline.trim() && (
            <ShareOnXButton
              text={buildXUpdateTweet({
                headline: headline.trim(),
                traderSummary: body.trim().split('\n')[0] ?? '',
                projectName: room?.name,
              })}
              url={shareUrl}
              label="Share to X"
            />
          )}
          {room && (
            <a
              href={`/project/${room.slug}?channel=ANNOUNCEMENTS`}
              className="rounded-lg border border-zinc-700 px-4 py-2 text-xs text-zinc-300 hover:border-violet-500/50 hover:text-white"
            >
              Share to Community
            </a>
          )}
        </div>
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
