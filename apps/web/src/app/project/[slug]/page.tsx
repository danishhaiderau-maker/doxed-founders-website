'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { extractPoolAddressFromDexUrl } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { FounderBadges } from '@/components/founder-badges';
import { GeckoTerminalChart } from '@/components/gecko-terminal-chart';
import { ProjectMetricsGrid } from '@/components/project-card';
import { ProjectRoomPanel } from '@/components/project-room';
import { WatchlistButton } from '@/components/watchlist-button';
import { fetchProject, ProjectDetail } from '@/lib/api';

export default function ProjectDetailPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug;
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchProject(slug);
      setProject(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Project not found');
      setProject(null);
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    load();
  }, [load]);

  const poolAddress =
    project?.dexscreenerUrl != null
      ? extractPoolAddressFromDexUrl(project.dexscreenerUrl)
      : null;

  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 md:px-6">
          <div>
            <Link href="/projects" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← All projects
            </Link>
            <h1 className="text-xl font-bold">{project?.name ?? 'Project'}</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 md:px-6">
        {loading && <p className="text-[var(--color-muted)]">Loading…</p>}
        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        {project && (
          <div className="space-y-8">
            <section className="flex flex-col gap-6 md:flex-row md:items-start md:justify-between">
              <div className="flex gap-4">
                <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-xl bg-[var(--color-card)] text-lg font-bold text-[var(--color-accent)]">
                  {project.logoUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={project.logoUrl}
                      alt=""
                      className="h-16 w-16 rounded-xl object-cover"
                    />
                  ) : (
                    project.ticker.slice(0, 2)
                  )}
                </div>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-3xl font-bold">{project.name}</h2>
                    <span className="text-lg text-[var(--color-muted)]">{project.ticker}</span>
                    {project.featured && (
                      <span className="rounded bg-amber-950/50 px-2 py-0.5 text-xs font-semibold uppercase text-amber-300">
                        Featured
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-[var(--color-muted)]">
                    {project.category?.name} · {project.chain.name}
                  </p>
                  {project.summary && (
                    <p className="mt-3 max-w-2xl text-[var(--color-muted)]">{project.summary}</p>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                {project.websiteUrl && (
                  <a
                    href={project.websiteUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm hover:border-[var(--color-accent)]"
                  >
                    Website
                  </a>
                )}
                {project.docsUrl && (
                  <a
                    href={project.docsUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm hover:border-[var(--color-accent)]"
                  >
                    Docs
                  </a>
                )}
                {project.dexscreenerUrl && (
                  <a
                    href={project.dexscreenerUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-lg border border-[var(--color-accent)]/50 px-4 py-2 text-sm text-[var(--color-accent)] hover:text-white"
                  >
                    DexScreener
                  </a>
                )}
                <Link
                  href={`/paper-trading?dex=${encodeURIComponent(project.dexscreenerUrl ?? '')}`}
                  className="rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--color-accent-hover)]"
                >
                  Paper trade
                </Link>
                <WatchlistButton slug={project.slug} />
              </div>
            </section>

            <section>
              <h3 className="mb-4 text-sm font-semibold uppercase tracking-widest text-[var(--color-muted)]">
                Market
              </h3>
              <ProjectMetricsGrid metrics={project.metrics} />
            </section>

            {poolAddress && (
              <section>
                <h3 className="mb-4 text-sm font-semibold uppercase tracking-widest text-[var(--color-muted)]">
                  Chart
                </h3>
                <GeckoTerminalChart
                  chainSlug={project.chain.slug}
                  poolAddress={poolAddress}
                  dexscreenerUrl={project.dexscreenerUrl}
                />
              </section>
            )}

            {project.description && (
              <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
                <h3 className="text-sm font-semibold uppercase tracking-widest text-[var(--color-muted)]">
                  About
                </h3>
                <p className="mt-4 leading-relaxed text-[var(--color-muted)]">{project.description}</p>
              </section>
            )}

            {project.verificationDossier && (
              <section className="rounded-xl border border-emerald-500/20 bg-[var(--color-card)] p-6">
                <h3 className="text-sm font-semibold uppercase tracking-widest text-emerald-300">
                  Verification dossier
                </h3>
                <p className="mt-2 text-xs text-[var(--color-muted)]">
                  Information submitted when this project was listed as team doxxed / public identity.
                </p>
                <dl className="mt-4 space-y-3 text-sm">
                  {project.verificationDossier.whyDoxxed && (
                    <div>
                      <dt className="text-xs uppercase text-[var(--color-muted)]">Why doxxed</dt>
                      <dd className="mt-1 whitespace-pre-wrap">{project.verificationDossier.whyDoxxed}</dd>
                    </div>
                  )}
                  {project.verificationDossier.whyList && (
                    <div>
                      <dt className="text-xs uppercase text-[var(--color-muted)]">Why list</dt>
                      <dd className="mt-1 whitespace-pre-wrap">{project.verificationDossier.whyList}</dd>
                    </div>
                  )}
                  {project.verificationDossier.companyDetails && (
                    <div>
                      <dt className="text-xs uppercase text-[var(--color-muted)]">Company details</dt>
                      <dd className="mt-1 whitespace-pre-wrap">{project.verificationDossier.companyDetails}</dd>
                    </div>
                  )}
                  {project.verificationDossier.verificationCriteria &&
                    project.verificationDossier.verificationCriteria.length > 0 && (
                      <div>
                        <dt className="text-xs uppercase text-[var(--color-muted)]">Criteria met</dt>
                        <dd className="mt-1 flex flex-wrap gap-2">
                          {project.verificationDossier.verificationCriteria.map((c) => (
                            <span
                              key={c}
                              className="rounded bg-emerald-950/40 px-2 py-0.5 text-xs text-emerald-200"
                            >
                              {c}
                            </span>
                          ))}
                        </dd>
                      </div>
                    )}
                </dl>
                <div className="mt-4 flex flex-wrap gap-3 text-sm">
                  {project.verificationDossier.founderTwitter && (
                    <a
                      href={
                        project.verificationDossier.founderTwitter.startsWith('http')
                          ? project.verificationDossier.founderTwitter
                          : `https://x.com/${project.verificationDossier.founderTwitter.replace(/^@/, '')}`
                      }
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sky-300 hover:text-white"
                    >
                      Founder X / Twitter
                    </a>
                  )}
                  {project.verificationDossier.founderLinkedIn && (
                    <a
                      href={project.verificationDossier.founderLinkedIn}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[var(--color-accent)] hover:text-white"
                    >
                      LinkedIn
                    </a>
                  )}
                  {project.verificationDossier.founderGithub && (
                    <a
                      href={project.verificationDossier.founderGithub}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[var(--color-accent)] hover:text-white"
                    >
                      GitHub
                    </a>
                  )}
                  {project.verificationDossier.founderVideoUrl && (
                    <a
                      href={project.verificationDossier.founderVideoUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[var(--color-accent)] hover:text-white"
                    >
                      Founder video
                    </a>
                  )}
                  {project.verificationDossier.founderInterviewUrl && (
                    <a
                      href={project.verificationDossier.founderInterviewUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[var(--color-accent)] hover:text-white"
                    >
                      Interview
                    </a>
                  )}
                  {project.verificationDossier.auditUrl && (
                    <a
                      href={project.verificationDossier.auditUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[var(--color-accent)] hover:text-white"
                    >
                      Audit
                    </a>
                  )}
                </div>
              </section>
            )}

            {project.founder && (
              <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
                <h3 className="text-sm font-semibold uppercase tracking-widest text-[var(--color-muted)]">
                  Public founder
                </h3>
                <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-start">
                  <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-[var(--color-background)] text-lg font-bold text-[var(--color-accent)]">
                    {project.founder.photoUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={project.founder.photoUrl}
                        alt=""
                        className="h-14 w-14 rounded-full object-cover"
                      />
                    ) : (
                      project.founder.name.slice(0, 1)
                    )}
                  </div>
                  <div className="flex-1">
                    <Link
                      href={`/founder/${project.founder.slug}`}
                      className="text-lg font-semibold hover:text-[var(--color-accent)]"
                    >
                      {project.founder.name}
                    </Link>
                    <FounderBadges verifications={project.founder.verifications} />
                    <div className="mt-3 flex flex-wrap gap-3 text-sm">
                      {project.founder.linkedInUrl && (
                        <a
                          href={project.founder.linkedInUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[var(--color-accent)] hover:text-white"
                        >
                          LinkedIn
                        </a>
                      )}
                      {project.founder.twitterUrl && (
                        <a
                          href={project.founder.twitterUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[var(--color-accent)] hover:text-white"
                        >
                          X / Twitter
                        </a>
                      )}
                      {project.founder.githubUrl && (
                        <a
                          href={project.founder.githubUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[var(--color-accent)] hover:text-white"
                        >
                          GitHub
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              </section>
            )}

            {(project.socials?.twitterUrl ||
              project.socials?.discordUrl ||
              project.socials?.githubUrl) && (
              <section>
                <h3 className="mb-3 text-sm font-semibold uppercase tracking-widest text-[var(--color-muted)]">
                  Community
                </h3>
                <div className="flex flex-wrap gap-3">
                  {project.socials.twitterUrl && (
                    <a href={project.socials.twitterUrl} target="_blank" rel="noopener noreferrer" className="text-sm text-[var(--color-accent)]">
                      Twitter
                    </a>
                  )}
                  {project.socials.discordUrl && (
                    <a href={project.socials.discordUrl} target="_blank" rel="noopener noreferrer" className="text-sm text-[var(--color-accent)]">
                      Discord
                    </a>
                  )}
                  {project.socials.telegramUrl && (
                    <a href={project.socials.telegramUrl} target="_blank" rel="noopener noreferrer" className="text-sm text-[var(--color-accent)]">
                      Telegram
                    </a>
                  )}
                  {project.socials.githubUrl && (
                    <a href={project.socials.githubUrl} target="_blank" rel="noopener noreferrer" className="text-sm text-[var(--color-accent)]">
                      GitHub
                    </a>
                  )}
                </div>
              </section>
            )}

            {project.contractAddress && (
              <section className="text-sm text-[var(--color-muted)]">
                <span className="font-medium text-white">Contract</span>{' '}
                <code className="break-all rounded bg-[var(--color-card)] px-2 py-1 text-xs">
                  {project.contractAddress}
                </code>
              </section>
            )}

            <section>
              <h3 className="mb-2 text-sm font-semibold uppercase tracking-widest text-emerald-400/90">
                Project room
              </h3>
              <p className="mb-4 text-sm text-[var(--color-muted)]">
                Videos, build logs, roadmap, demand testing, and simulated raise — all in one place.
              </p>
              <ProjectRoomPanel slug={slug} />
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
