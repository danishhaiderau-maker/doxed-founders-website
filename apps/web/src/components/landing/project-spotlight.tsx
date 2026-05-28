'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { contributorLevelLabel } from '@dcf/utils';
import { SpotlightProject } from '@/lib/api';

const ROTATE_MS = 150_000; // 2.5 minutes

function embedVideoUrl(url?: string | null) {
  if (!url) return null;
  const yt = url.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]+)/);
  if (yt) return `https://www.youtube.com/embed/${yt[1]}?autoplay=0&mute=1`;
  return null;
}

function twitterUrl(project: SpotlightProject) {
  return (
    project.founder?.twitterUrl ??
    project.socials?.twitterUrl ??
    null
  );
}

export function ProjectSpotlight({ projects }: { projects: SpotlightProject[] }) {
  const [index, setIndex] = useState(0);

  const items = useMemo(() => projects.filter(Boolean), [projects]);

  useEffect(() => {
    if (items.length <= 1) return;
    const timer = setInterval(() => {
      setIndex((i) => (i + 1) % items.length);
    }, ROTATE_MS);
    return () => clearInterval(timer);
  }, [items.length]);

  if (items.length === 0) {
    return (
      <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-card)]/80 p-8 text-center text-sm text-[var(--color-muted)]">
        Verified projects will rotate here as listings go live.
      </div>
    );
  }

  const project = items[index];
  const videoEmbed = embedVideoUrl(project.founder?.videoUrl);
  const xLink = twitterUrl(project);
  const headline = (
    project.summary ??
    `${project.name} — DOXXED FOUNDER PROJECT ON ${project.chain?.slug ?? 'CHAIN'}`
  ).toUpperCase();

  return (
    <div className="relative overflow-hidden rounded-2xl border border-emerald-500/30 bg-gradient-to-br from-[#0a0f0a] via-[var(--color-card)] to-[#0d1117] p-6 shadow-[0_0_60px_rgba(16,185,129,0.08)] md:p-8">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-400">
          Live spotlight · rotates every 2–3 min
        </p>
        <div className="flex gap-1">
          {items.map((_, i) => (
            <button
              key={i}
              type="button"
              aria-label={`Show project ${i + 1}`}
              onClick={() => setIndex(i)}
              className={`h-2 w-2 rounded-full ${i === index ? 'bg-emerald-400' : 'bg-[var(--color-border)]'}`}
            />
          ))}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <div className="flex items-center gap-3">
            {project.logoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={project.logoUrl} alt="" className="h-12 w-12 rounded-full" />
            )}
            <div>
              <h3 className="text-xl font-bold">
                {project.name}{' '}
                <span className="text-[var(--color-muted)]">({project.ticker})</span>
              </h3>
              {project.founder && (
                <p className="text-sm text-emerald-300/90">Founder: {project.founder.name}</p>
              )}
            </div>
          </div>

          <p className="mt-4 text-sm font-semibold leading-relaxed tracking-wide text-white">
            {headline.slice(0, 280)}
            {headline.length > 280 ? '…' : ''}
          </p>

          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              href={`/project/${project.slug}`}
              className="rounded-lg bg-emerald-500/90 px-4 py-2 text-sm font-medium text-black hover:bg-emerald-400"
            >
              View project
            </Link>
            {xLink && (
              <a
                href={xLink}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm text-white hover:border-emerald-400"
              >
                X profile →
              </a>
            )}
            {project.founder?.videoUrl && !videoEmbed && (
              <a
                href={project.founder.videoUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm text-white hover:border-emerald-400"
              >
                Watch video →
              </a>
            )}
          </div>
        </div>

        <div className="min-h-[200px] overflow-hidden rounded-xl border border-[var(--color-border)] bg-black/40">
          {videoEmbed ? (
            <iframe
              title={`${project.name} founder video`}
              src={videoEmbed}
              className="aspect-video h-full w-full"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            />
          ) : (
            <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-2 p-6 text-center text-sm text-[var(--color-muted)]">
              {project.logoUrl && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={project.logoUrl} alt="" className="h-20 w-20 rounded-full opacity-80" />
              )}
              <p>FOUNDER VIDEO & X UPDATES APPEAR HERE FOR VERIFIED PROJECTS</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function ReputationBadge({
  points,
  level,
}: {
  points: number;
  level: number;
}) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-amber-500/40 bg-amber-950/30 px-3 py-1 text-xs font-medium text-amber-200">
      <span className="text-amber-400">★</span>
      {contributorLevelLabel(level)} · {points.toLocaleString()} pts
    </span>
  );
}
