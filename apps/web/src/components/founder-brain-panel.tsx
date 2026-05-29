'use client';

import { FOUNDER_BRAIN_STARTER_QUESTIONS } from '@dcf/utils';
import { useState } from 'react';
import { askFounderBrain } from '@/lib/api';

type FounderBrainPanelProps = {
  slug: string;
  projectName: string;
};

export function FounderBrainPanel({ slug, projectName }: FounderBrainPanelProps) {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handleAsk(q?: string) {
    const text = (q ?? question).trim();
    if (text.length < 3) return;
    setLoading(true);
    setErr(null);
    try {
      const result = await askFounderBrain(slug, text);
      setAnswer(result.answer);
      if (q) setQuestion(q);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not reach Founder Brain');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-2xl border border-cyan-500/30 bg-cyan-950/10 p-5">
      <h3 className="text-lg font-semibold text-white">Ask The Project</h3>
      <p className="mt-1 text-sm text-zinc-500">
        Founder Brain answers from public build data, Raise Room, and project memory — not hype.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {FOUNDER_BRAIN_STARTER_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => handleAsk(q)}
            disabled={loading}
            className="rounded-full border border-zinc-700 px-3 py-1 text-xs text-zinc-400 hover:border-cyan-500/50 hover:text-cyan-200"
          >
            {q}
          </button>
        ))}
      </div>
      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={`Ask ${projectName} anything…`}
          className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-white"
          onKeyDown={(e) => e.key === 'Enter' && handleAsk()}
        />
        <button
          type="button"
          disabled={loading}
          onClick={() => handleAsk()}
          className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {loading ? 'Thinking…' : 'Ask'}
        </button>
      </div>
      {answer && (
        <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 text-sm text-zinc-300">
          {answer}
        </div>
      )}
      {err && <p className="mt-2 text-sm text-red-300">{err}</p>}
    </div>
  );
}
