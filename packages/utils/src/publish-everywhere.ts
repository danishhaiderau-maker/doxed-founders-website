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

/** System prompt for Social Hub — LLM must return HEADLINE / BODY / X_HOOK blocks. */
export function buildSocialDraftSystemPrompt(codeAgent?: string | null): string {
  const lens =
    codeAgent === 'CURSOR'
      ? 'You interpret the founder’s latest GitHub commits and repo work (as their Cursor coding agent).'
      : codeAgent === 'OPENHANDS'
        ? 'You interpret the founder’s latest repo and builder activity (as their OpenHands coding agent).'
        : 'You are an elite crypto founder marketing writer for build-in-public.';

  return [
    lens,
    'Read the technical context (commits, goals, tasks) and write for non-developers.',
    'Explain WHAT shipped, WHY it matters, WHY it is a big deal, WHO benefits, and what they are building — like a sharp ad, not a changelog.',
    'Use simple English. No jargon unless you immediately explain it.',
    'Be honest — only claim what the context supports.',
    '',
    'Return exactly this format (no extra sections):',
    'HEADLINE: (one punchy line, max 120 chars)',
    'BODY: (2–4 short paragraphs: what shipped, why huge, benefit, what is next)',
    'X_HOOK: (one tweet-sized hook under 220 chars, exciting, no hashtag spam)',
  ].join('\n');
}

export function parseSocialDraftLlmResponse(text: string): {
  headline: string;
  body: string;
  xHook: string;
} {
  const raw = text.trim();
  const headline =
    raw.match(/^HEADLINE:\s*(.+)$/im)?.[1]?.trim() ??
    raw.split('\n').find((l) => l.trim())?.trim() ??
    'Founder update';
  const bodyMatch = raw.match(/^BODY:\s*([\s\S]*?)(?=^X_HOOK:|$)/im);
  const body =
    bodyMatch?.[1]?.trim() ??
    raw.replace(/^HEADLINE:.*$/im, '').replace(/^X_HOOK:.*$/im, '').trim();
  const xHook =
    raw.match(/^X_HOOK:\s*(.+)$/im)?.[1]?.trim() ??
    headline;

  return {
    headline: headline.slice(0, 200),
    body: body.slice(0, 4000),
    xHook: xHook.slice(0, 280),
  };
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
