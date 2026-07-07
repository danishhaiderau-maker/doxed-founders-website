'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession, signOut } from 'next-auth/react';
import { useEffect, useMemo, useRef, useState, Suspense } from 'react';
import { ChevronDown, Menu, Search, X } from 'lucide-react';
import { cn, resolveGamifiedRole } from '@dcf/utils';
import { fetchAccountOverview, AccountOverview } from '@/lib/api';
import { GamifiedRoleBadge } from '@/components/account/gamified-role-badge';
import { EngagementFlashLayer } from '@/components/engagement-flash-layer';
import { NotificationBell } from '@/components/notification-bell';
import { PlatformMessagesBell } from '@/components/platform-messages-bell';
import { FounderChatLauncher } from '@/components/founder-chat-launcher';
import { HUB_NAV_ROWS, type HubNavItem, type HubNavRow } from '@/components/hub-nav-config';
import { useFeedNewCount } from '@/hooks/use-feed-new-count';

const PROFILE_LINKS = [
  { href: '/account', label: 'Overview' },
  { href: '/account?tab=messages', label: 'Messages' },
  { href: '/account?tab=security', label: 'Security' },
  { href: '/account?tab=notifications', label: 'Notification Settings' },
  { href: '/account?tab=connected', label: 'Connected Accounts' },
  { href: '/account?tab=reputation', label: 'Reputation' },
  { href: '/account?tab=activity', label: 'Activity History' },
] as const;

const ADMIN_PROFILE_LINKS = [
  { href: '/admin/control', label: 'Admin Control' },
  { href: '/admin/applications', label: 'Listing inbox' },
  { href: '/admin/agent-registrations', label: 'Agent registrations' },
  { href: '/account?tab=security', label: 'Connect Phantom wallet' },
] as const;

function sectionAccent(id: string) {
  if (id === 'trade') {
    return {
      trigger: 'text-emerald-200/90 hover:bg-emerald-500/10 hover:text-emerald-50',
      triggerActive: 'bg-emerald-500/20 text-emerald-50 ring-1 ring-emerald-400/40',
      panel: 'border-emerald-500/20',
      itemHover: 'hover:bg-emerald-500/10 hover:border-emerald-500/30',
      itemActive: 'bg-emerald-500/15 border-emerald-400/40 text-emerald-50',
    };
  }
  if (id === 'community') {
    return {
      trigger: 'text-amber-200/90 hover:bg-amber-500/10 hover:text-amber-50',
      triggerActive: 'bg-amber-500/20 text-amber-50 ring-1 ring-amber-400/40',
      panel: 'border-amber-500/20',
      itemHover: 'hover:bg-amber-500/10 hover:border-amber-500/30',
      itemActive: 'bg-amber-500/15 border-amber-400/40 text-amber-50',
    };
  }
  if (id === 'build') {
    return {
      trigger: 'text-violet-200/90 hover:bg-violet-500/10 hover:text-violet-50',
      triggerActive: 'bg-violet-500/20 text-violet-50 ring-1 ring-violet-400/40',
      panel: 'border-violet-500/20',
      itemHover: 'hover:bg-violet-500/10 hover:border-violet-500/30',
      itemActive: 'bg-violet-500/15 border-violet-400/40 text-violet-50',
    };
  }
  return {
    trigger: 'text-zinc-300 hover:bg-zinc-800/60 hover:text-white',
    triggerActive: 'bg-zinc-100/10 text-white ring-1 ring-zinc-400/40',
    panel: 'border-zinc-700/60',
    itemHover: 'hover:bg-zinc-800/80 hover:border-zinc-600/60',
    itemActive: 'bg-zinc-800 border-zinc-500/50 text-white',
  };
}

