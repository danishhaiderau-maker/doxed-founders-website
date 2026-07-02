'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { extractPoolAddressFromDexUrl, buildSiteUrl, buildListingShareMessage } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import { FounderBadges } from '@/components/founder-badges';
import { GeckoTerminalChart } from '@/components/gecko-terminal-chart';
import { ProjectMetricsGrid } from '@/components/project-card';
import { ProjectRoomPanel } from '@/components/project-room';
import { FounderBrainPanel } from '@/components/founder-brain-panel';
import { ProjectRecentBuyersPanel } from '@/components/project-recent-buyers';
import { WatchlistButton } from '@/components/watchlist-button';
import { ClaimProfilePanel } from '@/components/claim-profile-panel';
import { ProjectOwnerFlashBanner } from '@/components/project-owner-flash-banner';
import { ProjectProfileLockButton } from '@/components/project-profile-lock-button';
import { ProjectWall } from '@/components/project-wall';
import { CollapsibleInfo } from '@/components/ui/collapsible-info';
import { fetchProject, fetchProjectClaimContext, fetchAccountOverview, type ProjectDetail, type AccountOverview } from '@/lib/api';

const LIFECYCLE_LABEL: Record<string, string> = {
  IDEA: 'Idea',
  VALIDATION: 'Validation',
  PRE_LAUNCH: 'Pre-launch',
  TOKEN_LAUNCH: 'Token launch',
  LIVE_TRADING: 'Live trading',
};

