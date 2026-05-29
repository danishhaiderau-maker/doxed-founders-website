/** Build copy for multi-destination publish (no LLM). */

export type PublishDestinations = {
  buildFeed: boolean;
  x: boolean;
  community: boolean;
};

export const DEFAULT_PUBLISH_DESTINATIONS: PublishDestinations = {
  buildFeed: true,
  x: true,
  community: true,
};

export function buildFeedPostBody(input: {
  body: string;
  devSummary: string;
  traderSummary: string;
}): string {
  return [
    input.body,
    '',
    '---',
    '**Developer view:**',
    input.devSummary,
    '',
    '**Trader view:**',
    input.traderSummary,
  ].join('\n');
}

export function buildXUpdateTweet(input: {
  headline: string;
  traderSummary: string;
  projectName?: string;
}): string {
  const prefix = input.projectName ? `${input.projectName}: ` : '';
  const traderLine = input.traderSummary.split('\n')[0]?.replace(/^✓\s*/, '') ?? input.headline;
  const raw = `${prefix}${input.headline}\n\n${traderLine}\n\n#BuildInPublic`;
  return raw.length <= 280 ? raw : `${prefix}${input.headline}`.slice(0, 276) + '…';
}

export function buildCommunityAnnouncement(input: {
  headline: string;
  body: string;
  traderSummary: string;
}): { title: string; body: string } {
  return {
    title: input.headline,
    body: [input.body, '', '**For traders:**', input.traderSummary].join('\n'),
  };
}

export type PublishChannelResult = {
  buildFeed?: { ok: boolean; buildPostId?: string; error?: string };
  x?: { ok: boolean; tweetUrl?: string; error?: string; skipped?: boolean };
  community?: { ok: boolean; threadId?: string; error?: string; skipped?: boolean };
};