function navActive(pathname: string, href: string) {
  if (href === '/discover') return pathname === '/discover';
  if (href === '/feed') return pathname === '/feed' || pathname === '/build-feed' || pathname.startsWith('/town-hall');
  if (href === '/ddollar') return pathname.startsWith('/ddollar');
  if (href === '/paper-trading') return pathname.startsWith('/paper-trading');
  if (href.startsWith('/predict')) return pathname.startsWith('/predict');
  if (href.startsWith('/portfolio/')) return pathname.startsWith('/portfolio/');
  if (href === '/leaderboard') return pathname.startsWith('/leaderboard');
  if (href === '/agent-hub') return pathname.startsWith('/agent-hub') || pathname.startsWith('/agents');
  if (href === '/downloads' || href.startsWith('/downloads#')) return pathname.startsWith('/downloads');
  if (href.startsWith('/settings/builder')) {
    return pathname.startsWith('/settings/builder') || pathname.startsWith('/settings/integrations');
  }
  if (href === '/mobile') return pathname.startsWith('/downloads');
  if (href === '/founder-node') return pathname.startsWith('/downloads');
  if (href === '/builder-rewards') return pathname.startsWith('/builder-rewards') || pathname.startsWith('/airdrop');
  if (href === '/founder-os') {
    return pathname.startsWith('/founder-os');
  }
  if (href === '/founder-den') {
    return (
      pathname.startsWith('/founder-den') ||
      pathname.startsWith('/settings/builder') ||
      pathname.startsWith('/developers')
    );
  }
  if (href === '/raise-room') return pathname.startsWith('/raise-room');
  if (href === '/trust-center') return pathname.startsWith('/trust-center') || pathname.startsWith('/scout-votes');
  if (href === '/projects') return pathname.startsWith('/projects');
  if (href.startsWith('/account')) return pathname.startsWith('/account');
  if (href === '/watchlist') return pathname.startsWith('/watchlist');
  if (href === '/list-your-project') return pathname.startsWith('/list-your-project');
  return pathname === href || pathname.startsWith(`${href}/`);
}

function sectionHasActive(pathname: string, row: HubNavRow, resolveHref: (item: HubNavItem) => string) {
  return row.items.some((item) => navActive(pathname, resolveHref(item)));
}

function AdminProfileLinks({ onNavigate }: { onNavigate: () => void }) {
  return (
    <>
      {ADMIN_PROFILE_LINKS.map((item, index) => (
        <Link
          key={item.href}
          href={item.href}
          onClick={onNavigate}
          className={cn(
            'block px-3 py-2 text-sm transition hover:bg-zinc-900',
            index === 0
              ? 'border-t border-zinc-800 font-semibold text-amber-300/95'
              : 'text-amber-300/90',
          )}
        >
          {item.label}
        </Link>
      ))}
    </>
  );
}

export function SiteNav() {
  return (
    <>
      <Suspense fallback={<nav className="h-9 w-48 animate-pulse rounded-lg bg-zinc-800/50" />}>
        <SiteNavInner />
      </Suspense>
      <EngagementFlashLayer />
    </>
  );
}

