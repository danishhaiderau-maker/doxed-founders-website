'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { SiteNav } from '@/components/site-nav';
import { createTownHallPost, fetchTownHallPosts, TownHallPost } from '@/lib/api';

const CATEGORY_LABELS: Record<string, string> = {
  PLATFORM_UPDATE: 'Platform update',
  RULE_CHANGE: 'Rule change',
  SCAM_WARNING: 'Scam warning',
  DELISTING: 'Delisting notice',
  FEATURE_RELEASE: 'Feature release',
  WEEKLY_RECAP: 'Weekly recap',
  ANNOUNCEMENT: 'Announcement',
};

export default function TownHallPage() {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'ADMIN';
  const [posts, setPosts] = useState<TownHallPost[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ title: '', body: '', category: 'ANNOUNCEMENT', pinned: false });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const rows = await fetchTownHallPosts();
    setPosts(rows);
  }, []);

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'));
  }, [load]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!session?.accessToken || !isAdmin) return;
    setBusy(true);
    setError(null);
    try {
      await createTownHallPost(form, session.accessToken);
      setForm({ title: '', body: '', category: 'ANNOUNCEMENT', pinned: false });
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Publish failed');
    } finally {
      setBusy(false);
    }
  }

  const pinned = posts.filter((p) => p.pinned);
  const rest = posts.filter((p) => !p.pinned);

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-zinc-500 hover:text-white">
              ← Home
            </Link>
            <h1 className="mt-1 text-2xl font-bold">Town Hall</h1>
            <p className="text-sm text-zinc-400">
              Official platform communication — updates, rules, scam warnings, and community announcements.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-8">
        {isAdmin && (
          <div className="mb-6">
            {!showForm ? (
              <button
                type="button"
                onClick={() => setShowForm(true)}
                className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold hover:bg-violet-500"
              >
                New announcement
              </button>
            ) : (
              <form onSubmit={handleSubmit} className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-4 space-y-3">
                <input
                  className="w-full rounded-lg border border-zinc-700 bg-black/40 px-3 py-2 text-sm"
                  placeholder="Title"
                  value={form.title}
                  onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                  required
                />
                <select
                  className="w-full rounded-lg border border-zinc-700 bg-black/40 px-3 py-2 text-sm"
                  value={form.category}
                  onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
                >
                  {Object.entries(CATEGORY_LABELS).map(([k, label]) => (
                    <option key={k} value={k}>
                      {label}
                    </option>
                  ))}
                </select>
                <textarea
                  className="min-h-[120px] w-full rounded-lg border border-zinc-700 bg-black/40 px-3 py-2 text-sm"
                  placeholder="Message from Doxxed team…"
                  value={form.body}
                  onChange={(e) => setForm((f) => ({ ...f, body: e.target.value }))}
                  required
                />
                <label className="flex items-center gap-2 text-sm text-zinc-400">
                  <input
                    type="checkbox"
                    checked={form.pinned}
                    onChange={(e) => setForm((f) => ({ ...f, pinned: e.target.checked }))}
                  />
                  Pin to top
                </label>
                <div className="flex gap-2">
                  <button
                    type="submit"
                    disabled={busy}
                    className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold disabled:opacity-50"
                  >
                    Publish
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowForm(false)}
                    className="rounded-lg border border-zinc-700 px-4 py-2 text-sm"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </div>
        )}

        {error && <p className="mb-4 text-sm text-red-300">{error}</p>}

        {pinned.length > 0 && (
          <section className="mb-8 space-y-3">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-amber-400">Pinned</h2>
            {pinned.map((post) => (
              <PostCard key={post.id} post={post} />
            ))}
          </section>
        )}

        <section className="space-y-3">
          {rest.length === 0 && pinned.length === 0 ? (
            <p className="text-zinc-500">No announcements yet.</p>
          ) : (
            rest.map((post) => <PostCard key={post.id} post={post} />)
          )}
        </section>
      </div>
    </main>
  );
}

function PostCard({ post }: { post: TownHallPost }) {
  return (
    <article className="rounded-2xl border border-zinc-800 bg-zinc-950/40 p-4">
      <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wider text-zinc-500">
        <span className="rounded-full border border-zinc-700 px-2 py-0.5">
          {CATEGORY_LABELS[post.category] ?? post.category}
        </span>
        <span>{new Date(post.publishedAt).toLocaleDateString()}</span>
        {post.author?.name && <span>· {post.author.name}</span>}
      </div>
      <h3 className="mt-2 text-lg font-bold">{post.title}</h3>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-zinc-300">{post.body}</p>
    </article>
  );
}
