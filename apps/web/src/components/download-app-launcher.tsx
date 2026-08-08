'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { Download } from 'lucide-react';
import { cn } from '@dcf/utils';

/**
 * Compact nav control beside Chat — opens a small Android / iOS menu
 * that lands on the unified `/mobile` hub (not legacy APK URLs).
 */
export function DownloadAppLauncher() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('click', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('click', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, []);

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'relative inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-zinc-200 transition hover:bg-emerald-500/10 hover:text-white',
          open && 'bg-emerald-500/10 text-white',
        )}
        title="Download iOS or Android app"
        aria-label="Download app"
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <Download className="h-4 w-4 text-emerald-300" />
        <span className="hidden text-xs font-semibold lg:inline">App</span>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Download app"
          className="absolute left-0 top-full z-[110] mt-1.5 min-w-[11.5rem] overflow-hidden rounded-xl border border-zinc-700/80 bg-zinc-950/95 py-1 shadow-2xl backdrop-blur-md"
        >
          <p className="border-b border-zinc-800/80 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.16em] text-zinc-500">
            Get the app
          </p>
          <Link
            href="/mobile#android"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block px-3 py-2.5 text-sm text-zinc-200 transition hover:bg-emerald-500/10 hover:text-white"
          >
            Android
          </Link>
          <Link
            href="/mobile#ios"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block px-3 py-2.5 text-sm text-zinc-200 transition hover:bg-emerald-500/10 hover:text-white"
          >
            iOS
          </Link>
          <Link
            href="/mobile"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block border-t border-zinc-800/80 px-3 py-2 text-xs text-zinc-500 transition hover:bg-zinc-900 hover:text-zinc-300"
          >
            All platforms →
          </Link>
        </div>
      )}
    </div>
  );
}
