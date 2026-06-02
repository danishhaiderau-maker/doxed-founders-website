/** Normalize X/Twitter handle from URL, @handle, or bare username. */
export function normalizeTwitterHandle(input?: string | null): string | null {
  if (!input?.trim()) return null;
  const s = input.trim();
  const urlMatch = s.match(/(?:https?:\/\/)?(?:www\.)?(?:twitter\.com|x\.com)\/@?([^/?#]+)/i);
  if (urlMatch?.[1]) return urlMatch[1].replace(/^@/, '').toLowerCase();
  return s.replace(/^@/, '').toLowerCase();
}

/** True when the user set a custom trading / display name (not just their email). */
export function hasTradingDisplayName(name?: string | null, email?: string | null): boolean {
  const trimmed = name?.trim();
  if (!trimmed || trimmed.length < 2) return false;
  if (trimmed.includes('@')) return false;
  if (email && trimmed.toLowerCase() === email.trim().toLowerCase()) return false;
  return true;
}

/** Mask email for public UI — first few characters of local part, hide the rest. */
export function maskEmail(email?: string | null): string {
  if (!email?.trim()) return 'Trader';

  const normalized = email.trim().toLowerCase();

  if (normalized.endsWith('@guest.local')) {
    const local = normalized.split('@')[0] ?? '';
    if (local.startsWith('paper-')) {
      return `Guest ${local.slice(6)}`;
    }
    return 'Guest trader';
  }

  const at = normalized.indexOf('@');
  if (at <= 0) {
    return `${normalized.slice(0, Math.min(3, normalized.length))}***`;
  }

  const local = normalized.slice(0, at);
  const domain = normalized.slice(at + 1);
  const visible = Math.min(4, Math.max(2, local.length));
  return `${local.slice(0, visible)}***@${domain}`;
}

/**
 * Public-facing account label: trading name if set, otherwise masked email.
 */
export function formatPublicAccountLabel(
  name?: string | null,
  email?: string | null,
): string {
  if (hasTradingDisplayName(name, email)) {
    return name!.trim();
  }
  return maskEmail(email);
}
