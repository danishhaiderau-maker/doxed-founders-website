export type LinkKind = 'twitter' | 'external';

export interface TextSegment {
  type: 'text' | 'link';
  content: string;
  href?: string;
  linkKind?: LinkKind;
}

const LINK_PATTERN =
  /(?:https?:\/\/)?(?:www\.)?(?:twitter\.com|x\.com)\/[\w@./?=&%-]+|https?:\/\/[^\s<>"']+/gi;

function normalizeHref(raw: string): string {
  if (/^https?:\/\//i.test(raw)) {
    return raw;
  }
  return `https://${raw}`;
}

export function isTwitterUrl(href: string): boolean {
  try {
    const host = new URL(href).hostname.replace(/^www\./, '').toLowerCase();
    return host === 'twitter.com' || host === 'x.com';
  } catch {
    return /(?:twitter\.com|x\.com)/i.test(href);
  }
}

/** Split plain text into text + hyperlink segments (Twitter/X links tagged separately). */
export function linkifyText(text: string): TextSegment[] {
  if (!text.trim()) {
    return [{ type: 'text', content: text }];
  }

  const segments: TextSegment[] = [];
  let lastIndex = 0;
  const re = new RegExp(LINK_PATTERN.source, LINK_PATTERN.flags);
  let match: RegExpExecArray | null;

  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: text.slice(lastIndex, match.index) });
    }

    const matched = match[0];
    const trimmed = matched.replace(/[.,;:!?)]+$/, '');
    const href = normalizeHref(trimmed);

    segments.push({
      type: 'link',
      content: trimmed,
      href,
      linkKind: isTwitterUrl(href) ? 'twitter' : 'external',
    });

    lastIndex = match.index + matched.length;
  }

  if (lastIndex < text.length) {
    segments.push({ type: 'text', content: text.slice(lastIndex) });
  }

  return segments.length > 0 ? segments : [{ type: 'text', content: text }];
}
