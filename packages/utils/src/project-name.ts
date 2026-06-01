/** Normalize a human project name — never store raw URLs as display names. */
export function normalizeProjectName(input: string): string {
  let name = input.trim().replace(/\s+/g, ' ');
  if (!name) return '';

  if (/^https?:\/\//i.test(name)) {
    try {
      const url = new URL(name);
      const segments = url.pathname.split('/').filter(Boolean);
      const last = segments[segments.length - 1];
      if (last && !/^[a-f0-9-]{8,}$/i.test(last)) {
        name = decodeURIComponent(last).replace(/[-_]+/g, ' ');
      } else {
        name = url.hostname.replace(/^www\./, '').split('.')[0] ?? name;
      }
    } catch {
      name = name.replace(/^https?:\/\/(www\.)?/i, '').split('/')[0] ?? name;
    }
  }

  name = name
    .replace(/^@+/, '')
    .replace(/\.(com|io|app|xyz|dev|co|net|org)$/i, '')
    .replace(/[-_]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');

  if (name.length > 120) name = name.slice(0, 120).trim();
  if (name.length >= 2) {
    name = name.charAt(0).toUpperCase() + name.slice(1);
  }
  return name;
}

export function projectTickerFromName(name: string): string {
  const normalized = normalizeProjectName(name);
  const fromWords = normalized
    .split(/\s+/)
    .map((w) => w.replace(/[^A-Za-z0-9]/g, ''))
    .filter(Boolean)
    .map((w) => w[0])
    .join('')
    .toUpperCase();
  const compact = normalized.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
  return (fromWords.length >= 2 ? fromWords : compact).slice(0, 6) || 'IDEA';
}