export default function ProjectDetailPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug;
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [claimContext, setClaimContext] = useState<ProjectDetail['claimProfile']>();
  const [account, setAccount] = useState<AccountOverview | null>(null);
  const { data: session } = useSession();
  const origin = useShareOrigin();

  const claimProfile = claimContext ?? project?.claimProfile;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchProject(slug);
      setProject(data);
      setError(null);
      const token = session?.accessToken;
      if (token) {
        const ctx = await fetchProjectClaimContext(slug, token).catch(() => undefined);
        setClaimContext(ctx ?? data.claimProfile);
      } else {
        setClaimContext(undefined);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Project not found');
      setProject(null);
    } finally {
      setLoading(false);
    }
  }, [slug, session?.accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const token = session?.accessToken;
    if (!token || !slug) {
      setClaimContext(undefined);
      return;
    }
    fetchProjectClaimContext(slug, token)
      .then(setClaimContext)
      .catch(() => setClaimContext(undefined));
  }, [session?.accessToken, slug, project?.claimProfile?.claimed]);

  useEffect(() => {
    if (!session?.accessToken) {
      setAccount(null);
      return;
    }
    fetchAccountOverview(session.accessToken)
      .then(setAccount)
      .catch(() => setAccount(null));
  }, [session?.accessToken]);

  const shareText = useMemo(() => {
    if (!project) return '';
    return buildListingShareMessage({
      projectName: project.name,
      ticker: project.ticker,
      scoutHighlight: project.scoutHighlight,
      scoutThesis: project.listingScoutThesis ?? project.verificationDossier?.whyList,
      whyDoxxed: project.verificationDossier?.whyDoxxed,
      summary: project.summary,
      projectTwitterHandle: claimProfile?.projectTwitterHandle,
    });
  }, [project, claimProfile?.projectTwitterHandle]);

  const poolAddress =
    project?.dexscreenerUrl != null
      ? extractPoolAddressFromDexUrl(project.dexscreenerUrl)
      : null;

  const ddollarBalance = account?.reputation?.reputationPoints ?? null;

  return (
    <div className="min-h-screen bg-[#0B0B0B]">
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#0B0B0B]/90 backdrop-blur-xl">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3 md:px-6">
          <div className="flex items-center gap-2">
            <Link href="/projects" className="text-xs text-zinc-500 hover:text-white">
              ← Projects
            </Link>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6 md:px-6 md:py-8">
        {loading && <p className="text-zinc-500">Loading…</p>}
        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        {project && (
          <div className="space-y-6">
            {(claimProfile?.claimable || claimProfile?.projectTwitterHandle) && (
              <>
                <ProjectOwnerFlashBanner
                  projectName={project.name}
                  slug={project.slug}
                  claimProfile={claimProfile}
                />
                {claimProfile?.claimable && (
                  <ClaimProfilePanel
                    slug={project.slug}
                    projectName={project.name}
                    claimProfile={claimProfile}
                    onClaimed={load}
                  />
                )}
              </>
            )}

            {/* ── Compact identity header card ── */}
            <section className="rounded-2xl border border-zinc-800 bg-zinc-950/40 p-4 md:p-5">
              <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div className="flex gap-3.5">
                  <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-zinc-900 text-base font-bold text-violet-300 ring-1 ring-violet-500/20">
                    {project.logoUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={project.logoUrl} alt="" className="h-14 w-14 rounded-xl object-cover" />
                    ) : (
                      project.ticker.slice(0, 2)
                    )}
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h1 className="text-2xl font-bold text-white">{project.name}</h1>
                      <span className="text-sm text-zinc-500">${project.ticker}</span>
                      {project.listingKind === 'verified' && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-300">
                          ✓ Verified
                        </span>
                      )}
                      {project.featured && (
                        <span className="rounded bg-amber-950/50 px-2 py-0.5 text-[10px] font-semibold uppercase text-amber-300">
                          Featured
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-zinc-500">
                      {project.category?.name ?? 'Token'} · {project.chain.name}
                      {project.lifecycleStage && (
                        <> · <span className="text-violet-300">{LIFECYCLE_LABEL[project.lifecycleStage] ?? project.lifecycleStage.replace(/_/g, ' ')}</span></>
                      )}
                    </p>
                    {project.founder && (
                      <p className="mt-1 text-xs text-zinc-500">
                        Founder{' '}
                        <Link href={`/founder/${project.founder.slug}`} className="font-medium text-zinc-300 hover:text-white">
                          {project.founder.name}
                        </Link>
                        <FounderBadges verifications={project.founder.verifications} />
                      </p>
                    )}
                    {project.summary && (
                      <p className="mt-2 max-w-2xl line-clamp-2 text-sm text-zinc-400">{project.summary}</p>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  {project.websiteUrl && (
                    <a href={project.websiteUrl} target="_blank" rel="noopener noreferrer" className="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:border-zinc-600 hover:text-white">
                      Website
                    </a>
                  )}
                  {project.dexscreenerUrl && (
                    <a href={project.dexscreenerUrl} target="_blank" rel="noopener noreferrer" className="rounded-lg border border-violet-500/40 px-3 py-2 text-xs text-violet-200 hover:bg-violet-950/30">
                      DexScreener
                    </a>
                  )}
                  <Link
                    href={`/paper-trading?dex=${encodeURIComponent(project.dexscreenerUrl ?? '')}`}
                    className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500"
                  >
                    Paper trade
                  </Link>
                  <WatchlistButton slug={project.slug} />
                  <ShareOnXButton
                    text={shareText}
                    url={buildSiteUrl(origin, `/project/${project.slug}`)}
                    label="Share"
                    className="px-3 py-2"
                  />
                  {claimProfile?.isOwner && session?.accessToken && (
                    <ProjectProfileLockButton
                      slug={project.slug}
                      accessToken={session.accessToken}
                      profileLocked={Boolean(claimProfile.profileLocked)}
                      onUpdated={load}
                    />
                  )}
                </div>
              </div>
            </section>

            {/* ── Dominant chat wall ── */}
            <section>
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-sm font-semibold uppercase tracking-widest text-violet-300">Community wall</h2>
                <p className="text-[11px] text-zinc-600">Telegram-style group chat · founders & investors</p>
              </div>
              <ProjectWall slug={slug} ddollarBalance={ddollarBalance} />
            </section>

            {/* ── Collapsible details panel (everything else tucked away) ── */}
            <section className="space-y-3">
              <p className="text-[11px] uppercase tracking-widest text-zinc-600">Details</p>

              {project.listingKind === 'paper_track' &&
                (project.recentPaperBuyers?.length ? (
                  <ProjectRecentBuyersPanel
                    ticker={project.ticker}
                    slug={project.slug}
                    dexscreenerUrl={project.dexscreenerUrl}
                    buyers={project.recentPaperBuyers}
                  />
                ) : (
                  <CollapsibleInfo title="Paper track notice" hint="not a verified listing yet" accent="zinc">
                    <p className="rounded-lg border border-amber-500/30 bg-amber-950/15 px-3 py-2 text-sm text-amber-100">
                      Paper-traded via DexScreener — not a verified Doxxed listing yet.{' '}
                      <Link href="/list-your-project" className="font-medium text-amber-200 underline">
                        Submit a listing
                      </Link>{' '}
                      with founder proof for admin approval.
                    </p>
                  </CollapsibleInfo>
                ))}

              <CollapsibleInfo title="Market & chart" hint="price, liquidity, chart" accent="emerald" defaultOpen={Boolean(project.metrics?.priceUsd != null)}>
                <div className="space-y-4">
                  <ProjectMetricsGrid metrics={project.metrics} />
                  {poolAddress && (
                    <GeckoTerminalChart
                      chainSlug={project.chain.slug}
                      poolAddress={poolAddress}
                      dexscreenerUrl={project.dexscreenerUrl}
                    />
                  )}
                </div>
              </CollapsibleInfo>

              {project.description && (
                <CollapsibleInfo title="About" hint="project description" accent="zinc">
                  <p className="leading-relaxed text-zinc-400">{project.description}</p>
                </CollapsibleInfo>
              )}

              {(project.scoutHighlight || project.listingScoutThesis) && (
                <CollapsibleInfo title="Scout thesis" hint="why this was listed" accent="violet">
                  <p className="rounded-lg border border-violet-500/25 bg-violet-950/15 px-3 py-2 text-sm text-violet-100">
                    {project.scoutHighlight ?? project.listingScoutThesis}
                  </p>
                </CollapsibleInfo>
              )}

              {project.verificationDossier && (
                <CollapsibleInfo title="Verification dossier" hint="founder proof & criteria" accent="emerald">
                  <div className="space-y-3 text-sm">
                    {project.verificationDossier.whyDoxxed && (
                      <div>
                        <p className="text-xs uppercase text-zinc-500">Why doxxed</p>
                        <p className="mt-1 whitespace-pre-wrap text-zinc-300">{project.verificationDossier.whyDoxxed}</p>
                      </div>
                    )}
                    {project.verificationDossier.whyList && (
                      <div>
                        <p className="text-xs uppercase text-zinc-500">Why list</p>
                        <p className="mt-1 whitespace-pre-wrap text-zinc-300">{project.verificationDossier.whyList}</p>
                      </div>
                    )}
                    {project.verificationDossier.companyDetails && (
                      <div>
                        <p className="text-xs uppercase text-zinc-500">Company details</p>
                        <p className="mt-1 whitespace-pre-wrap text-zinc-300">{project.verificationDossier.companyDetails}</p>
                      </div>
                    )}
                    {project.verificationDossier.verificationCriteria &&
                      project.verificationDossier.verificationCriteria.length > 0 && (
                        <div>
                          <p className="text-xs uppercase text-zinc-500">Criteria met</p>
                          <div className="mt-1 flex flex-wrap gap-2">
                            {project.verificationDossier.verificationCriteria.map((c) => (
                              <span key={c} className="rounded bg-emerald-950/40 px-2 py-0.5 text-xs text-emerald-200">
                                {c}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    <div className="flex flex-wrap gap-3 pt-1 text-sm">
                      {project.verificationDossier.founderTwitter && (
                        <a href={project.verificationDossier.founderTwitter.startsWith('http') ? project.verificationDossier.founderTwitter : `https://x.com/${project.verificationDossier.founderTwitter.replace(/^@/, '')}`} target="_blank" rel="noopener noreferrer" className="text-sky-300 hover:text-white">Founder X</a>
                      )}
                      {project.verificationDossier.founderLinkedIn && (
                        <a href={project.verificationDossier.founderLinkedIn} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">LinkedIn</a>
                      )}
                      {project.verificationDossier.founderGithub && (
                        <a href={project.verificationDossier.founderGithub} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">GitHub</a>
                      )}
                      {project.verificationDossier.founderVideoUrl && (
                        <a href={project.verificationDossier.founderVideoUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">Founder video</a>
                      )}
                      {project.verificationDossier.founderInterviewUrl && (
                        <a href={project.verificationDossier.founderInterviewUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">Interview</a>
                      )}
                      {project.verificationDossier.auditUrl && (
                        <a href={project.verificationDossier.auditUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">Audit</a>
                      )}
                    </div>
                  </div>
                </CollapsibleInfo>
              )}

              {project.founder && (
                <CollapsibleInfo title="Public founder" hint={project.founder.name} accent="violet">
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-sm font-bold text-violet-300">
                      {project.founder.photoUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={project.founder.photoUrl} alt="" className="h-12 w-12 rounded-full object-cover" />
                      ) : (
                        project.founder.name.slice(0, 1)
                      )}
                    </div>
                    <div className="flex-1">
                      <Link href={`/founder/${project.founder.slug}`} className="text-base font-semibold hover:text-violet-300">
                        {project.founder.name}
                      </Link>
                      <FounderBadges verifications={project.founder.verifications} />
                      <div className="mt-2 flex flex-wrap gap-3 text-xs">
                        {project.founder.linkedInUrl && <a href={project.founder.linkedInUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">LinkedIn</a>}
                        {project.founder.twitterUrl && <a href={project.founder.twitterUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">X / Twitter</a>}
                        {project.founder.githubUrl && <a href={project.founder.githubUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">GitHub</a>}
                      </div>
                    </div>
                  </div>
                </CollapsibleInfo>
              )}

              {(project.socials?.twitterUrl || project.socials?.discordUrl || project.socials?.githubUrl || project.socials?.telegramUrl) && (
                <CollapsibleInfo title="Community links" hint="twitter, discord, telegram" accent="zinc">
                  <div className="flex flex-wrap gap-3 text-sm">
                    {project.socials.twitterUrl && <a href={project.socials.twitterUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">Twitter</a>}
                    {project.socials.discordUrl && <a href={project.socials.discordUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">Discord</a>}
                    {project.socials.telegramUrl && <a href={project.socials.telegramUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">Telegram</a>}
                    {project.socials.githubUrl && <a href={project.socials.githubUrl} target="_blank" rel="noopener noreferrer" className="text-violet-300 hover:text-white">GitHub</a>}
                  </div>
                </CollapsibleInfo>
              )}

              {project.contractAddress && (
                <CollapsibleInfo title="Contract" hint="token address" accent="zinc">
                  <code className="break-all rounded bg-zinc-900 px-2 py-1 text-xs text-zinc-300">
                    {project.contractAddress}
                  </code>
                </CollapsibleInfo>
              )}

              <CollapsibleInfo title="Project room" hint="lifecycle, genome, raise room, build log" accent="emerald">
                <p className="mb-3 text-xs text-zinc-500">
                  {project.listingKind === 'paper_track'
                    ? 'Live market data and paper trades — founder tools unlock after verified listing.'
                    : 'Ship logs and roadmap for this project. Money Feed shows trades & conviction only — build noise stays in Founder OS.'}
                </p>
                <ProjectRoomPanel slug={slug} />
              </CollapsibleInfo>

              <CollapsibleInfo title="Founder Brain" hint="ask the AI about this project" accent="violet">
                <FounderBrainPanel slug={slug} projectName={project.name} />
              </CollapsibleInfo>

              <Link
                href={`/list-your-project?edit=${encodeURIComponent(project.slug)}`}
                className="inline-block rounded-lg border border-violet-500/40 px-4 py-2 text-sm text-violet-200 hover:bg-violet-950/30"
              >
                Update listing
              </Link>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
