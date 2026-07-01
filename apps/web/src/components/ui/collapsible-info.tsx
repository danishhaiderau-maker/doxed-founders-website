"use client";

import { useState, useId } from "react";

type Accent = "zinc" | "blue" | "violet" | "emerald";

const ACCENT_BORDER: Record<Accent, string> = {
  zinc: "border-zinc-800",
  blue: "border-blue-900/60",
  violet: "border-violet-900/60",
  emerald: "border-emerald-900/60",
};

const ACCENT_CHEVRON: Record<Accent, string> = {
  zinc: "text-zinc-400",
  blue: "text-blue-300",
  violet: "text-violet-300",
  emerald: "text-emerald-300",
};

/**
 * Sleek collapsible info accordion. Renders a single-line header (title + hint
 * + chevron) that expands on click to reveal the children. Collapsed by default
 * so verbose instructional/explainer prose stays tucked away until the reader
 * opts in -- keeps the dashboard vertical space clean.
 */
export function CollapsibleInfo({
  title,
  hint,
  children,
  defaultOpen = false,
  accent = "zinc",
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  accent?: Accent;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();

  return (
    <div
      className={`rounded-lg border ${ACCENT_BORDER[accent]} bg-zinc-950/40 overflow-hidden`}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-zinc-200 hover:bg-zinc-900/60 transition-colors"
      >
        <span className="shrink-0 font-semibold text-zinc-100">{title}</span>
        {hint ? (
          <span className="truncate text-xs text-zinc-500">- {hint}</span>
        ) : null}
        <span className="ml-auto shrink-0">
          <svg
            className={`${ACCENT_CHEVRON[accent]} h-4 w-4 transition-transform duration-150 ${
              open ? "rotate-180" : ""
            }`}
            viewBox="0 0 20 20"
            fill="currentColor"
            aria-hidden="true"
          >
            <path
              fillRule="evenodd"
              d="M5.23 7.21a.75.75 0 011.06.02L10 11.06l3.71-3.83a.75.75 0 111.08 1.04l-4.25 4.39a.75.75 0 01-1.08 0L5.21 8.27a.75.75 0 01.02-1.06z"
              clipRule="evenodd"
            />
          </svg>
        </span>
      </button>
      {open ? (
        <div
          id={panelId}
          className="border-t border-zinc-800/70 px-3 py-3 text-sm text-zinc-300 space-y-2"
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
