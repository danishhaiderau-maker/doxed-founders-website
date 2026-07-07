/** Paths that use minimal hub navigation (Home + notifications + profile). */
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
  '/raise-room',
  '/list-your-project',
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
    (p) => pathname === p || pathname.startsWith(`${p}/`) || pathname.startsWith(p),
  );
}

export function hubPageTitle(pathname: string): string {
  if (pathname.startsWith('/discover')) return 'Discover';
  if (pathname.startsWith('/projects')) return 'Projects';
  if (pathname.startsWith('/leaderboard')) return 'Rankings';
  if (pathname.startsWith('/trust-center')) return 'Trust Center';
  if (pathname.startsWith('/scout-votes')) return 'Scout Voting';
  if (pathname.startsWith('/agent-hub') || pathname.startsWith('/agents')) return 'Agents';
  if (pathname.startsWith('/airdrop') || pathname.startsWith('/builder-rewards'))
    return 'Builder Rewards';
  if (pathname.startsWith('/feed') || pathname.startsWith('/build-feed') || pathname.startsWith('/town-hall'))
    return 'Feed';
  if (pathname.startsWith('/ddollar')) return 'DDollar';
  if (pathname.startsWith('/paper-trading')) return 'Trading Alpha';
  if (pathname.startsWith('/watchlist')) return 'Watchlist';
  if (pathname.startsWith('/portfolio')) return 'Portfolio';
  if (pathname.startsWith('/founder-os')) return 'Founder OS';
  if (pathname.startsWith('/founder-den')) return 'Founder Den';
  if (pathname.startsWith('/settings/builder') || pathname.startsWith('/settings/integrations'))
    return 'Integrations';
  if (pathname.startsWith('/downloads')) return 'Downloads';
  if (pathname.startsWith('/raise-room')) return 'Raise Room';
  if (pathname.startsWith('/list-your-project')) return 'List Project';
  if (pathname.startsWith('/notifications')) return 'Notifications';
  if (pathname.startsWith('/reputation')) return 'Reputation';
  if (pathname.startsWith('/predict')) return 'Predict';
  if (pathname.startsWith('/founders')) return 'Founders';
  if (pathname.startsWith('/founder/')) return 'Founder';
  if (pathname.startsWith('/project/')) return 'Project';
  return 'Doxxed Crypto';
}

export type HubNavItem = {
  href: string;
  label: string;
  icon: string;
  auth?: boolean;
};

export type HubNavRow = {
  id: string;
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
    id: 'trade',
    rowNumber: 'Row 1',
    label: 'Trade & Agents',
    subtitle: 'Live agent · Paper desk · Rankings',
    sidebarDescription: 'Copy-trade the showcase BTC agent, paper trade with DDollar, climb the leaderboard.',
    borderClass: 'border-emerald-500/25',
    labelClass: 'text-emerald-200',
    rowBgClass: 'bg-emerald-950/20',
    items: [
      { href: '/agent-hub', label: 'Agents', icon: '🤖' },
      { href: '/paper-trading', label: 'Trading Alpha', icon: '📈' },
      { href: '/predict?tab=rules', label: 'Predictions', icon: '🎯' },
      { href: '/leaderboard', label: 'Rankings', icon: '🏅' },
      { href: '/watchlist', label: 'Watchlist', icon: '★', auth: true },
      { href: '__portfolio__', label: 'Portfolio', icon: '💼', auth: true },
    ],
  },
  {
    id: 'community',
    rowNumber: 'Row 2',
    label: 'Community',
    subtitle: 'Feed · Trust · DDollar · Rewards',
    sidebarDescription: 'Follow shipping founders, validate projects, earn DDollar, claim builder rewards.',
    borderClass: 'border-amber-500/25',
    labelClass: 'text-amber-200',
    rowBgClass: 'bg-amber-950/20',
    items: [
      { href: '/feed', label: 'Feed', icon: '📰' },
      { href: '/ddollar', label: 'DDollar', icon: '💵' },
      { href: '/discover', label: 'Discover', icon: '🔍' },
      { href: '/projects', label: 'Projects', icon: '📦' },
      { href: '/trust-center', label: 'Trust Center', icon: '🛡' },
      { href: '/builder-rewards', label: 'Builder Rewards', icon: '🏗' },
    ],
  },
  {
    id: 'build',
    rowNumber: 'Row 3',
    label: 'Build',
    subtitle: 'Founder OS · Downloads · Ship',
    sidebarDescription: 'Development Workspace, install apps, connect AI and infra, raise capital, list your project.',
    borderClass: 'border-violet-500/25',
    labelClass: 'text-violet-200',
    rowBgClass: 'bg-violet-950/20',
    items: [
      { href: '/founder-os', label: 'Founder OS', icon: '⚡', auth: true },
      { href: '/founder-den', label: 'Founder Den', icon: '🛠', auth: true },
      { href: '/downloads#founder-node', label: 'Founder Node', icon: '🖥' },
      { href: '/raise-room', label: 'Raise Room', icon: '🚀' },
      { href: '/list-your-project', label: 'List Project', icon: '📋' },
      { href: '/downloads', label: 'Downloads', icon: '⬇️' },
      { href: '/downloads#mobile', label: 'Android app', icon: '📱' },
      { href: '/settings/builder?tab=ai', label: 'AI Providers', icon: '🧠', auth: true },
      { href: '/settings/builder?tab=infra', label: 'Infrastructure', icon: '☁️', auth: true },
    ],
  },
];
