/** Unique public handles — animals, birds, reptiles, countries. Reserved terms blocked. */

const ANIMALS = [
  'Aardvark', 'Badger', 'Bison', 'Cheetah', 'Dolphin', 'Eagle', 'Falcon', 'Gecko',
  'Heron', 'Ibis', 'Jaguar', 'Kestrel', 'Lynx', 'Marten', 'Newt', 'Osprey', 'Panda',
  'Quail', 'Raven', 'Stoat', 'Toucan', 'Urchin', 'Viper', 'Walrus', 'Yak', 'Zebra',
  'Cobra', 'Drake', 'Egret', 'Finch', 'Gull', 'Hawk', 'Iguana', 'Jackal', 'Koala',
  'Lemur', 'Moose', 'Narwhal', 'Otter', 'Puffin', 'Quokka', 'Robin', 'Seal',
] as const;

const BIRDS = [
  'Albatross', 'Bluebird', 'Canary', 'Dove', 'Emberwing', 'Flamingo', 'Goldfinch',
  'Harrier', 'Kingfisher', 'Lark', 'Magpie', 'Nightingale', 'Oriole', 'Pelican',
  'Redstart', 'Swallow', 'Tern', 'Warbler',
] as const;

const REPTILES = [
  'Anaconda', 'Basilisk', 'Chameleon', 'Dragon', 'Eyelash', 'Frilled', 'Gila',
  'Horned', 'Iguana', 'Komodo', 'Leopard', 'Monitor', 'Nile', 'Python', 'Rattler',
] as const;

const COUNTRIES = [
  'Argentina', 'Brazil', 'Canada', 'Denmark', 'Egypt', 'Finland', 'Ghana', 'Honduras',
  'Iceland', 'Japan', 'Kenya', 'Laos', 'Mexico', 'Norway', 'Oman', 'Peru', 'Qatar',
  'Rwanda', 'Spain', 'Turkey', 'Uruguay', 'Vietnam', 'Wales', 'Zambia',
] as const;

const RESERVED_PATTERNS = [
  /\bdoxxed\b/i,
  /\bdoxed\b/i,
  /\bdoxxed\s*crypto\b/i,
  /\bdoxed\s*crypto\b/i,
  /\bplatform\s*admin\b/i,
  /\bplatform\s*operator\b/i,
  /\bfounder\s*admin\b/i,
  /\blisting\s*admin\b/i,
  /\badmin\b/i,
  /\bmoderator\b/i,
  /\bofficial\b/i,
  /\bsupport\s*team\b/i,
  /\bverified\s*staff\b/i,
];

const CREATURE_POOL = [...ANIMALS, ...BIRDS, ...REPTILES];

export function isReservedPlatformHandle(handle: string): boolean {
  const normalized = handle.trim();
  if (normalized.length < 3 || normalized.length > 48) return true;
  return RESERVED_PATTERNS.some((re) => re.test(normalized));
}

export function validatePlatformHandleInput(handle: string): { ok: true } | { ok: false; error: string } {
  const trimmed = handle.trim();
  if (trimmed.length < 3) return { ok: false, error: 'Handle must be at least 3 characters' };
  if (trimmed.length > 48) return { ok: false, error: 'Handle must be 48 characters or fewer' };
  if (!/^[A-Za-z][A-Za-z0-9]*(?:[ \-·][A-Za-z][A-Za-z0-9]*){0,3}$/.test(trimmed)) {
    return {
      ok: false,
      error: 'Use letters, spaces, hyphens, or · only (e.g. Crimson Falcon · Kenya)',
    };
  }
  if (isReservedPlatformHandle(trimmed)) {
    return {
      ok: false,
      error: 'That handle is reserved. Names like Admin, Doxxed, or Platform Operator are not allowed.',
    };
  }
  return { ok: true };
}

/** Deterministic-ish handle from user id for auto-assignment */
export function generatePlatformHandle(seed: string, attempt = 0): string {
  let hash = 0;
  const s = `${seed}:${attempt}`;
  for (let i = 0; i < s.length; i++) {
    hash = (hash * 31 + s.charCodeAt(i)) >>> 0;
  }
  const creature = CREATURE_POOL[hash % CREATURE_POOL.length];
  const country = COUNTRIES[(hash >> 8) % COUNTRIES.length];
  const suffix = attempt > 0 ? ` ${1000 + (hash % 9000)}` : '';
  return `${creature} · ${country}${suffix}`;
}

export function userHasTwitterConnected(user: {
  twitterHandle?: string | null;
  oauthAccounts?: { provider: string }[];
}): boolean {
  if (user.twitterHandle?.trim()) return true;
  return (user.oauthAccounts ?? []).some(
    (a) => a.provider === 'twitter' || a.provider === 'x',
  );
}
