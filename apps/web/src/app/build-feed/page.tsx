'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { fetchBuildFeed, FounderBuildPost } from '@/lib/api';
import { FounderPresenceBadge } from '@/components/founder-presence';

export default function BuildFeedPage() {
  const [posts, setPosts] = useState<FounderBuildPost[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPosts(await fetchBuildFeed(50));
    } catch {
      setPosts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-xl font-semibold">Build in public</h1>
            <p className="text-xs text-zinc-500">Founder updates · execution proof · no hype</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-3xl space-y-4 px-6 py-8">
        {loading && <p className="text-zinc-500">Loading build feed…</p>}
        {!loading && posts.length === 0 && (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-8 text-center">
            <p className="text-zinc-400">No build posts yet.</p>
            <Link href="/founder-den" className="mt-3 inline-block text-sm text-emerald-400 hover:underline">
              Open Founder Den to post your first update →
            </Link>
          </div>
        )}
        {posts.map((post) => (
          <article
            key={post.id}
            className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-5"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/founder/${post.founder.slug}`} className="font-semibold text-white hover:text-emerald-400">
                {post.founder.name}
              </Link>
              <FounderPresenceBadge level={post.founder.presenceLevel} compact />
              {post.dayNumber != null && (
                <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-zinc-400">
                  Day {post.dayNumber}
                </span>
              )}
            </div>
            {post.project && (
              <Link
                href={`/project/${post.project.slug}`}
                className="mt-1 inline-block text-xs text-zinc-500 hover:text-emerald-400"
              >
                {post.project.name} ({post.project.ticker})
              </Link>
            )}
            <h2 className="mt-3 font-medium text-white">{post.headline}</h2>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-zinc-400">{post.body}</p>
            {post.githubUrl && (
              <a
                href={post.githubUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-block text-xs text-emerald-400 hover:underline"
              >
                View on GitHub →
              </a>
            )}
            <p className="mt-3 text-xs text-zinc-600">
              {new Date(post.publishedAt).toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
              })}
            </p>
          </article>
        ))}
      </div>
    </main>
  );
}