function ProfileMenu({
  session,
  isAdmin,
  profileOpen,
  setProfileOpen,
  profileRef,
  profileActive,
  accountPreview,
  fallbackRole,
}: {
  session: NonNullable<ReturnType<typeof useSession>['data']>;
  isAdmin: boolean;
  profileOpen: boolean;
  setProfileOpen: React.Dispatch<React.SetStateAction<boolean>>;
  profileRef: React.RefObject<HTMLDivElement | null>;
  profileActive: boolean;
  accountPreview: AccountOverview | null;
  fallbackRole: ReturnType<typeof resolveGamifiedRole>;
}) {
  return (
    <div className="relative" ref={profileRef}>
      <button
        type="button"
        onClick={() => setProfileOpen((o) => !o)}
        className={cn(
          'flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition',
          profileActive || profileOpen
            ? 'bg-zinc-700 font-semibold text-white ring-1 ring-zinc-500'
            : 'text-zinc-300 hover:bg-zinc-800 hover:text-white',
        )}
      >
        <span className="hidden max-w-[120px] truncate md:inline">
          {accountPreview?.username ?? session.user?.name ?? session.user?.email}
        </span>
        <GamifiedRoleBadge
          role={accountPreview?.gamifiedRole ?? fallbackRole}
          className="hidden md:inline-flex"
        />
        <span className="md:hidden">Profile</span>
      </button>
      {profileOpen && (
        <div className="absolute right-0 top-full z-[60] mt-1.5 min-w-[220px] rounded-xl border border-zinc-700/80 bg-zinc-950/95 py-1 shadow-2xl backdrop-blur-md">
          {accountPreview && (
            <div className="border-b border-zinc-800 px-3 py-2">
              <p className="truncate text-sm font-medium text-white">{accountPreview.username}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <GamifiedRoleBadge role={accountPreview.gamifiedRole} />
                <Link
                  href="/ddollar"
                  onClick={() => setProfileOpen(false)}
                  className="text-xs font-semibold text-amber-300 hover:underline"
                >
                  {accountPreview.reputation.reputationPoints.toLocaleString()} DDollar
                </Link>
              </div>
            </div>
          )}
          {PROFILE_LINKS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              onClick={() => setProfileOpen(false)}
              className="block px-3 py-2 text-sm text-zinc-400 transition hover:bg-zinc-900 hover:text-white"
            >
              {item.label}
            </Link>
          ))}
          {isAdmin && <AdminProfileLinks onNavigate={() => setProfileOpen(false)} />}
          <button
            type="button"
            onClick={() => signOut({ callbackUrl: '/' })}
            className="block w-full px-3 py-2 text-left text-sm text-zinc-400 transition hover:bg-zinc-900 hover:text-white"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function NavDropdown({
  row,
  pathname,
  resolveHref,
  itemBadge,
  openId,
  setOpenId,
}: {
  row: HubNavRow;
  pathname: string;
  resolveHref: (item: HubNavItem) => string | null;
  itemBadge: (item: HubNavItem) => string | undefined;
  openId: string | null;
  setOpenId: (id: string | null) => void;
}) {
  const accent = sectionAccent(row.id);
  const isOpen = openId === row.id;
  const isActive = sectionHasActive(pathname, row, (item) => resolveHref(item) ?? '');

  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={isOpen}
        aria-haspopup="true"
        onClick={() => setOpenId(isOpen ? null : row.id)}
        className={cn(
          'inline-flex items-center gap-1 rounded-lg px-3 py-2 text-sm font-medium transition',
          isActive ? accent.triggerActive : accent.trigger,
        )}
      >
        {row.label}
        <ChevronDown className={cn('h-3.5 w-3.5 opacity-70 transition', isOpen && 'rotate-180')} />
      </button>
      {isOpen && (
        <div
          className={cn(
            'absolute left-0 top-full z-50 mt-1.5 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border bg-zinc-950/95 shadow-2xl backdrop-blur-md',
            accent.panel,
          )}
        >
          <div className="border-b border-zinc-800/80 px-4 py-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-zinc-500">{row.subtitle}</p>
            <p className="mt-1 text-xs text-zinc-400">{row.sidebarDescription}</p>
          </div>
          <div className="grid gap-1 p-2 sm:grid-cols-2">
            {row.items.map((item) => {
              const href = resolveHref(item);
              if (!href) return null;
              const active = navActive(pathname, href);
              const badge = itemBadge(item);
              return (
                <Link
                  key={`${item.label}-${item.href}`}
                  href={href}
                  onClick={() => setOpenId(null)}
                  className={cn(
                    'flex items-start gap-2.5 rounded-lg border border-transparent px-3 py-2.5 transition',
                    active ? accent.itemActive : cn('text-zinc-300', accent.itemHover),
                  )}
                >
                  <span className="mt-0.5 text-base leading-none" aria-hidden>
                    {item.icon}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 text-sm font-medium">
                      {item.label}
                      {badge ? (
                        <span className="rounded-full bg-violet-500 px-1.5 py-0.5 text-[9px] font-bold text-white">
                          {badge}
                        </span>
                      ) : null}
                    </span>
                  </span>
                </Link>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function MobileNavDrawer({
  open,
  onClose,
  pathname,
  resolveHref,
  itemBadge,
  session,
  isAdmin,
  profileOpen,
  setProfileOpen,
  profileRef,
  profileActive,
  accountPreview,
  fallbackRole,
}: {
  open: boolean;
  onClose: () => void;
  pathname: string;
  resolveHref: (item: HubNavItem) => string | null;
  itemBadge: (item: HubNavItem) => string | undefined;
  session: ReturnType<typeof useSession>['data'];
  isAdmin: boolean;
  profileOpen: boolean;
  setProfileOpen: React.Dispatch<React.SetStateAction<boolean>>;
  profileRef: React.RefObject<HTMLDivElement | null>;
  profileActive: boolean;
  accountPreview: AccountOverview | null;
  fallbackRole: ReturnType<typeof resolveGamifiedRole>;
}) {
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(HUB_NAV_ROWS.map((r) => r.id)),
  );

  useEffect(() => {
    if (!open) {
      setQuery('');
      return;
    }
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  const filteredRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return HUB_NAV_ROWS;
    return HUB_NAV_ROWS.map((row) => ({
      ...row,
      items: row.items.filter(
        (item) =>
          item.label.toLowerCase().includes(q) ||
          row.label.toLowerCase().includes(q) ||
          row.subtitle.toLowerCase().includes(q),
      ),
    })).filter((row) => row.items.length > 0);
  }, [query]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[70] md:hidden">
      <button
        type="button"
        aria-label="Close menu"
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />
      <aside className="absolute inset-y-0 right-0 flex w-full flex-col border-l border-zinc-800 bg-[#07070c] shadow-2xl sm:max-w-sm">
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <p className="text-sm font-bold text-white">Menu</p>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-zinc-400 hover:bg-zinc-900 hover:text-white"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="border-b border-zinc-800 px-4 py-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a page…"
              className="w-full rounded-xl border border-zinc-800 bg-zinc-950 py-2.5 pl-9 pr-3 text-sm text-zinc-200 placeholder:text-zinc-600"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-3">
          <Link
            href="/raise-room"
            onClick={onClose}
            className={cn(
              'mb-3 flex items-center gap-2 rounded-xl border px-3 py-3 text-sm font-bold transition',
              navActive(pathname, '/raise-room')
                ? 'border-amber-400/40 bg-amber-500/15 text-amber-50'
                : 'border-amber-500/25 bg-amber-950/20 text-amber-200 hover:bg-amber-500/10',
            )}
          >
            <span aria-hidden>🚀</span>
            Raise Room
          </Link>
          {filteredRows.map((row) => {
            const accent = sectionAccent(row.id);
            const isExpanded = expanded.has(row.id) || query.length > 0;
            return (
              <div key={row.id} className="mb-2 overflow-hidden rounded-xl border border-zinc-800/80">
                <button
                  type="button"
                  onClick={() =>
                    setExpanded((prev) => {
                      const next = new Set(prev);
                      if (next.has(row.id)) next.delete(row.id);
                      else next.add(row.id);
                      return next;
                    })
                  }
                  className={cn(
                    'flex w-full items-center justify-between px-3 py-3 text-left text-sm font-semibold',
                    sectionHasActive(pathname, row, (item) => resolveHref(item) ?? '')
                      ? accent.triggerActive
                      : 'text-zinc-200',
                  )}
                >
                  <span className="flex items-center gap-2">
                    <span
                      className={cn(
                        'inline-block h-2 w-2 rounded-full',
                        row.id === 'trade' && 'bg-emerald-400',
                        row.id === 'community' && 'bg-amber-400',
                        row.id === 'build' && 'bg-violet-400',
                      )}
                      aria-hidden
                    />
                    {row.label}
                  </span>
                  <ChevronDown className={cn('h-4 w-4 opacity-60 transition', isExpanded && 'rotate-180')} />
                </button>
                {isExpanded && (
                  <div className="space-y-0.5 border-t border-zinc-800/80 p-2">
                    {row.items.map((item) => {
                      const href = resolveHref(item);
                      if (!href) return null;
                      const active = navActive(pathname, href);
                      const badge = itemBadge(item);
                      return (
                        <Link
                          key={`${item.label}-${item.href}`}
                          href={href}
                          onClick={onClose}
                          className={cn(
                            'flex items-center gap-2 rounded-lg px-3 py-2.5 text-sm transition',
                            active ? accent.itemActive : cn('text-zinc-300', accent.itemHover),
                          )}
                        >
                          <span aria-hidden>{item.icon}</span>
                          <span className="flex-1">{item.label}</span>
                          {badge ? (
                            <span className="rounded-full bg-violet-500 px-1.5 py-0.5 text-[9px] font-bold text-white">
                              {badge}
                            </span>
                          ) : null}
                        </Link>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-zinc-800 px-4 py-3">
          <FounderChatLauncher />
          <PlatformMessagesBell />
          <NotificationBell />
          {session ? (
            <ProfileMenu
              session={session}
              isAdmin={isAdmin}
              profileOpen={profileOpen}
              setProfileOpen={setProfileOpen}
              profileRef={profileRef}
              profileActive={profileActive}
              accountPreview={accountPreview}
              fallbackRole={fallbackRole}
            />
          ) : (
            <Link
              href="/login"
              onClick={onClose}
              className="rounded-lg border border-zinc-600 px-3 py-2 text-sm text-zinc-200 hover:text-white"
            >
              Sign in
            </Link>
          )}
        </div>
      </aside>
    </div>
  );
}

function MobileSectionDropdown({
  row,
  pathname,
  resolveHref,
  itemBadge,
  onClose,
}: {
  row: HubNavRow | null;
  pathname: string;
  resolveHref: (item: HubNavItem) => string | null;
  itemBadge: (item: HubNavItem) => string | undefined;
  onClose: () => void;
}) {
  if (!row) return null;
  const accent = sectionAccent(row.id);

  return (
    <div
      className={cn(
        'absolute right-0 top-full z-[65] mt-1.5 w-[min(24rem,calc(100vw-2rem))] overflow-hidden rounded-xl border bg-zinc-950/97 shadow-2xl backdrop-blur-md md:hidden',
        accent.panel,
      )}
      role="menu"
      aria-label={`${row.label} menu`}
    >
      <div className="border-b border-zinc-800/80 px-4 py-3">
        <p
          className={cn(
            'text-[10px] font-bold uppercase tracking-[0.18em]',
            row.id === 'trade' && 'text-emerald-300',
            row.id === 'community' && 'text-amber-300',
            row.id === 'build' && 'text-violet-300',
          )}
        >
          {row.label}
        </p>
        <p className="mt-0.5 text-[11px] text-zinc-500">{row.subtitle}</p>
      </div>
      <div className="grid grid-cols-2 gap-1 p-2">
        {row.items.map((item) => {
          const href = resolveHref(item);
          if (!href) return null;
          const active = navActive(pathname, href);
          const badge = itemBadge(item);
          return (
            <Link
              key={`${item.label}-${item.href}`}
              href={href}
              onClick={onClose}
              className={cn(
                'flex items-center gap-2 rounded-lg border border-transparent px-3 py-2.5 text-sm transition',
                active ? accent.itemActive : cn('text-zinc-200', accent.itemHover),
              )}
            >
              <span className="text-base leading-none" aria-hidden>{item.icon}</span>
              <span className="flex-1 font-medium">{item.label}</span>
              {badge ? (
                <span className="rounded-full bg-violet-500 px-1.5 py-0.5 text-[9px] font-bold text-white">
                  {badge}
                </span>
              ) : null}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function SiteNavInner() {
  const pathname = usePathname();
  const { data: session } = useSession();
  const { count: feedNewCount } = useFeedNewCount();
  const isAdmin = session?.user?.role === 'ADMIN';
  const [profileOpen, setProfileOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const [accountPreview, setAccountPreview] = useState<AccountOverview | null>(null);
  const profileRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLDivElement>(null);

  const portfolioUserId =
    session?.user?.id ?? accountPreview?.userId ?? getActiveUserId(session?.user?.id);

  useEffect(() => {
    if (!session?.accessToken) {
      setAccountPreview(null);
      return;
    }
    fetchAccountOverview(session.accessToken)
      .then(setAccountPreview)
      .catch(() => setAccountPreview(null));
  }, [session?.accessToken]);

  useEffect(() => {
    setOpenDropdown(null);
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) {
        setProfileOpen(false);
      }
      if (navRef.current && !navRef.current.contains(e.target as Node)) {
        setOpenDropdown(null);
      }
    }
    document.addEventListener('click', onDocClick);
    return () => document.removeEventListener('click', onDocClick);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpenDropdown(null);
        setMobileOpen(false);
        setProfileOpen(false);
      }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const profileActive =
    pathname.startsWith('/account') ||
    (pathname.startsWith('/settings') && !pathname.startsWith('/settings/builder'));

  const fallbackRole = resolveGamifiedRole({
    platformRole: session?.user?.role,
  });

  function resolveHref(item: HubNavItem): string | null {
    if (item.href === '__portfolio__') {
      if (!session && !portfolioUserId) return '/login?callbackUrl=%2Fpaper-trading';
      if (!portfolioUserId) return null;
      return `/portfolio/${portfolioUserId}`;
    }
    if (item.auth && !session) {
      return `/login?callbackUrl=${encodeURIComponent(item.href)}`;
    }
    return item.href;
  }

  function itemBadge(item: HubNavItem): string | undefined {
    if (item.label === 'Feed' && feedNewCount > 0) {
      return feedNewCount > 9 ? '9+' : String(feedNewCount);
    }
    return undefined;
  }

  return (
    <>
      <div ref={navRef} className="relative flex flex-col items-end gap-1">
        <nav className="flex items-center gap-1 text-sm">
        {/* Founder Chat launcher — placed before nav dropdowns (between brand and section links) */}
        <FounderChatLauncher />

        <Link
          href="/raise-room"
          className={cn(
            'hidden rounded-lg px-3 py-2 text-sm font-bold transition md:inline-flex',
            navActive(pathname, '/raise-room')
              ? 'bg-amber-500/20 text-amber-50 ring-1 ring-amber-400/40'
              : 'text-amber-200/90 hover:bg-amber-500/10 hover:text-amber-50',
          )}
        >
          Raise Room
        </Link>

        {/* Desktop dropdowns — visible on md+ so tablets and smaller laptops see them */}
        <div className="hidden items-center gap-0.5 md:flex">
          {HUB_NAV_ROWS.map((row) => (
            <NavDropdown
              key={row.id}
              row={row}
              pathname={pathname}
              resolveHref={resolveHref}
              itemBadge={itemBadge}
              openId={openDropdown}
              setOpenId={setOpenDropdown}
            />
          ))}
        </div>

        {/* Desktop actions */}
        <div className="hidden items-center gap-2 pl-2 md:flex">
          <div className="h-5 w-px bg-zinc-800" aria-hidden />
          <PlatformMessagesBell />
          <NotificationBell />
          {session ? (
            <ProfileMenu
              session={session}
              isAdmin={isAdmin}
              profileOpen={profileOpen}
              setProfileOpen={setProfileOpen}
              profileRef={profileRef}
              profileActive={profileActive}
              accountPreview={accountPreview}
              fallbackRole={fallbackRole}
            />
          ) : (
            <Link
              href="/login"
              className="rounded-lg border border-zinc-600 px-3 py-2 text-sm text-zinc-200 transition hover:border-zinc-500 hover:text-white"
            >
              Sign in
            </Link>
          )}
        </div>

        {/* Mobile menu trigger */}
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-900/80 px-3 py-2 text-sm font-medium text-zinc-200 md:hidden"
          aria-label="Open menu"
        >
          <Menu className="h-4 w-4" />
          Menu
        </button>
      </nav>

      {/* Mobile section tabs — each opens its own dropdown, always visible */}
      <div className="flex items-center gap-1.5 md:hidden" aria-label="Sections">
        <Link
          href="/raise-room"
          onClick={() => setOpenDropdown(null)}
          className={cn(
            'inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-bold transition',
            navActive(pathname, '/raise-room')
              ? 'border-amber-400/40 bg-amber-500/20 text-amber-50'
              : 'border-amber-500/30 bg-amber-950/30 text-amber-200/90 hover:bg-amber-500/10',
          )}
        >
          Raise Room
        </Link>
        {HUB_NAV_ROWS.map((row) => {
          const accent = sectionAccent(row.id);
          const active = sectionHasActive(pathname, row, (item) => resolveHref(item) ?? '');
          const isOpen = openDropdown === row.id;
          return (
            <button
              key={row.id}
              type="button"
              aria-expanded={isOpen}
              onClick={() => setOpenDropdown(isOpen ? null : row.id)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold transition',
                isOpen || active
                  ? cn(accent.triggerActive, accent.panel)
                  : cn('text-zinc-300 border-zinc-700/70 bg-zinc-900/60', accent.itemHover),
              )}
            >
              <span
                className={cn(
                  'inline-block h-1.5 w-1.5 rounded-full',
                  row.id === 'trade' && 'bg-emerald-400',
                  row.id === 'community' && 'bg-amber-400',
                  row.id === 'build' && 'bg-violet-400',
                )}
                aria-hidden
              />
              {row.id === 'trade' ? 'Trade' : row.id === 'community' ? 'Community' : 'Build'}
              <ChevronDown className={cn('h-3 w-3 opacity-60 transition', isOpen && 'rotate-180')} />
            </button>
          );
        })}
      </div>

      {/* Mobile section dropdown panel */}
      {openDropdown && (
        <MobileSectionDropdown
          row={HUB_NAV_ROWS.find((r) => r.id === openDropdown) ?? null}
          pathname={pathname}
          resolveHref={resolveHref}
          itemBadge={itemBadge}
          onClose={() => setOpenDropdown(null)}
        />
      )}
      </div>

      <MobileNavDrawer
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        pathname={pathname}
        resolveHref={resolveHref}
        itemBadge={itemBadge}
        session={session}
        isAdmin={isAdmin}
        profileOpen={profileOpen}
        setProfileOpen={setProfileOpen}
        profileRef={profileRef}
        profileActive={profileActive}
        accountPreview={accountPreview}
        fallbackRole={fallbackRole}
      />
    </>
  );
}

export function SiteBrand({ className }: { className?: string }) {
  return (
    <Link
      href="/"
      className={cn('font-semibold tracking-tight text-white hover:text-[var(--color-accent)]', className)}
    >
      Doxxed crypto
    </Link>
  );
}

const SESSION_KEY = 'dcf-paper-user-id';

export function getActiveUserId(sessionUserId?: string | null): string | null {
  if (sessionUserId) return sessionUserId;
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(SESSION_KEY);
}
