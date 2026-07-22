/** Paths that use the shared Founder product navigation. */
const HUB_PREFIXES = [
  '/discover',
  '/projects',
  '/leaderboard',
  '/trust-center',
  '/agent-hub',
  '/agents',
  '/feed',
  '/build-feed',
  '/town-hall',
  '/ddollar',
  '/paper-trading',
  '/watchlist',
  '/portfolio',
  '/founder-os',
  '/founder-den',
  '/settings/builder',
  '/settings/integrations',
  '/downloads',
  '/phone',
  '/raise-room',
  '/list-your-project',
  '/founder-economics',
  '/notifications',
  '/reputation',
  '/predict',
  '/founders',
  '/founder/',
  '/project/',
  '/scout-votes',
  '/airdrop',
  '/builder-rewards',
] as const;

export function isHubWorkspacePath(pathname: string): boolean {
  if (!pathname || pathname === '/') return false;
  if (pathname.startsWith('/login') || pathname.startsWith('/register')) return false;
  if (pathname.startsWith('/admin')) return false;
  if (pathname === '/privacy' || pathname === '/rules' || pathname === '/busted') return false;
  return HUB_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`) || pathname.startsWith(prefix),
  );
}

export function hubPageTitle(pathname: string): string {
  if (pathname.startsWith('/discover')) return 'Discover';
  if (pathname.startsWith('/projects')) return 'Projects';
  if (pathname.startsWith('/leaderboard')) return 'Rankings';
  if (pathname.startsWith('/trust-center')) return 'Trust Center';
  if (pathname.startsWith('/scout-votes')) return 'Scout Voting';
  if (pathname.startsWith('/agent-hub') || pathname.startsWith('/agents')) return 'Agents';
  if (pathname.startsWith('/airdrop') || pathname.startsWith('/builder-rewards')) return 'Builder Rewards';
  if (pathname.startsWith('/feed') || pathname.startsWith('/build-feed') || pathname.startsWith('/town-hall')) return 'Updates';
  if (pathname.startsWith('/ddollar')) return 'DDollar';
  if (pathname.startsWith('/paper-trading')) return 'Trading Desk';
  if (pathname.startsWith('/watchlist')) return 'Watchlist';
  if (pathname.startsWith('/portfolio')) return 'Portfolio';
  if (pathname.startsWith('/founder-os') || pathname.startsWith('/founder-den')) return 'Build';
  if (pathname.startsWith('/phone')) return 'Remote Work';
  if (pathname.startsWith('/settings/builder') || pathname.startsWith('/settings/integrations')) return 'Connections';
  if (pathname.startsWith('/downloads')) return 'Founder IDE';
  if (pathname.startsWith('/raise-room')) return 'Raise Room';
  if (pathname.startsWith('/founder-economics')) return 'Founder Economics';
  if (pathname.startsWith('/list-your-project')) return 'Launch Readiness';
  if (pathname.startsWith('/notifications')) return 'Notifications';
  if (pathname.startsWith('/reputation')) return 'Reputation';
  if (pathname.startsWith('/predict')) return 'Predictions';
  if (pathname.startsWith('/founders')) return 'Founders';
  if (pathname.startsWith('/founder/')) return 'Founder';
  if (pathname.startsWith('/project/')) return 'Project';
  return 'Doxxed Crypto';
}

export type HubNavIconName =
  | 'workspace'
  | 'ide'
  | 'remote'
  | 'connections'
  | 'launch'
  | 'capital'
  | 'projects'
  | 'founders'
  | 'agents'
  | 'updates'
  | 'trust'
  | 'rankings'
  | 'markets'
  | 'swap'
  | 'predictions';

export type HubNavItem = {
  href: string;
  label: string;
  icon: HubNavIconName;
  auth?: boolean;
};

export type HubNavRow = {
  id: 'build' | 'discover' | 'trade';
  rowNumber: string;
  label: string;
  subtitle: string;
  sidebarDescription: string;
  borderClass: string;
  labelClass: string;
  rowBgClass: string;
  items: HubNavItem[];
};

export const HUB_NAV_ROWS: HubNavRow[] = [
  {
    id: 'build',
    rowNumber: 'Row 1',
    label: 'Build',
    subtitle: 'Workspace · IDE · Connect · Ship',
    sidebarDescription: 'Create, connect your tools, work remotely, and prepare a responsible launch.',
    borderClass: 'border-blue-500/25',
    labelClass: 'text-blue-200',
    rowBgClass: 'bg-blue-950/20',
    items: [
      { href: '/founder-den', label: 'Workspace', icon: 'workspace', auth: true },
      { href: '/downloads', label: 'Founder IDE', icon: 'ide' },
      { href: '/phone', label: 'Remote work', icon: 'remote', auth: true },
      { href: '/settings/builder', label: 'Connections', icon: 'connections', auth: true },
      { href: '/list-your-project', label: 'Launch readiness', icon: 'launch' },
      { href: '/raise-room', label: 'Raise Room', icon: 'capital' },
    ],
  },
  {
    id: 'discover',
    rowNumber: 'Row 2',
    label: 'Discover',
    subtitle: 'Projects · Founders · Agents · Trust',
    sidebarDescription: 'Evaluate real builders, projects, agents, evidence, and long-term progress.',
    borderClass: 'border-amber-500/25',
    labelClass: 'text-amber-200',
    rowBgClass: 'bg-amber-950/20',
    items: [
      { href: '/projects', label: 'Projects', icon: 'projects' },
      { href: '/founders', label: 'Founders', icon: 'founders' },
      { href: '/agent-hub', label: 'Agents', icon: 'agents' },
      { href: '/feed', label: 'Updates', icon: 'updates' },
      { href: '/trust-center', label: 'Trust Center', icon: 'trust' },
      { href: '/leaderboard', label: 'Rankings', icon: 'rankings' },
    ],
  },
  {
    id: 'trade',
    rowNumber: 'Row 3',
    label: 'Trade',
    subtitle: 'Markets · Positions · Predictions',
    sidebarDescription: 'Use the current trading tools, follow positions, and evaluate graduated markets.',
    borderClass: 'border-emerald-500/25',
    labelClass: 'text-emerald-200',
    rowBgClass: 'bg-emerald-950/20',
    items: [
      { href: '/paper-trading', label: 'Trading desk', icon: 'markets' },
      { href: '/predict?tab=rules', label: 'Predictions', icon: 'predictions' },
      { href: '/watchlist', label: 'Watchlist', icon: 'trust', auth: true },
      { href: '__portfolio__', label: 'Portfolio', icon: 'markets', auth: true },
      { href: '/raise-room', label: 'Raise activity', icon: 'capital' },
      { href: '/ddollar', label: 'DDollar', icon: 'swap' },
    ],
  },
];
