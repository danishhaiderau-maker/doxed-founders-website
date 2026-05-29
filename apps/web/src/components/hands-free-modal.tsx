'use client';

const STORAGE_KEY = 'dcf-hands-free-intro-seen';

const EXAMPLES = [
  'Build a referral system.',
  'Generate this week\'s update.',
  'Create GitHub issues from roadmap.',
  'Publish latest progress everywhere.',
  'Summarize commits for traders.',
  'Create launch readiness report.',
];

export type HandsFreeModalProps = {
  onTry: (example: string) => void;
  onDismiss: () => void;
};

export function shouldShowHandsFreeIntro(): boolean {
  if (typeof window === 'undefined') return false;
  return !localStorage.getItem(STORAGE_KEY);
}

export function markHandsFreeIntroSeen() {
  if (typeof window !== 'undefined') localStorage.setItem(STORAGE_KEY, '1');
}

export function HandsFreeModal({ onTry, onDismiss }: HandsFreeModalProps) {
  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/70 p-4 sm:items-center">
      <div className="w-full max-w-lg rounded-2xl border border-emerald-500/40 bg-zinc-950 p-6 shadow-2xl">
        <p className="text-2xl">🚀</p>
        <h2 className="mt-2 text-xl font-bold text-white">Hands-Free Mode</h2>
        <p className="mt-2 text-sm text-zinc-400">
          Tell Founder OS what you want. It coordinates your connected tools through the event bus —
          tasks, GitHub, community, and publish everywhere.
        </p>
        <ul className="mt-4 space-y-2">
          {EXAMPLES.map((ex) => (
            <li key={ex}>
              <button
                type="button"
                onClick={() => {
                  markHandsFreeIntroSeen();
                  onTry(ex);
                }}
                className="w-full rounded-lg border border-zinc-800 bg-black/40 px-3 py-2 text-left text-sm text-zinc-200 hover:border-emerald-500/40 hover:text-white"
              >
                • {ex}
              </button>
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={() => {
            markHandsFreeIntroSeen();
            onDismiss();
          }}
          className="mt-4 w-full rounded-lg border border-zinc-700 py-2 text-sm text-zinc-500 hover:text-white"
        >
          Got it — I&apos;ll type my own
        </button>
      </div>
    </div>
  );
}
